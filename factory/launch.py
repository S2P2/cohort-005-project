import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid


GUEST = Path(__file__).with_name("guest.py")
SLUG = r"[a-z0-9][a-z0-9-]{0,47}"
REPO = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
PROFILE_CONFIG = {"security.devlxd", "limits.cpu", "limits.memory"}


class TrialError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise TrialError(message)


def endpoint(url):
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname and not (
        parsed.username or parsed.password or parsed.query or parsed.fragment
    ), "Expected a credential-free HTTPS endpoint.")
    return [parsed.hostname, parsed.port or 443]


def load_config(path, request):
    config = json.loads(path.read_text())
    for field in ("incus", "issuer", "run_root"):
        require(Path(config[field]).is_absolute(), f"{field} must be an absolute path.")
    for field in ("incus_project", "incus_profile", "incus_network"):
        require(re.fullmatch(SLUG, config[field]), f"Invalid {field}.")
    require(re.fullmatch("[0-9a-f]{64}", config["image_fingerprint"]),
            "An Incus image fingerprint is required.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", config["magpie_model"]),
            "Expected a single fixed Magpie model.")
    repository = config["repositories"].get(request.repo)
    require(repository is not None, "Repository is outside the trial allowlist.")
    required = [endpoint(repository["url"]), endpoint(config["magpie_url"])]
    blocked = config["blocked_endpoints"]
    require(bool(blocked), "Configure at least one blocked host endpoint.")
    for address, port in blocked:
        ipaddress.ip_address(address)
        require(type(port) is int and 1 <= port <= 65535 and [address, port] not in required,
                "Invalid blocked host endpoint.")
    root = Path(config["run_root"])
    require(root.is_dir() and not root.is_symlink() and root.stat().st_uid == os.geteuid()
            and root.stat().st_mode & 0o077 == 0, "Run root must be a private directory.")
    timeout = config.get("agent_timeout_seconds", 300)
    require(type(timeout) is int and 1 <= timeout <= 600, "Invalid agent timeout.")
    return config, repository, required


def process(args, timeout=30, payload=None, output=None):
    return subprocess.run(
        args, timeout=timeout,
        input=json.dumps(payload).encode() if payload is not None else b"",
        stdout=output if output is not None else subprocess.PIPE,
        stderr=output if output is not None else subprocess.DEVNULL,
        check=False,
    )


def checked(result, stage):
    require(result.returncode == 0, f"{stage} failed (exit {result.returncode}).")
    return result.stdout


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Run:
    def __init__(self, config, request):
        self.config = config
        self.directory = Path(config["run_root"]) / request.run_id
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            raise TrialError("Run ID already has a record. Inspect it before retrying.") from None
        sync_directory(self.directory.parent)
        self.record = {
            "run_id": request.run_id, "repo": request.repo, "issue": request.issue,
            "commit": request.commit, "job": request.job, "startup": request.startup,
            "image_fingerprint": config["image_fingerprint"], "profile": config["incus_profile"],
            "project": config["incus_project"],
            "vm": "factory-" + hashlib.sha256(request.run_id.encode()).hexdigest()[:24],
            "allocation_id": uuid.uuid4().hex, "allocation_attempted": False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "state": "starting", "vm_cleanup": "not_allocated", "magpie_cleanup": "not_issued",
            "magpie_key_id": None, "agent_exit_status": None, "output_complete": False,
        }
        self.save()

    def save(self, **fields):
        self.record.update(fields)
        temporary = self.directory / ".run.tmp"
        with temporary.open("w") as output:
            json.dump(self.record, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.directory / "run.json")
        sync_directory(self.directory)

    def incus(self, *args, **options):
        return process([self.config["incus"], "--project", self.record["project"], *args],
                       **options)

    def instance(self):
        instances = json.loads(checked(self.incus("list", "local:", "--format=json"),
                                       "Incus inventory"))
        return next((item for item in instances if item["name"] == self.record["vm"]), None)

    def owns(self, instance):
        return (instance["expanded_config"].get("user.factory.allocation")
                == self.record["allocation_id"])

    def validate_profile(self, profile_config, devices):
        require(set(profile_config) <= PROFILE_CONFIG
                and profile_config.get("security.devlxd") == "false",
                "The trial profile contains unsupported settings or exposes the Incus socket.")
        require(set(devices) == {"root", "eth0"}, "The trial profile needs only root and eth0.")
        root = devices["root"]
        require(set(root) <= {"type", "path", "pool", "size"} and root.get("type") == "disk"
                and root.get("path") == "/" and root.get("pool"),
                "The trial root disk must be private storage.")
        require(devices["eth0"] == {"type": "nic", "network": self.config["incus_network"],
                                    "name": "eth0"},
                "The trial NIC must use the fixed managed network.")

    def start(self):
        profile = json.loads(checked(process([
            self.config["incus"], "query",
            f"local:/1.0/profiles/{self.record['profile']}?project={self.record['project']}",
        ]), "Incus profile lookup"))
        self.validate_profile(profile["config"], profile["devices"])
        require(self.instance() is None, "The trial VM name is already in use.")
        self.save(allocation_attempted=True, vm_cleanup="pending")
        start = time.monotonic()
        target = f"local:{self.record['vm']}"
        checked(self.incus(
            "init", f"local:{self.record['image_fingerprint']}", target, "--vm",
            f"--profile={self.record['profile']}",
            f"--config=user.factory.allocation={self.record['allocation_id']}", timeout=120,
        ), "Incus VM allocation")
        instance = self.instance()
        require(instance and self.owns(instance) and instance["type"] == "virtual-machine"
                and instance["expanded_config"].get("volatile.base_image")
                == self.record["image_fingerprint"], "Allocated VM identity could not be verified.")
        profile_config = {name: value for name, value in instance["expanded_config"].items()
                          if not name.startswith(("volatile.", "image."))
                          and name != "user.factory.allocation"}
        self.validate_profile(profile_config, instance["expanded_devices"])
        checked(self.incus("start", target, timeout=120), "Incus VM start")
        deadline = time.monotonic() + 120
        while self.incus("exec", target, "--", "/usr/bin/true").returncode:
            require(time.monotonic() < deadline, "Guest agent did not become ready.")
            time.sleep(1)
        self.save(state="ready", startup_seconds=round(time.monotonic() - start, 3))

    def guest(self, action, *, timeout=30, output=None, **payload):
        return self.incus(
            "exec", f"local:{self.record['vm']}", "--mode=non-interactive",
            "--user=1000", "--group=1000",
            "--cwd=/home/factory", "--", "/usr/bin/python3", "-I", "-c", GUEST.read_text(),
            timeout=timeout, payload={"action": action, **payload}, output=output,
        )

    def execute(self, repository, required):
        checked(self.guest("probe", required=required, blocked=self.config["blocked_endpoints"]),
                "Guest network probes")
        self.save(network="passed")
        token = None
        if "token_file" in repository:
            token_path = Path(repository["token_file"])
            with os.fdopen(os.open(token_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as secret:
                info = os.fstat(secret.fileno())
                require(token_path.is_absolute() and info.st_uid == os.geteuid()
                        and stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 == 0,
                        "Repository token file must be a private regular file.")
                token = secret.read().strip()
                require(token and repository.get("username"), "Repository credential is incomplete.")
        commit = checked(self.guest(
            "checkout", url=repository["url"], commit=self.record["commit"],
            token=token, username=repository.get("username"), timeout=120,
        ), "Guest checkout").decode().strip()
        require(commit == self.record["commit"], "Guest checkout differs from the recorded commit.")
        self.save(checked_out_commit=commit, magpie_cleanup="issuance_unconfirmed")
        issued = json.loads(checked(process([
            self.config["issuer"], "issue", self.record["run_id"], self.config["magpie_model"], "900",
        ]), "Magpie issue"))
        key_id = issued["id"]
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", key_id), "Invalid Magpie key ID.")
        self.save(magpie_key_id=key_id, magpie_cleanup="pending", state="running")
        require(isinstance(issued.get("key"), str) and issued["key"], "Magpie key is missing.")
        with (self.directory / "agent.log").open("xb", buffering=0) as log:
            try:
                result = self.guest(
                    "agent", url=self.config["magpie_url"], model=self.config["magpie_model"],
                    key=issued["key"], timeout=self.config.get("agent_timeout_seconds", 300),
                    output=log,
                )
            except subprocess.TimeoutExpired:
                raise TrialError("Guest agent timed out.") from None
            finally:
                os.fsync(log.fileno())
        self.save(agent_exit_status=result.returncode, output_complete=True)
        checked(result, "Guest agent")

    def cleanup_vm(self):
        if self.record["allocation_attempted"]:
            instance = self.instance()
            if instance is not None:
                require(self.owns(instance), "VM ownership could not be verified.")
                self.incus("delete", f"local:{self.record['vm']}", "--force", timeout=120)
            require(self.instance() is None, "VM remains in Incus inventory.")
            self.save(vm_cleanup="removed")

    def cleanup_key(self):
        if self.record["magpie_key_id"] is not None:
            checked(process([self.config["issuer"], "revoke", self.record["magpie_key_id"]]),
                    "Magpie revoke")
            self.save(magpie_cleanup="revoked")


def run_trial(config, repository, required, request):
    run = Run(config, request)
    error = None
    stage = "VM startup"
    try:
        run.start()
        stage = "Trial execution"
        run.execute(repository, required)
    except TrialError as failure:
        error = str(failure)
    except subprocess.TimeoutExpired:
        error = f"{stage} timed out."
    except (OSError, ValueError, KeyError, TypeError):
        error = f"{stage} could not be verified."
    finally:
        for name, cleanup in (("magpie", run.cleanup_key), ("vm", run.cleanup_vm)):
            try:
                cleanup()
            except (TrialError, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
                run.save(**{f"{name}_cleanup": "unconfirmed"})
                error = error or f"{name} cleanup could not be verified."
        run.save(state="failed" if error else "succeeded", error=error,
                 finished_at=datetime.now(timezone.utc).isoformat())
    if error:
        raise TrialError(error)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Run one trusted factory trial in an Incus VM.")
    parser.add_argument("--config", type=Path, required=True)
    for field in ("run-id", "repo", "issue", "commit", "job"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--startup", choices=("cold", "warm", "unspecified"), default="unspecified")
    request = parser.parse_args()
    try:
        require(re.fullmatch(SLUG, request.run_id), "Invalid logical run ID.")
        require(re.fullmatch(REPO, request.repo), "Invalid repository reference.")
        require(re.fullmatch(re.escape(request.repo) + r"#[1-9][0-9]*", request.issue),
                "Issue must belong to the requested repository.")
        require(re.fullmatch("[0-9a-f]{40}", request.commit), "A full Git commit SHA is required.")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:./_-]{0,191}", request.job),
                "Invalid orchestrator job reference.")
        config, repository, required = load_config(request.config, request)
        run_trial(config, repository, required, request)
    except TrialError as error:
        print(error, file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print("Could not load the configuration or persist the run record.", file=sys.stderr)
        return 1
    print(f"Trial {request.run_id} completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
