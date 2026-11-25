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
MAX_PAYLOAD = 1024 * 1024
LABEL_SETTLE_SECONDS = 0.25


class EventError(Exception):
    pass


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
        woodpecker = config["woodpecker"]
        return cls(root, label, repositories, private_secret(forgejo["webhook_secret_file"]),
                   Forgejo(forgejo), Woodpecker(woodpecker))


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
            url.username or url.password or url.query or url.fragment
        ):
            raise EventError("Use a credential-free HTTPS service URL or a loopback HTTP URL.")
        self.authorization = authorization + private_secret(config["token_file"]).decode("ascii")
        if any(ord(character) < 32 for character in self.authorization):
            raise EventError("An API token contains control characters.")
        self.opener = build_opener(NoRedirect())

    def request(self, path, data=None):
        headers = {"Accept": "application/json", "Authorization": self.authorization}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        try:
            with self.opener.open(Request(self.url + path, body, headers), timeout=5) as response:
                return json.load(response)
        except HTTPError as response:
            response.close()
            raise EventError(f"Service returned HTTP {response.code}.") from None

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
            "variables": {
                "FACTORY_RUN_ID": run.run_id, "FACTORY_REPO": run.repo,
                "FACTORY_ISSUE": run.issue, "FACTORY_COMMIT": run.commit,
            },
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
                db.execute("INSERT INTO issue_labels VALUES (?, ?, ?)",
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


def ingest(settings, delivery, digest, repo, number, event_type):
    with ledger(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT digest FROM deliveries WHERE delivery = ?", (delivery,)).fetchone()
        if prior is not None:
            return 202 if prior["digest"] == digest else 409
        applied = label_applied(settings.forgejo.labels(repo, number), settings.label)
        prior = db.execute("SELECT applied FROM issue_labels WHERE repo = ? AND issue = ?",
                           (repo, number)).fetchone()
        db.execute("INSERT INTO deliveries (delivery, digest, repo, issue, event_type) VALUES (?, ?, ?, ?, ?)",
                   (delivery, digest, repo, number, event_type))
        if applied and not (prior and prior["applied"]):
            commit = settings.forgejo.commit(repo, settings.repositories[repo])
            db.execute("INSERT INTO runs (run_id, repo, issue, commit_sha, delivery) VALUES (?, ?, ?, ?, ?)",
                       (f"forgejo-{uuid4().hex}", repo, f"{repo}#{number}", commit, delivery))
        db.execute("INSERT INTO issue_labels VALUES (?, ?, ?) ON CONFLICT (repo, issue) DO UPDATE "
                   "SET applied = excluded.applied", (repo, number, applied))
        return 202


UPSTREAM_ERRORS = (URLError, HTTPException, OSError, ValueError, KeyError, TypeError,
                   AttributeError, EventError)
SERVICE_ERRORS = (*UPSTREAM_ERRORS, sqlite3.Error)
EVENTS = {("issues", "opened"), ("issue_label", "label_updated"), ("issue_label", "label_cleared")}


def webhook_server(settings, host, port):
    with ledger(settings) as db:
        check_baseline(settings, db)

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
                payload = json.loads(body)
                event_type = self.headers.get("X-Forgejo-Event-Type")
                action = payload["action"]
                if self.headers.get("X-Forgejo-Event") != "issues" or (event_type, action) not in EVENTS:
                    return 422
                repo = payload["repository"]["full_name"]
                if repo not in settings.repositories:
                    return 403
                issue = payload["issue"]
                if issue.get("pull_request") is not None:
                    return 422
                number = issue_number(issue)
            except (ValueError, KeyError, TypeError, AttributeError, EventError):
                return 400
            if event_type == "issue_label":
                time.sleep(LABEL_SETTLE_SECONDS)
            try:
                return ingest(settings, delivery, hashlib.sha256(body).hexdigest(), repo, number, event_type)
            except SERVICE_ERRORS:
                print(f"Unable to commit Forgejo delivery {delivery}.", file=sys.stderr)
                return 503

    return ThreadingHTTPServer((host, port), Webhook)


def dispatch(settings, retry_id=None):
    with (settings.state_dir / "dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with ledger(settings) as db:
            check_baseline(settings, db)
            if retry_id is None:
                candidates = db.execute(
                    "SELECT * FROM runs WHERE dispatch != 'submitted' ORDER BY rowid"
                ).fetchall()
            else:
                row = db.execute("SELECT * FROM runs WHERE run_id = ?", (retry_id,)).fetchone()
                if row is None:
                    raise EventError("Unknown logical run ID.")
                candidates = [] if row["dispatch"] == "submitted" else [row]
        for row in candidates:
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
