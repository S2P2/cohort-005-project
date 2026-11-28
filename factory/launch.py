import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
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
KEY_ID = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"


class TrialError(Exception):
    pass


class Cancelled(TrialError):
    pass


def require(condition, message):
    if not condition:
        raise TrialError(message)


def endpoint(url):
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname and not (
        parsed.username is not None or parsed.query or parsed.fragment
    ), "Expected a credential-free HTTPS endpoint.")
    return [parsed.hostname, parsed.port or 443]


def load_config(path):
    config = json.loads(path.read_text())
    for field in ("incus", "issuer", "run_root"):
        require(Path(config[field]).is_absolute(), f"{field} must be an absolute path.")
    for field in ("incus_project", "incus_profile", "incus_network"):
        require(re.fullmatch(SLUG, config[field]), f"Invalid {field}.")
    require(re.fullmatch("[0-9a-f]{64}", config["image_fingerprint"]),
            "An Incus image fingerprint is required.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", config["magpie_model"]),
            "Expected a single fixed Magpie model.")
    root = Path(config["run_root"])
    require(root.is_dir() and not root.is_symlink() and root.stat().st_uid == os.geteuid()
            and root.stat().st_mode & 0o077 == 0, "Run root must be a private directory.")
    return config


def trial_inputs(config, request):
    repository = config["repositories"].get(request.repo)
    require(repository is not None, "Repository is outside the trial allowlist.")
    required = [endpoint(repository["url"]), endpoint(config["magpie_url"])]
    blocked = config["blocked_endpoints"]
    require(bool(blocked), "Configure at least one blocked host endpoint.")
    for address, port in blocked:
        ipaddress.ip_address(address)
        require(type(port) is int and 1 <= port <= 65535 and [address, port] not in required,
                "Invalid blocked host endpoint.")
    timeout = config.get("agent_timeout_seconds", 300)
    require(type(timeout) is int and 1 <= timeout <= 600, "Invalid agent timeout.")
    return repository, required


def process(args, timeout=30, payload=None, output=None, lease=(), stop=None, capture_stderr=False):
    data = json.dumps(payload).encode() if payload is not None else b""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(args, stdin=subprocess.PIPE, pass_fds=lease,
                          stdout=output if output is not None else subprocess.PIPE,
                          stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL) as child:
        try:
            while True:
                if stop is not None:
                    stop()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    stdout, stderr = child.communicate(data, timeout=min(0.25, remaining))
                    return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    data = None
        except BaseException:
            child.kill()
            child.wait()
            raise


def checked(result, stage):
    require(result.returncode == 0, f"{stage} failed (exit {result.returncode}).")
    return result.stdout


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_directory(config, run_id):
    require(bool(re.fullmatch(SLUG, run_id)), "Invalid logical run ID.")
    return Path(config["run_root"]) / run_id


def ensure_directory(directory):
    directory.mkdir(mode=0o700, exist_ok=True)
    sync_directory(directory.parent)


@contextmanager
def run_lease(directory, wait):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        deadline = time.monotonic() + (150 if wait else 0)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not wait:
                    yield None
                    return
                require(time.monotonic() < deadline, "Timed out waiting for the active launcher.")
                time.sleep(0.25)
        yield descriptor
    finally:
        os.close(descriptor)


class Run:
    def __init__(self, config, directory, descriptor):
        self.config = config
        self.directory = directory
        self.lease = (descriptor,)
        self.signalled = False
        try:
            self.record = json.loads((directory / "run.json").read_text())
        except FileNotFoundError:
            self.record = None

    def create(self, request):
        self.record = {
            "run_id": request.run_id, "repo": request.repo, "issue": request.issue,
            "commit": request.commit, "job": request.job, "startup": request.startup,
            "image_fingerprint": self.config["image_fingerprint"], "profile": self.config["incus_profile"],
            "project": self.config["incus_project"],
            "vm": "factory-" + hashlib.sha256(request.run_id.encode()).hexdigest()[:24],
            "allocation_id": uuid.uuid4().hex, "allocation_attempted": False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "state": "starting", "vm_cleanup": "not_allocated", "magpie_cleanup": "not_issued",
            "magpie_key_id": None, "agent_exit_status": None, "output_complete": False,
            "publication": "disabled",
        }
        self.save()

    def cancelled(self):
        return self.signalled or (self.directory / "cancel.requested").exists()

    def check_stop(self):
        if self.cancelled():
            raise Cancelled("Operator requested cancellation.")

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

    def incus(self, *args, keep_lease=True, **options):
        return process([self.config["incus"], "--project", self.record["project"], *args],
                       lease=self.lease if keep_lease else (), **options)

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
        self.check_stop()
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
        self.check_stop()
        checked(self.incus("start", target, timeout=120), "Incus VM start")
        deadline = time.monotonic() + 120
        while self.incus("exec", target, "--", "/usr/bin/true").returncode:
            self.check_stop()
            require(time.monotonic() < deadline, "Guest agent did not become ready.")
            time.sleep(1)
        self.save(state="ready", startup_seconds=round(time.monotonic() - start, 3))

    def guest(self, action, *, timeout=30, output=None, **payload):
        return self.incus(
            "exec", f"local:{self.record['vm']}", "--mode=non-interactive",
            "--user=1000", "--group=1000",
            "--cwd=/home/factory", "--", "/usr/bin/python3", "-I", "-c", GUEST.read_text(),
            timeout=timeout, payload={"action": action, **payload}, output=output,
            stop=self.check_stop, keep_lease=False, capture_stderr=action == "agent",
        )

    def execute(self, repository, required):
        self.check_stop()
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
        self.check_stop()
        self.save(checked_out_commit=commit, magpie_cleanup="issuance_unconfirmed")
        issued = json.loads(checked(process([
            self.config["issuer"], "issue", self.record["run_id"], self.config["magpie_model"], "900",
        ], lease=self.lease), "Magpie issue"))
        key_id = issued["id"]
        require(re.fullmatch(KEY_ID, key_id), "Invalid Magpie key ID.")
        self.save(magpie_key_id=key_id, magpie_cleanup="pending", state="running")
        require(isinstance(issued.get("key"), str) and issued["key"], "Magpie key is missing.")
        with (self.directory / "agent.log").open("xb", buffering=0) as log:
            sync_directory(self.directory)
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
        checked(result, "Guest agent transport")
        try:
            completion = json.loads(result.stderr)
            status, valid = completion["exit_status"], completion["marker_valid"]
            require(type(status) is int and type(valid) is bool, "Invalid guest agent completion.")
        except (ValueError, KeyError, TypeError):
            raise TrialError("Invalid guest agent completion.") from None
        self.save(agent_exit_status=status, output_complete=True)
        require(status == 0, f"Codex CLI failed (exit {status}).")
        require(valid, "The model response did not match the trial marker.")

    def cleanup_vm(self):
        if not self.record["allocation_attempted"] or self.record["vm_cleanup"] == "removed":
            return
        self.wait_for_vm_operations()
        instance = self.instance()
        if instance is not None:
            require(self.owns(instance), "VM ownership could not be verified.")
            self.incus("delete", f"local:{self.record['vm']}", "--force", timeout=120)
            self.wait_for_vm_operations()
        require(self.instance() is None, "VM remains in Incus inventory.")
        self.save(vm_cleanup="removed")

    def wait_for_vm_operations(self):
        names = {f"/1.0/{kind}/{self.record['vm']}" for kind in ("instances", "virtual-machines")}
        deadline = time.monotonic() + 30
        while True:
            operations = json.loads(checked(
                self.incus("operation", "list", "local:", "--format=json"), "Incus operations"))
            require(isinstance(operations, list), "Invalid Incus operation inventory.")
            if not any(operation["class"] == "task" and operation["status_code"] < 200
                       and any(urlsplit(resource).path in names
                               for group in (operation.get("resources") or {}).values()
                               for resource in group)
                       for operation in operations):
                return
            require(time.monotonic() < deadline, "Incus VM operation has not settled.")
            time.sleep(0.25)

    def cleanup_key(self):
        if self.record["magpie_cleanup"] in {"not_issued", "revoked"}:
            return
        issuer = self.config["issuer"]
        key_ids = self.record.get("magpie_key_ids")
        if key_ids is None:
            key = self.record["magpie_key_id"]
            key_ids = [key] if key else json.loads(checked(process(
                [issuer, "find", self.record["run_id"]], lease=self.lease), "Magpie key lookup"))
            require(isinstance(key_ids, list) and all(
                isinstance(key_id, str) and re.fullmatch(KEY_ID, key_id) for key_id in key_ids
            ), "Invalid Magpie key inventory.")
            self.save(magpie_key_ids=key_ids, magpie_key_id=key_ids[0] if len(key_ids) == 1 else None)
        for key_id in key_ids:
            checked(process([issuer, "revoke", key_id], lease=self.lease), "Magpie revoke")
        self.save(magpie_cleanup="revoked" if key_ids else "not_issued")

    def cleanup_complete(self):
        return (self.record["vm_cleanup"] in {"not_allocated", "removed"}
                and self.record["magpie_cleanup"] in {"not_issued", "revoked"})


RECOVERY_ERRORS = (TrialError, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError)
TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def finish(run, outcome, error):
    run.save(state="cleanup_pending", outcome=outcome, error=error)
    for name, cleanup in (("vm", run.cleanup_vm), ("magpie", run.cleanup_key)):
        try:
            cleanup()
        except RECOVERY_ERRORS:
            run.save(**{f"{name}_cleanup": "unconfirmed"})
    log = run.directory / "agent.log"
    if log.is_file():
        with log.open("rb") as output:
            os.fsync(output.fileno())
    if not run.cleanup_complete():
        raise TrialError(f"Cleanup remains unconfirmed for {run.record['run_id']}.")
    run.save(state=outcome, finished_at=datetime.now(timezone.utc).isoformat())


def reconcile(run):
    if run.record is None:
        return
    state = run.record["state"]
    if state in TERMINAL and run.cleanup_complete():
        return
    if state == "cleanup_pending":
        finish(run, run.record["outcome"], run.record["error"])
    elif state in TERMINAL:
        finish(run, state, run.record.get("error"))
    elif run.cancelled():
        finish(run, "cancelled", "Operator requested cancellation.")
    else:
        finish(run, "interrupted", "Launcher exited before recording a terminal outcome.")


def run_trial(run, repository, required):
    error = None
    outcome = "failed"
    stage = "VM startup"
    previous_handlers = {
        name: signal.signal(name, lambda *_: setattr(run, "signalled", True))
        for name in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        run.check_stop()
        run.start()
        stage = "Trial execution"
        run.execute(repository, required)
        run.check_stop()
        outcome = "succeeded"
    except Cancelled as failure:
        outcome = "cancelled"
        error = str(failure)
    except TrialError as failure:
        error = str(failure)
    except subprocess.TimeoutExpired:
        error = f"{stage} timed out."
    except (OSError, ValueError, KeyError, TypeError):
        error = f"{stage} could not be verified."
    finally:
        try:
            if run.cancelled():
                outcome = "cancelled"
                error = "Operator requested cancellation."
            finish(run, outcome, error)
        finally:
            for name, handler in previous_handlers.items():
                signal.signal(name, handler)
    if error:
        raise TrialError(error)


def launch(config, request):
    directory = run_directory(config, request.run_id)
    repository, required = trial_inputs(config, request)
    ensure_directory(directory)
    with run_lease(directory, wait=False) as descriptor:
        require(descriptor is not None, "Run ID is already active.")
        run = Run(config, directory, descriptor)
        if run.record is not None:
            require(all(run.record[field] == getattr(request, field)
                        for field in ("repo", "issue", "commit")),
                    "Logical run ID is already associated with different inputs.")
            reconcile(run)
            raise TrialError(f"Existing run {request.run_id} is {run.record['state']}.")
        run.create(request)
        run_trial(run, repository, required)


def manage(config, request):
    if request.run_id:
        directory = run_directory(config, request.run_id)
        if request.operation == "cancel":
            ensure_directory(directory)
            with (directory / "cancel.requested").open("ab") as marker:
                os.fsync(marker.fileno())
            sync_directory(directory)
        require(directory.is_dir(), "Run ID has no host record.")
        directories = [directory]
    else:
        directories = sorted(directory for directory in Path(config["run_root"]).iterdir()
                             if re.fullmatch(SLUG, directory.name) and directory.is_dir())
    failures = []
    for directory in directories:
        try:
            with run_lease(directory, wait=bool(request.run_id)) as descriptor:
                if descriptor is None:
                    continue
                run = Run(config, directory, descriptor)
                reconcile(run)
                state = run.record["state"] if run.record else (
                    "cancelled before launch" if run.cancelled() else "no run recorded")
                print(f"{directory.name}: {state}")
        except RECOVERY_ERRORS:
            failures.append(directory.name)
    require(not failures, "Could not confirm recovery for: " + ", ".join(failures))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Run one trusted factory trial in an Incus VM.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("operation", choices=("run", "reconcile", "cancel"), nargs="?", default="run")
    for field in ("run-id", "repo", "issue", "commit", "job"):
        parser.add_argument("--" + field)
    parser.add_argument("--startup", choices=("cold", "warm", "unspecified"), default="unspecified")
    request = parser.parse_args()
    try:
        config = load_config(request.config)
        if request.operation != "run":
            require(request.operation != "cancel" or request.run_id, "Cancellation needs --run-id.")
            manage(config, request)
            return 0
        require(all(getattr(request, field) for field in ("run_id", "repo", "issue", "commit", "job")),
                "Run requires --run-id, --repo, --issue, --commit and --job.")
        require(re.fullmatch(REPO, request.repo), "Invalid repository reference.")
        require(re.fullmatch(re.escape(request.repo) + r"#[1-9][0-9]*", request.issue),
                "Issue must belong to the requested repository.")
        require(re.fullmatch("[0-9a-f]{40}", request.commit), "A full Git commit SHA is required.")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:./_-]{0,191}", request.job),
                "Invalid orchestrator job reference.")
        launch(config, request)
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
