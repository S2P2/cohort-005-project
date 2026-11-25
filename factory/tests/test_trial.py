import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Timer
import time
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "launch.py"
COMMIT = "1234567890abcdef" * 2 + "12345678"


def configure_trial(root):
    config = root / "config.json"
    runs = root / "runs"
    runs.mkdir(mode=0o700)
    fixture = Path(__file__).with_name("service_fixture.py")
    for name in ("incus", "issuer"):
        executable = root / name
        executable.write_text(f"#!{sys.executable}\n" + fixture.read_text())
        executable.chmod(0o700)
    config.write_text(json.dumps({
        "run_root": str(runs),
        "incus": str(root / "incus"),
        "incus_project": "factory",
        "incus_profile": "trial",
        "incus_network": "trial-net",
        "image_fingerprint": "a" * 64,
        "issuer": str(root / "issuer"),
        "magpie_url": "https://magpie.example/v1",
        "magpie_model": "openai/example",
        "blocked_endpoints": [["192.0.2.1", 8443]],
        "repositories": {"demo/trial": {"url": "https://forgejo.example/demo/trial.git"}},
    }))
    return config


class TrialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = configure_trial(self.root)
        self.runs = self.root / "runs"

    def args(self, *args):
        return [sys.executable, str(LAUNCHER), "--config", str(self.config), *args]

    def launch_args(self):
        return self.args("--run-id", "trial-1", "--repo", "demo/trial", "--issue", "demo/trial#4",
                         "--commit", COMMIT, "--job", "woodpecker:control/trial:1",
                         "--startup", "cold")

    def env(self, **env):
        return {**os.environ, "FACTORY_FIXTURE": str(self.root), **env}

    def launch(self, **env):
        return subprocess.run(self.launch_args(), env=self.env(**env), text=True,
                              capture_output=True, timeout=10)

    def control(self, *args, **env):
        return subprocess.run(self.args(*args), env=self.env(**env), text=True,
                              capture_output=True, timeout=10)

    def start(self, case):
        child = subprocess.Popen(self.launch_args(), env=self.env(FACTORY_CASE=case),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        self.addCleanup(child.wait, 10)
        self.addCleanup(child.kill)
        return child

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "Fixture did not reach the interruption point.")
            time.sleep(0.01)

    def record(self):
        return json.loads((self.runs / "trial-1" / "run.json").read_text())

    def test_replay_reconciles_after_launcher_is_killed_during_allocation(self):
        child = self.start("interrupt-allocation")
        self.wait_for(lambda: (self.root / "allocating").exists())
        child.kill()
        child.wait(5)
        (self.root / "release").touch()
        recovery = self.control("reconcile", "--run-id", "trial-1")
        self.assertEqual(recovery.returncode, 0, recovery.stderr)
        self.assertEqual(self.record()["state"], "interrupted")
        self.assertEqual(self.record()["vm_cleanup"], "removed")
        self.assertFalse((self.root / "vm.json").exists())
        (self.root / "allocated").unlink()
        replay = self.launch()
        self.assertEqual(replay.returncode, 1)
        self.assertFalse((self.root / "allocated").exists())

    def test_reconciliation_waits_for_server_allocation_after_client_is_killed(self):
        child = self.start("server-allocation")
        allocating = self.root / "allocating"
        self.wait_for(allocating.exists)
        details = json.loads(allocating.read_text())
        child.kill()
        child.wait(5)
        os.kill(details["pid"], signal.SIGKILL)

        def finish_server_allocation():
            (self.root / "vm.json").write_text(json.dumps(details["instance"]))
            (self.root / "incus-operation.json").unlink()

        timer = Timer(0.4, finish_server_allocation)
        timer.start()
        self.addCleanup(timer.join, 5)
        result = self.control("reconcile", "--run-id", "trial-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["state"], "interrupted")
        self.assertFalse((self.root / "vm.json").exists())

    def test_revokes_a_key_when_the_issuer_loses_its_response(self):
        result = self.launch(FACTORY_CASE="lost-key-response")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.record()["state"], "failed")
        self.assertEqual(self.record()["magpie_key_id"], "fixture-key-id")
        self.assertEqual(self.record()["magpie_cleanup"], "revoked")
        self.assertFalse((self.root / "key").exists())

    def test_cancel_fences_late_launch_and_confirms_guest_and_key_removal(self):
        agent_log = self.runs / "trial-1" / "agent.log"
        child = self.start("agent-interrupt")
        self.wait_for(lambda: agent_log.exists() and "started agent" in agent_log.read_text())
        result = self.control("cancel", "--run-id", "trial-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(child.wait(5), 1)
        self.assertEqual(self.record()["state"], "cancelled")
        self.assertEqual(self.record()["vm_cleanup"], "removed")
        self.assertEqual(self.record()["magpie_cleanup"], "revoked")
        self.assertFalse(self.record()["output_complete"])
        self.assertIn("started agent", agent_log.read_text())
        self.assertFalse((self.root / "key").exists())
        self.assertFalse((self.root / "vm.json").exists())
        (self.root / "allocated").unlink()
        self.assertEqual(self.launch().returncode, 1)
        self.assertFalse((self.root / "allocated").exists())

    def test_cancel_before_launch_survives_orchestrator_retry(self):
        result = self.control("cancel", "--run-id", "trial-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cancelled before launch", result.stdout)
        self.assertEqual(self.launch().returncode, 1)
        self.assertEqual(self.record()["state"], "cancelled")
        self.assertEqual(self.record()["vm_cleanup"], "not_allocated")
        self.assertEqual(self.record()["magpie_cleanup"], "not_issued")
        self.assertFalse((self.root / "allocated").exists())

    def test_recovery_retries_unconfirmed_cleanup_after_kill_with_incomplete_output(self):
        agent_log = self.runs / "trial-1" / "agent.log"
        child = self.start("agent-interrupt")
        self.wait_for(lambda: agent_log.exists() and "started agent" in agent_log.read_text())
        child.kill()
        child.wait(5)
        (self.root / "vm.json").unlink()
        result = self.control("reconcile", "--run-id", "trial-1", FACTORY_CASE="revoke-unavailable")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.record()["state"], "cleanup_pending")
        self.assertEqual(self.record()["outcome"], "interrupted")
        self.assertEqual(self.record()["vm_cleanup"], "removed")
        self.assertEqual(self.record()["magpie_cleanup"], "unconfirmed")
        self.assertTrue((self.root / "key").exists())
        result = self.control("reconcile")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["state"], "interrupted")
        self.assertEqual(self.record()["magpie_cleanup"], "revoked")
        self.assertFalse((self.root / "key").exists())
        self.assertFalse(self.record()["output_complete"])
        self.assertIn("started agent", agent_log.read_text())

    def test_provider_failure_keeps_output_and_exit_code_after_teardown(self):
        result = self.launch(FACTORY_CASE="provider-error-with-marker")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.record()["state"], "failed")
        self.assertEqual(self.record()["agent_exit_status"], 42)
        self.assertTrue(self.record()["output_complete"])
        self.assertIn("Provider unavailable", (self.runs / "trial-1" / "agent.log").read_text())
        self.assertFalse((self.root / "key").exists())
        self.assertFalse((self.root / "vm.json").exists())

    def test_records_trial_and_removes_vm_and_magpie_key(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.runs / "trial-1"
        record = json.loads((run / "run.json").read_text())
        self.assertEqual(record["commit"], COMMIT)
        self.assertEqual(record["checked_out_commit"], COMMIT)
        self.assertEqual(record["startup"], "cold")
        self.assertGreaterEqual(record["startup_seconds"], 0)
        self.assertEqual(record["job"], "woodpecker:control/trial:1")
        self.assertEqual(record["magpie_key_id"], "fixture-key-id")
        self.assertEqual(record["agent_exit_status"], 0)
        self.assertEqual(record["vm_cleanup"], "removed")
        self.assertEqual(record["magpie_cleanup"], "revoked")
        self.assertIn("FACTORY_TRIAL_OK", (run / "agent.log").read_text())
        self.assertFalse((self.root / "vm.json").exists())
        self.assertFalse((self.root / "key").exists())
        self.assertNotIn("fixture-secret", (run / "run.json").read_text() + result.stdout + result.stderr)

    def test_rejects_profile_with_host_directory_before_vm_allocation(self):
        result = self.launch(FACTORY_CASE="shared-disk")
        self.assertEqual(result.returncode, 1)
        record = json.loads((self.runs / "trial-1" / "run.json").read_text())
        self.assertEqual(record["vm_cleanup"], "not_allocated")
        self.assertFalse((self.root / "allocated").exists())

    def test_keeps_partial_output_when_the_agent_times_out(self):
        config = json.loads(self.config.read_text())
        config["agent_timeout_seconds"] = 1
        self.config.write_text(json.dumps(config))
        result = self.launch(FACTORY_CASE="agent-timeout")
        self.assertEqual(result.returncode, 1)
        run = self.runs / "trial-1"
        record = json.loads((run / "run.json").read_text())
        self.assertEqual(record["error"], "Guest agent timed out.")
        self.assertEqual(record["vm_cleanup"], "removed")
        self.assertEqual(record["magpie_cleanup"], "revoked")
        self.assertFalse(record["output_complete"])
        self.assertIsNone(record["agent_exit_status"])
        self.assertIn("started agent", (run / "agent.log").read_text())

    def test_refuses_a_checkout_that_differs_from_the_requested_commit(self):
        result = self.launch(FACTORY_CASE="wrong-ref")
        self.assertEqual(result.returncode, 1)
        record = json.loads((self.runs / "trial-1" / "run.json").read_text())
        self.assertEqual(record["vm_cleanup"], "removed")
        self.assertEqual(record["magpie_cleanup"], "not_issued")
        self.assertFalse((self.runs / "trial-1" / "agent.log").exists())

    def test_replaying_the_logical_id_preserves_the_original_record(self):
        self.assertEqual(self.launch().returncode, 0)
        record = self.runs / "trial-1" / "run.json"
        original = record.read_text()
        (self.root / "allocated").unlink()
        self.assertEqual(self.launch().returncode, 1)
        self.assertEqual(record.read_text(), original)
        self.assertFalse((self.root / "allocated").exists())


if __name__ == "__main__":
    unittest.main()
