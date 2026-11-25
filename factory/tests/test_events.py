from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from factory.events import Settings, webhook_server
from test_trial import LAUNCHER, configure_trial


CLI = Path(__file__).resolve().parents[1] / "events.py"
REPO = "demo/trial"
SECRET = b"fixture-webhook-secret"
COMMIT = "1234567890abcdef1234567890abcdef12345678"


@contextmanager
def running(server):
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class Services(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        issues = self.server.issues
        page = int(parse_qs(url.query).get("page", ["1"])[0])
        if url.path == f"/api/v1/repos/{REPO}/issues":
            self.send_json(list(issues.values())[2 * (page - 1):2 * page])
        elif url.path.startswith(f"/api/v1/repos/{REPO}/issues/") and url.path.endswith("/labels"):
            number = int(url.path.split("/")[-2])
            pending = self.server.pending_labels.get(number)
            self.send_json(pending[1] if pending and time.monotonic() < pending[0]
                           else issues[number]["labels"])
        elif url.path == f"/api/v1/repos/{REPO}/branches/main":
            self.send_json({"commit": {"id": self.server.commit}})
        elif url.path == "/api/repos/27/pipelines":
            self.send_json(self.server.pipelines[30 * (page - 1):30 * page])
        elif url.path.startswith("/api/v1/dags/factory-trial/dag-runs/"):
            run = self.server.dag_runs.get(url.path.split("/")[-1])
            if run is None:
                self.send_error(404)
            else:
                self.send_json({"dagRun": run})
        elif url.path.startswith("/api/v1/dag-runs/factory-trial/") and url.path.endswith("/dequeue"):
            run_id = url.path.split("/")[-2]
            if self.server.dag_runs[run_id]["statusLabel"] != "queued":
                self.send_error(409)
                return
            del self.server.dag_runs[run_id]
            if not self.server.lose_response:
                self.send_response(200)
                self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/v1/dag-runs/factory-trial/") and self.path.endswith("/stop"):
            run = self.server.dag_runs[self.path.split("/")[-2]]
            if run["statusLabel"] != "running":
                self.send_error(409)
                return
            run["statusLabel"] = "aborted"
            self.send_response(200)
            self.end_headers()
            return
        if self.path not in {"/api/repos/27/pipelines", "/api/v1/dags/factory-trial/enqueue"}:
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.server.reject_next_submission:
            self.server.reject_next_submission = False
            self.send_error(503)
            return
        if self.path.endswith("/enqueue"):
            run_id = body["dagRunId"]
            if run_id in self.server.dag_runs:
                self.send_error(500)
                return
            self.server.dag_runs[run_id] = {
                "dagRunId": run_id, "params": json.loads(body["params"]), "statusLabel": "queued",
            }
            if not self.server.lose_response:
                self.send_json({"dagRunId": run_id})
            return
        pipeline = {
            "number": len(self.server.pipelines) + 1,
            "event": "manual",
            "branch": body["branch"],
            "message": f"MANUAL: {body['message']} @ {body['branch']}",
            "variables": body["variables"],
            "status": "pending",
        }
        self.server.pipelines.append(pipeline)
        if not self.server.lose_response:
            self.send_json(pipeline)


class SignedEventScenarios:
    backend = "woodpecker"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.services = ThreadingHTTPServer(("127.0.0.1", 0), Services)
        self.services.issues = {}
        self.services.pending_labels = {}
        self.services.pipelines = []
        self.services.dag_runs = {}
        self.services.commit = COMMIT
        self.services.lose_response = False
        self.services.reject_next_submission = False
        self.service_url = self.enterContext(running(self.services))
        self.config = self.root / "events.json"
        trial = self.root / "trial.json"
        trial.write_text(json.dumps({"repositories": {REPO: {}}}))
        for filename, contents in (("hook", SECRET), ("forgejo", b"read-token"),
                                   (self.backend, b"submit-token")):
            path = self.root / filename
            path.touch(mode=0o600)
            path.write_bytes(contents)
        self.config.write_text(json.dumps({
            "state_dir": str(self.root),
            "trial_config": str(trial),
            "label": "factory:run",
            "repositories": {REPO: "main"},
            "forgejo": {
                "url": self.service_url, "token_file": str(self.root / "forgejo"),
                "webhook_secret_file": str(self.root / "hook"),
            },
            self.backend: {
                "url": self.service_url, "token_file": str(self.root / self.backend),
                **({"repo_id": 27} if self.backend == "woodpecker" else {}),
            },
        }))

    def job(self, run_id):
        if self.backend == "dagu":
            return f"dagu:factory-trial:{run_id}"
        return "woodpecker:factory-control/launcher:1"

    def submitted_inputs(self):
        if self.backend == "dagu":
            return [run["params"] for run in self.services.dag_runs.values()]
        return [pipeline["variables"] for pipeline in self.services.pipelines]

    def invoke(self, *args):
        return subprocess.run([sys.executable, str(CLI), "--config", str(self.config), *args],
                              text=True, capture_output=True, timeout=10)

    def command(self, *args):
        result = self.invoke(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def seed_and_listen(self):
        self.command("seed")
        server = webhook_server(Settings.load(self.config), "127.0.0.1", 0)
        self.webhook = self.enterContext(running(server)) + "/forgejo"

    def issue(self, number, *labels):
        issue = {"number": number, "labels": [{"name": label} for label in labels]}
        self.services.issues[number] = issue
        return issue

    def send(self, issue, event_type="issue_label", action="label_updated", delivery=None,
             secret=SECRET, repo=REPO):
        body = json.dumps({"action": action, "repository": {"full_name": repo}, "issue": issue},
                          indent=2).encode()
        request = Request(self.webhook, body, {
            "Content-Type": "application/json",
            "X-Forgejo-Event": "issues",
            "X-Forgejo-Event-Type": event_type,
            "X-Forgejo-Delivery": delivery or str(uuid4()),
            "X-Forgejo-Signature": hmac.new(secret, body, hashlib.sha256).hexdigest(),
        })
        try:
            with urlopen(request, timeout=5) as response:
                return response.status
        except HTTPError as response:
            with response:
                return response.code

    def test_signed_labeled_creation_dispatches_the_commit_recorded_before_ack(self):
        origin = self.root / "origin"
        origin.mkdir()

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=origin, text=True).strip()

        def commit(message):
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                "commit", "--allow-empty", "-m", message)
            return git("rev-parse", "HEAD")

        git("init", "--quiet", "--initial-branch=main")
        original = commit("Recorded issue input")
        self.services.commit = original
        self.seed_and_listen()
        issue = self.issue(4, "factory:run")
        self.assertEqual(self.send(issue, "issues", "opened"), 202)
        self.services.commit = commit("Advanced while queued")
        self.command("dispatch", "--once")
        variables, = self.submitted_inputs()
        recorded, = json.loads(self.command("status"))
        run_id = recorded["run_id"]
        self.assertEqual(variables, {
            "FACTORY_REPO": REPO, "FACTORY_ISSUE": f"{REPO}#4", "FACTORY_COMMIT": original,
            "FACTORY_RUN_ID": run_id,
        })
        self.assertEqual(recorded["job"], self.job(run_id))
        if self.backend == "woodpecker":
            pipeline, = self.services.pipelines
            self.assertEqual(pipeline["branch"], "main")
            self.assertEqual(pipeline["message"], f"MANUAL: {run_id} @ main")
        config = configure_trial(self.root)
        result = subprocess.run([
            sys.executable, str(LAUNCHER), "--config", str(config),
            "--run-id", variables["FACTORY_RUN_ID"], "--repo", variables["FACTORY_REPO"],
            "--issue", variables["FACTORY_ISSUE"], "--commit", variables["FACTORY_COMMIT"],
            "--job", recorded["job"],
        ], env={**os.environ, "FACTORY_FIXTURE": str(self.root), "FIXTURE_RUN_ID": run_id,
                "FIXTURE_GIT_ORIGIN": origin.as_uri()}, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.root / "runs" / run_id / "run.json").read_text())
        self.assertEqual(record["checked_out_commit"], original)
        self.assertEqual(record["vm_cleanup"], "removed")
        checkout = self.root / "guest-home" / "repo"
        ref = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                      cwd=checkout, text=True)
        self.assertEqual(ref.strip(), "HEAD")

    def test_cutover_preserves_preexisting_labels_even_with_small_forgejo_pages(self):
        self.issue(1)
        self.issue(2)
        seeded = self.issue(3, "factory:run")
        self.seed_and_listen()
        self.assertEqual(self.send(seeded), 202)
        self.command("dispatch", "--once")
        self.assertEqual(self.submitted_inputs(), [])

    def test_existing_issue_requires_an_observed_removal_before_reapplication(self):
        old_snapshot = self.issue(4)
        self.seed_and_listen()
        applied_snapshot = self.issue(4, "factory:run")
        delivery = str(uuid4())
        self.assertEqual(self.send(old_snapshot, delivery=delivery), 202)
        self.assertEqual(self.send(old_snapshot, delivery=delivery), 202)
        self.assertEqual(self.send(applied_snapshot, delivery=delivery), 409)
        self.issue(4, "factory:run", "unrelated")
        self.assertEqual(self.send(applied_snapshot), 202)

        self.issue(4, "unrelated")
        self.issue(4, "unrelated", "factory:run")
        self.assertEqual(self.send(old_snapshot), 202)
        self.assertEqual(self.send(applied_snapshot), 202)
        ledger = json.loads(self.command("status"))
        self.assertEqual(len(ledger), 1)

        self.issue(4)
        self.assertEqual(self.send(applied_snapshot, action="label_cleared"), 202)
        self.issue(4, "factory:run")
        self.assertEqual(self.send(old_snapshot), 202)
        self.command("dispatch", "--once")
        self.assertEqual(len(self.submitted_inputs()), 2)
        self.assertEqual(len({inputs["FACTORY_RUN_ID"] for inputs in self.submitted_inputs()}), 2)

    def test_later_label_notification_can_observe_a_missed_application(self):
        self.issue(4)
        self.seed_and_listen()
        applied = self.issue(4, "factory:run")
        self.assertEqual(json.loads(self.command("status")), [])
        self.services.commit = "f" * 40
        self.issue(4, "factory:run", "unrelated")
        self.assertEqual(self.send(applied), 202)
        observed, = json.loads(self.command("status"))
        self.assertEqual(observed["commit_sha"], "f" * 40)

    def test_label_notification_can_arrive_before_the_current_label_read_changes(self):
        old_snapshot = self.issue(4)
        self.seed_and_listen()
        self.issue(4, "factory:run")
        self.services.pending_labels[4] = (time.monotonic() + 0.15, old_snapshot["labels"])
        self.assertEqual(self.send(old_snapshot), 202)
        observed, = json.loads(self.command("status"))
        self.assertEqual(observed["commit_sha"], COMMIT)

    def test_invalid_signatures_repositories_and_events_never_enqueue_runs(self):
        self.seed_and_listen()
        issue = self.issue(4, "factory:run")
        self.assertEqual(self.send(issue, secret=b"wrong-signing-key"), 401)
        self.assertEqual(self.send(issue, repo="demo/another"), 403)
        self.assertEqual(self.send(issue, "issues", "edited"), 422)
        self.assertEqual(self.send(issue, "pull_request_label", "label_updated"), 422)
        self.assertEqual(json.loads(self.command("status")), [])

    def test_worker_recovers_lost_submit_response_by_stable_run_id(self):
        self.seed_and_listen()
        self.assertEqual(self.send(self.issue(4, "factory:run")), 202)
        self.services.lose_response = True
        self.command("dispatch", "--once")
        initial, = json.loads(self.command("status"))
        self.assertEqual(initial["dispatch"], "submitting")
        self.assertIsNone(initial["job"])
        self.command("dispatch", "--once")
        confirmed, = json.loads(self.command("status"))
        self.assertEqual(confirmed["dispatch"], "submitted")
        self.assertEqual(confirmed["job"], self.job(initial["run_id"]))
        self.assertEqual(confirmed["run_id"], initial["run_id"])
        self.assertEqual(len(self.submitted_inputs()), 1)

    def test_operator_retry_recovers_an_unaccepted_submission_under_the_same_run_id(self):
        self.seed_and_listen()
        self.assertEqual(self.send(self.issue(4, "factory:run")), 202)
        self.services.reject_next_submission = True
        self.command("dispatch", "--once")
        initial, = json.loads(self.command("status"))
        self.assertEqual(initial["dispatch"], "submitting")
        self.command("dispatch", "--once")
        self.assertEqual(self.submitted_inputs(), [])
        self.command("retry", "--run-id", initial["run_id"])
        self.command("retry", "--run-id", initial["run_id"])
        final, = json.loads(self.command("status"))
        self.assertEqual(final["run_id"], initial["run_id"])
        self.assertEqual(final["job"], self.job(initial["run_id"]))
        variables, = self.submitted_inputs()
        self.assertEqual(variables["FACTORY_COMMIT"], COMMIT)
        self.assertEqual(variables["FACTORY_RUN_ID"], initial["run_id"])
        config = configure_trial(self.root)
        command = [
            sys.executable, str(LAUNCHER), "--config", str(config),
            "--run-id", variables["FACTORY_RUN_ID"], "--repo", variables["FACTORY_REPO"],
            "--issue", variables["FACTORY_ISSUE"], "--commit", variables["FACTORY_COMMIT"],
            "--job", final["job"],
        ]
        env = {**os.environ, "FACTORY_FIXTURE": str(self.root),
               "FIXTURE_RUN_ID": final["run_id"]}
        first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        record_path = self.root / "runs" / final["run_id"] / "run.json"
        record = json.loads(record_path.read_text())
        self.assertEqual(record["job"], final["job"])
        self.assertEqual(record["issue"], final["issue"])
        self.assertEqual(record["checked_out_commit"], final["commit_sha"])
        self.assertEqual(record["magpie_key_id"], "fixture-key-id")
        self.assertEqual(record["publication"], "disabled")
        (self.root / "allocated").unlink()
        replay = subprocess.run(command, env=env, capture_output=True, timeout=10)
        self.assertEqual(replay.returncode, 1)
        self.assertEqual(json.loads(record_path.read_text()), record)
        self.assertFalse((self.root / "allocated").exists())
        self.assertFalse((self.root / "vm.json").exists())
        self.assertFalse((self.root / "key").exists())


class WoodpeckerEventsTest(SignedEventScenarios, unittest.TestCase):
    pass


class DaguEventsTest(SignedEventScenarios, unittest.TestCase):
    backend = "dagu"

    def test_duplicate_enqueue_500_recovers_the_existing_job(self):
        self.seed_and_listen()
        self.assertEqual(self.send(self.issue(4, "factory:run")), 202)
        initial, = json.loads(self.command("status"))
        run_id = initial["run_id"]
        payload = {"dagRunId": run_id, "params": json.dumps({
            "FACTORY_RUN_ID": run_id, "FACTORY_REPO": REPO,
            "FACTORY_ISSUE": f"{REPO}#4", "FACTORY_COMMIT": COMMIT,
        })}
        with urlopen(Request(self.service_url + "/api/v1/dags/factory-trial/enqueue",
                             json.dumps(payload).encode()), timeout=5) as response:
            self.assertEqual(response.status, 200)
        self.command("dispatch", "--once")
        final, = json.loads(self.command("status"))
        self.assertEqual(final["dispatch"], "submitted")
        self.assertEqual(final["job"], self.job(run_id))
        self.assertEqual(len(self.submitted_inputs()), 1)

    def test_queued_cancellation_survives_dequeue_removing_remote_history(self):
        self.seed_and_listen()
        self.assertEqual(self.send(self.issue(4, "factory:run")), 202)
        self.command("dispatch", "--once")
        initial, = json.loads(self.command("status"))
        run_id = initial["run_id"]
        self.assertEqual(self.services.dag_runs[run_id]["statusLabel"], "queued")
        self.services.lose_response = True
        self.assertNotEqual(self.invoke("cancel", "--run-id", run_id).returncode, 0)
        pending, = json.loads(self.command("status"))
        self.assertEqual(pending["dispatch"], "cancelling")
        self.command("dispatch", "--once")
        self.command("retry", "--run-id", run_id)
        self.command("cancel", "--run-id", run_id)
        final, = json.loads(self.command("status"))
        self.assertNotIn(run_id, self.services.dag_runs)
        self.assertEqual(final["dispatch"], "cancelled")
        self.assertEqual(final["job"], initial["job"])

    def test_running_cancellation_stops_the_dag_and_preserves_its_history(self):
        self.seed_and_listen()
        self.assertEqual(self.send(self.issue(4, "factory:run")), 202)
        self.command("dispatch", "--once")
        initial, = json.loads(self.command("status"))
        run_id = initial["run_id"]
        self.services.dag_runs[run_id]["statusLabel"] = "running"
        self.command("cancel", "--run-id", run_id)
        final, = json.loads(self.command("status"))
        self.assertEqual(final["dispatch"], "cancelled")
        self.assertEqual(final["job"], initial["job"])
        self.assertEqual(self.services.dag_runs[run_id]["statusLabel"], "aborted")


if __name__ == "__main__":
    unittest.main()
