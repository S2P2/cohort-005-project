import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "launch.py"
COMMIT = "1234567890abcdef" * 2 + "12345678"


class TrialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.json"
        self.runs = self.root / "runs"
        self.runs.mkdir(mode=0o700)
        fixture = Path(__file__).with_name("service_fixture.py")
        for name in ("incus", "issuer"):
            executable = self.root / name
            executable.write_text(f"#!{sys.executable}\n" + fixture.read_text())
            executable.chmod(0o700)
        self.config.write_text(json.dumps({
            "run_root": str(self.runs),
            "incus": str(self.root / "incus"),
            "incus_project": "factory",
            "incus_profile": "trial",
            "incus_network": "trial-net",
            "image_fingerprint": "a" * 64,
            "issuer": str(self.root / "issuer"),
            "magpie_url": "https://magpie.example/v1",
            "magpie_model": "openai/example",
            "blocked_endpoints": [["192.0.2.1", 8443]],
            "repositories": {"demo/trial": {"url": "https://forgejo.example/demo/trial.git"}},
        }))

    def launch(self, **env):
        return subprocess.run(
            [sys.executable, str(LAUNCHER), "--config", str(self.config),
             "--run-id", "trial-1", "--repo", "demo/trial", "--issue", "demo/trial#4",
             "--commit", COMMIT, "--job", "woodpecker:control/trial:1", "--startup", "cold"],
            env={**os.environ, "FACTORY_FIXTURE": str(self.root), **env},
            text=True, capture_output=True, check=False,
        )

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
