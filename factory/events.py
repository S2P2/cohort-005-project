import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4


CONTROL_REPO = "factory-control/launcher"
CONTROL_BRANCH = "main"
DAG_NAME = "factory-trial"
MAX_PAYLOAD = 1024 * 1024
LABEL_SETTLE_SECONDS = 0.25


class EventError(Exception):
    pass


class APIError(EventError):
    def __init__(self, status):
        super().__init__(f"Service returned HTTP {status}.")
        self.status = status


def private_secret(filename):
    path = Path(filename)
    details = path.lstat()
    if (not path.is_absolute() or not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid() or details.st_mode & 0o077):
        raise EventError("Use an absolute path to a private service-owned secret file.")
    secret = path.read_bytes().rstrip(b"\r\n")
    if not secret:
        raise EventError("Secret file is empty.")
    return secret


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    label: str
    repositories: dict[str, str]
    secret: bytes
    forgejo: "Forgejo"
    orchestrator: "Orchestrator"

    @classmethod
    def load(cls, path):
        config = json.loads(path.read_text())
        root = Path(config["state_dir"])
        details = root.lstat()
        if (not root.is_absolute() or not stat.S_ISDIR(details.st_mode)
                or details.st_uid != os.geteuid() or details.st_mode & 0o077):
            raise EventError("Use an absolute path to a private service-owned state directory.")
        trial_path = Path(config["trial_config"])
        allowed = json.loads(trial_path.read_text())["repositories"]
        repositories = config["repositories"]
        if not repositories or any(
            repo not in allowed or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or any(part in {".", ".."} for part in repo.split("/"))
            or not isinstance(branch, str) or not branch
            for repo, branch in repositories.items()
        ):
            raise EventError("Configure a target branch for each selected launcher repository.")
        label = config["label"]
        if not isinstance(label, str) or not label:
            raise EventError("Configure a trigger label.")
        forgejo = config["forgejo"]
        if ("woodpecker" in config) == ("dagu" in config):
            raise EventError("Configure exactly one orchestrator.")
        orchestrator = Dagu(config["dagu"]) if "dagu" in config else Woodpecker(config["woodpecker"])
        return cls(root, label, repositories, private_secret(forgejo["webhook_secret_file"]),
                   Forgejo(forgejo), orchestrator)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class API:
    def __init__(self, config, authorization):
        self.url = config["url"].rstrip("/")
        url = urlsplit(self.url)
        secure = url.scheme == "https"
        loopback = url.scheme == "http" and url.hostname in {"127.0.0.1", "::1"}
        if not (secure or loopback) or not url.hostname or (
            url.username is not None or url.query or url.fragment
        ):
            raise EventError("Use a credential-free HTTPS service URL or a loopback HTTP URL.")
        self.authorization = authorization + private_secret(config["token_file"]).decode("ascii")
        if any(ord(character) < 32 for character in self.authorization):
            raise EventError("An API token contains control characters.")
        self.opener = build_opener(NoRedirect())

    def request(self, path, data=None, *, expect_json=True):
        headers = {"Accept": "application/json", "Authorization": self.authorization}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        try:
            with self.opener.open(Request(self.url + path, body, headers), timeout=5) as response:
                if expect_json:
                    return json.load(response)
                return None
        except HTTPError as response:
            response.close()
            raise APIError(response.code) from None

    def pages(self, path, **query):
        for page in range(1, 10001):
            items = self.request(f"{path}?{urlencode({**query, 'page': page})}")
            if not isinstance(items, list):
                raise EventError("Expected a paginated collection.")
            if not items:
                return
            yield from items
        raise EventError("Service pagination exceeded the page limit.")


class Forgejo:
    def __init__(self, config):
        self.api = API(config, "token ")

    def issues(self, repo):
        return self.api.pages(f"/api/v1/repos/{repo}/issues", limit=30,
                              state="all", type="issues")

    def labels(self, repo, number):
        return self.api.request(f"/api/v1/repos/{repo}/issues/{number}/labels")

    def commit(self, repo, branch):
        value = self.api.request(f"/api/v1/repos/{repo}/branches/{quote(branch, safe='')}")
        commit = value["commit"]["id"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise EventError("Forgejo did not return a full commit SHA.")
        return commit


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    repo: str
    issue: str
    commit: str

    def parameters(self):
        return {
            "FACTORY_RUN_ID": self.run_id, "FACTORY_REPO": self.repo,
            "FACTORY_ISSUE": self.issue, "FACTORY_COMMIT": self.commit,
        }


class Orchestrator(Protocol):
    def submit(self, run: RunRequest) -> str: ...
    def find(self, run_id: str) -> str | None: ...


class Woodpecker:
    def __init__(self, config):
        self.api = API(config, "Bearer ")
        repo_id = config["repo_id"]
        if type(repo_id) is not int or repo_id < 1:
            raise EventError("Configure the numeric Woodpecker control repository ID.")
        self.path = f"/api/repos/{repo_id}/pipelines"

    @staticmethod
    def job(pipeline):
        number = pipeline["number"]
        if type(number) is not int or number < 1:
            raise EventError("Woodpecker did not return a valid pipeline number.")
        return f"woodpecker:{CONTROL_REPO}:{number}"

    def submit(self, run):
        pipeline = self.api.request(self.path, {
            "branch": CONTROL_BRANCH,
            "message": run.run_id,
            "variables": run.parameters(),
        })
        return self.job(pipeline)

    def find(self, run_id):
        expected = f"MANUAL: {run_id} @ {CONTROL_BRANCH}"
        jobs = [self.job(pipeline) for pipeline in self.api.pages(
            self.path, perPage=30, branch=CONTROL_BRANCH, event="manual"
        ) if (pipeline.get("message") == expected and pipeline.get("event") == "manual"
              and pipeline.get("branch") == CONTROL_BRANCH)]
        if len(jobs) > 1:
            raise EventError("More than one Woodpecker pipeline matches the logical run.")
        return jobs[0] if jobs else None


class Dagu:
    def __init__(self, config):
        self.api = API(config, "Bearer ")
        self.path = f"/api/v1/dags/{DAG_NAME}"

    @staticmethod
    def job(run_id):
        return f"dagu:{DAG_NAME}:{run_id}"

    def submit(self, run):
        try:
            result = self.api.request(f"{self.path}/enqueue", {
                "dagRunId": run.run_id,
                "params": json.dumps(run.parameters()),
            })
        except APIError as error:
            if error.status == 500 and (job := self.find(run.run_id)) is not None:
                return job
            raise
        if result["dagRunId"] != run.run_id:
            raise EventError("Dagu returned an unexpected run ID.")
        return self.job(run.run_id)

    def details(self, run_id):
        try:
            details = self.api.request(f"{self.path}/dag-runs/{quote(run_id, safe='')}")["dagRun"]
        except APIError as error:
            if error.status == 404:
                return None
            raise
        if details["dagRunId"] != run_id:
            raise EventError("Dagu returned an unexpected run ID.")
        return details

    def find(self, run_id):
        return self.job(run_id) if self.details(run_id) is not None else None

    def cancel(self, run_id, details):
        terminal = {"aborted", "failed", "partially_succeeded", "rejected", "succeeded"}
        if details is None or details["statusLabel"] in terminal:
            return True
        status = details["statusLabel"]
        path = f"/api/v1/dag-runs/{DAG_NAME}/{quote(run_id, safe='')}"
        try:
            if status == "queued":
                self.api.request(f"{path}/dequeue", expect_json=False)
            elif status in {"running", "waiting"}:
                self.api.request(f"{path}/stop", {}, expect_json=False)
            elif status != "not_started":
                raise EventError("Dagu returned an unexpected run status.")
        except APIError as error:
            if error.status not in {404, 409}:
                raise
        details = self.details(run_id)
        return details is None or details["statusLabel"] in terminal


@contextmanager
def ledger(settings):
    path = settings.state_dir / "events.sqlite3"
    with closing(sqlite3.connect(path, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA synchronous = FULL")
        with db:
            yield db


def seed(settings):
    with ledger(settings) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS baseline (scope TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS issue_labels (
                repo TEXT NOT NULL, issue INTEGER NOT NULL, applied INTEGER NOT NULL,
                opening_covered INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (repo, issue)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery TEXT PRIMARY KEY NOT NULL, digest TEXT NOT NULL,
                repo TEXT NOT NULL, issue INTEGER NOT NULL, event_type TEXT NOT NULL,
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY NOT NULL, repo TEXT NOT NULL, issue TEXT NOT NULL,
                commit_sha TEXT NOT NULL, delivery TEXT NOT NULL UNIQUE REFERENCES deliveries,
                dispatch TEXT NOT NULL DEFAULT 'queued', job TEXT
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT scope FROM baseline").fetchone():
            raise EventError("This ledger already has a cutover baseline.")
        for repo in sorted(settings.repositories):
            for issue in settings.forgejo.issues(repo):
                number = issue_number(issue)
                db.execute("INSERT INTO issue_labels (repo, issue, applied) VALUES (?, ?, ?)",
                           (repo, number, label_applied(issue["labels"], settings.label)))
        db.execute("INSERT INTO baseline VALUES (?)", (scope(settings),))


def scope(settings):
    return json.dumps([settings.forgejo.api.url, settings.label, settings.repositories],
                      sort_keys=True)


def check_baseline(settings, db):
    row = db.execute("SELECT scope FROM baseline").fetchone()
    if row is None or row["scope"] != scope(settings):
        raise EventError("Expected a completed cutover baseline for this scope.")


def issue_number(issue):
    number = issue["number"]
    if type(number) is not int or not 0 < number < 2**63:
        raise EventError("Invalid issue number.")
    return number


def label_applied(labels, label):
    if labels is None:
        return False
    if not isinstance(labels, list):
        raise EventError("Invalid Forgejo label set.")
    return label in {item["name"] for item in labels}


def ingest(settings, delivery, digest, event, event_type):
    repo, number = event.repo, event.number
    with ledger(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT digest FROM deliveries WHERE delivery = ?", (delivery,)).fetchone()
        if prior is not None:
            return 202 if prior["digest"] == digest else 409
        prior = db.execute("SELECT applied, opening_covered FROM issue_labels WHERE repo = ? AND issue = ?",
                           (repo, number)).fetchone()
        db.execute("INSERT INTO deliveries (delivery, digest, repo, issue, event_type) VALUES (?, ?, ?, ?, ?)",
                   (delivery, digest, repo, number, event_type))
        if event.action == "opened" and prior is not None:
            trigger = event.opened_with_trigger and not prior["opening_covered"]
            db.execute("UPDATE issue_labels SET opening_covered = 1 WHERE repo = ? AND issue = ?",
                       (repo, number))
        else:
            applied = label_applied(settings.forgejo.labels(repo, number), settings.label)
            trigger = event.opened_with_trigger or (applied and not (prior and prior["applied"]))
            db.execute(
                "INSERT INTO issue_labels (repo, issue, applied, opening_covered) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (repo, issue) DO UPDATE SET applied = excluded.applied",
                (repo, number, applied, event.action == "opened" or applied),
            )
        if trigger:
            commit = settings.forgejo.commit(repo, settings.repositories[repo])
            db.execute("INSERT INTO runs (run_id, repo, issue, commit_sha, delivery) VALUES (?, ?, ?, ?, ?)",
                       (f"forgejo-{uuid4().hex}", repo, f"{repo}#{number}", commit, delivery))
        return 202


UPSTREAM_ERRORS = (URLError, HTTPException, OSError, ValueError, KeyError, TypeError,
                   AttributeError, EventError)
SERVICE_ERRORS = (*UPSTREAM_ERRORS, sqlite3.Error)
EVENTS = {("issues", "opened"), ("issue_label", "label_updated"), ("issue_label", "label_cleared")}


@dataclass(frozen=True)
class IssueEvent:
    repo: str
    number: int
    action: str
    pull_request: bool
    opened_with_trigger: bool

    @classmethod
    def parse(cls, body, trigger_label):
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise EventError("Expected a Forgejo issue event.")
        repo, issue, action = (payload.get(key) for key in ("repository", "issue", "action"))
        if not isinstance(repo, dict) or not isinstance(issue, dict) or not isinstance(action, str):
            raise EventError("Expected a repository, issue and action.")
        name = repo.get("full_name")
        if not isinstance(name, str) or not name:
            raise EventError("Expected a repository full name.")
        return cls(name, issue_number(issue), action, issue.get("pull_request") is not None,
                   action == "opened" and label_applied(issue.get("labels"), trigger_label))


def webhook_server(settings, host, port):
    with ledger(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        check_baseline(settings, db)
        if not any(column["name"] == "opening_covered"
                   for column in db.execute("PRAGMA table_info(issue_labels)")):
            db.execute("ALTER TABLE issue_labels ADD COLUMN opening_covered INTEGER NOT NULL DEFAULT 1")

    class Webhook(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(15)
            super().setup()

        def log_message(self, *args):
            pass

        def do_POST(self):
            status = self.accept_event()
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def accept_event(self):
            if self.path != "/forgejo":
                return 404
            length = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,10}", length) or self.headers.get("Transfer-Encoding"):
                return 400
            if int(length) > MAX_PAYLOAD:
                return 413
            try:
                body = self.rfile.read(int(length))
            except TimeoutError:
                return 408
            if len(body) != int(length):
                return 400
            signature = self.headers.get("X-Forgejo-Signature", "")
            expected = hmac.new(settings.secret, body, hashlib.sha256).hexdigest()
            if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, expected):
                return 401
            try:
                delivery = str(UUID(self.headers.get("X-Forgejo-Delivery", "")))
                event = IssueEvent.parse(body, settings.label)
            except (ValueError, KeyError, TypeError, EventError):
                return 400
            event_type = self.headers.get("X-Forgejo-Event-Type")
            if (self.headers.get("X-Forgejo-Event") != "issues" or event.pull_request
                    or (event_type, event.action) not in EVENTS):
                return 422
            if event.repo not in settings.repositories:
                return 403
            if event_type == "issue_label":
                time.sleep(LABEL_SETTLE_SECONDS)
            try:
                return ingest(settings, delivery, hashlib.sha256(body).hexdigest(), event, event_type)
            except SERVICE_ERRORS:
                print(f"Unable to commit Forgejo delivery {delivery}.", file=sys.stderr)
                return 503

    return ThreadingHTTPServer((host, port), Webhook)


@contextmanager
def dispatch_lock(settings):
    with (settings.state_dir / "dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def finish_cancellation(settings, run_id, dispatch_state):
    details = settings.orchestrator.details(run_id)
    if dispatch_state == "cancelling_unconfirmed":
        if details is None:
            return False
        with ledger(settings) as db:
            db.execute("UPDATE runs SET dispatch = 'cancelling' WHERE run_id = ?", (run_id,))
    if not settings.orchestrator.cancel(run_id, details):
        raise EventError(f"Dagu cancellation remains pending for {run_id}.")
    with ledger(settings) as db:
        db.execute("UPDATE runs SET dispatch = 'cancelled' WHERE run_id = ?", (run_id,))
    return True


def cancel(settings, run_id):
    if not isinstance(settings.orchestrator, Dagu):
        raise EventError("Use the Woodpecker pipeline controls to cancel a Woodpecker job.")
    with dispatch_lock(settings):
        with ledger(settings) as db:
            check_baseline(settings, db)
            row = db.execute("SELECT dispatch, job FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise EventError("Unknown logical run ID.")
            dispatch_state = row["dispatch"]
            if dispatch_state in {"queued", "cancelled"}:
                db.execute("UPDATE runs SET dispatch = 'cancelled' WHERE run_id = ?", (run_id,))
                return
            if dispatch_state == "submitting":
                dispatch_state = "cancelling_unconfirmed"
            elif dispatch_state == "submitted":
                dispatch_state = "cancelling"
            db.execute("UPDATE runs SET dispatch = ?, job = ? WHERE run_id = ?",
                       (dispatch_state, settings.orchestrator.job(run_id), run_id))
        if not finish_cancellation(settings, run_id, dispatch_state):
            raise EventError(f"Dagu enqueue remains unconfirmed for {run_id}.")


def dispatch(settings, retry_id=None):
    with dispatch_lock(settings):
        with ledger(settings) as db:
            check_baseline(settings, db)
            if retry_id is None:
                candidates = db.execute(
                    "SELECT * FROM runs WHERE dispatch NOT IN ('submitted', 'cancelled') ORDER BY rowid"
                ).fetchall()
            else:
                row = db.execute("SELECT * FROM runs WHERE run_id = ?", (retry_id,)).fetchone()
                if row is None:
                    raise EventError("Unknown logical run ID.")
                candidates = [row] if row["dispatch"] in {"queued", "submitting"} else []
        for row in candidates:
            if row["dispatch"] in {"cancelling", "cancelling_unconfirmed"}:
                try:
                    finish_cancellation(settings, row["run_id"], row["dispatch"])
                except UPSTREAM_ERRORS:
                    print(f"Dagu cancellation remains pending for {row['run_id']}.", file=sys.stderr)
                continue
            run = RunRequest(row["run_id"], row["repo"], row["issue"], row["commit_sha"])
            try:
                job = None
                if row["dispatch"] != "queued" or retry_id is not None:
                    job = settings.orchestrator.find(run.run_id)
                if job is None and (row["dispatch"] == "queued" or retry_id is not None):
                    with ledger(settings) as db:
                        db.execute("UPDATE runs SET dispatch = 'submitting' WHERE run_id = ?", (run.run_id,))
                    job = settings.orchestrator.submit(run)
            except UPSTREAM_ERRORS:
                if retry_id is not None:
                    raise EventError(f"Orchestrator request unconfirmed for {run.run_id}.") from None
                print(f"Orchestrator request unconfirmed for {run.run_id}.", file=sys.stderr)
                continue
            if job is not None:
                with ledger(settings) as db:
                    db.execute("UPDATE runs SET dispatch = 'submitted', job = ? WHERE run_id = ?",
                               (job, run.run_id))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed")
    commands.add_parser("status")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8765, type=int)
    worker = commands.add_parser("dispatch")
    worker.add_argument("--once", action="store_true")
    retry = commands.add_parser("retry")
    retry.add_argument("--run-id", required=True)
    cancellation = commands.add_parser("cancel")
    cancellation.add_argument("--run-id", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        settings = Settings.load(args.config)
        if args.command == "seed":
            seed(settings)
        elif args.command == "serve":
            with webhook_server(settings, args.host, args.port) as server:
                server.serve_forever()
        elif args.command == "status":
            with ledger(settings) as db:
                check_baseline(settings, db)
                print(json.dumps([dict(row) for row in db.execute("SELECT * FROM runs ORDER BY rowid")]))
        elif args.command == "retry":
            dispatch(settings, retry_id=args.run_id)
        elif args.command == "cancel":
            cancel(settings, args.run_id)
        else:
            while True:
                dispatch(settings)
                if args.once:
                    break
                time.sleep(2)
    except EventError as error:
        sys.exit(str(error))
    except SERVICE_ERRORS:
        sys.exit("Factory event command failed while reading configuration, state, or service data.")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
