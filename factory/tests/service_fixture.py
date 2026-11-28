import json
import os
from pathlib import Path
import subprocess
import sys
import time


root = Path(os.environ["FACTORY_FIXTURE"])
case = os.environ.get("FACTORY_CASE")
args = sys.argv[1:]
vm = root / "vm.json"
key = root / "key"
fingerprint = "a" * 64
profile = {
    "config": {"security.devlxd": "false"},
    "devices": {
        "root": {"type": "disk", "path": "/", "pool": "default"},
        "eth0": {"type": "nic", "network": "trial-net", "name": "eth0"},
    },
}

if Path(sys.argv[0]).name == "issuer":
    if args == ["issue", os.environ.get("FIXTURE_RUN_ID", "trial-1"), "openai/example", "900"]:
        key.touch()
        if case == "lost-key-response":
            sys.exit(3)
        print(json.dumps({"id": "fixture-key-id", "key": "fixture-secret"}))
    elif args == ["revoke", "fixture-key-id"]:
        if case == "revoke-unavailable":
            sys.exit(3)
        key.unlink(missing_ok=True)
    elif args == ["find", os.environ.get("FIXTURE_RUN_ID", "trial-1")]:
        print(json.dumps(["fixture-key-id"] if key.exists() else []))
    else:
        sys.exit(2)
    sys.exit(0)

if args == ["query", "local:/1.0/profiles/trial?project=factory"]:
    if case == "shared-disk":
        profile["devices"]["root"]["source"] = "/host"
    print(json.dumps(profile))
    sys.exit(0)

assert args[:2] == ["--project", "factory"]
command, *rest = args[2:]
if command == "operation":
    assert rest == ["list", "local:", "--format=json"]
    pending = root / "incus-operation.json"
    print("[" + pending.read_text() + "]" if pending.exists() else "[]")
elif command == "list":
    assert rest == ["local:", "--format=json"]
    print("[" + vm.read_text() + "]" if vm.exists() else "[]")
elif command == "init":
    (root / "allocated").touch()
    assert rest[0] == f"local:{fingerprint}" and "--vm" in rest and "--profile=trial" in rest
    assert rest[1].startswith("local:")
    config = dict(item.removeprefix("--config=").split("=", 1)
                  for item in rest if item.startswith("--config="))
    instance = {
        "name": rest[1].removeprefix("local:"), "type": "virtual-machine",
        "expanded_config": {**profile["config"], **config, "volatile.base_image": fingerprint},
        "expanded_devices": profile["devices"],
    }
    if case in {"interrupt-allocation", "server-allocation"}:
        pending = root / "incus-operation.json"
        pending.write_text(json.dumps({
            "class": "task", "status_code": 103,
            "resources": {"instances": [f"/1.0/instances/{instance['name']}"]},
        }))
        details = root / "allocating.tmp"
        details.write_text(json.dumps({"instance": instance, "pid": os.getpid()}))
        details.replace(root / "allocating")
        deadline = time.monotonic() + 5
        while not (root / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        vm.write_text(json.dumps(instance))
        pending.unlink()
    else:
        vm.write_text(json.dumps(instance))
elif command == "start":
    assert vm.exists()
elif command == "exec":
    assert vm.exists() and "fixture-secret" not in " ".join(rest)
    if "/usr/bin/true" in rest:
        sys.exit(0)
    payload = json.load(sys.stdin)
    action = payload["action"]
    if action == "probe":
        assert payload["required"] == [["forgejo.example", 443], ["magpie.example", 443]]
        assert payload["blocked"] == [["192.0.2.1", 8443]]
    elif action == "checkout":
        if origin := os.environ.get("FIXTURE_GIT_ORIGIN"):
            guest_home = root / "guest-home"
            guest_home.mkdir()
            guest = rest[-1].replace('Path("/home/factory")', f"Path({str(guest_home)!r})")
            payload["url"] = origin
            sys.exit(subprocess.run([sys.executable, "-I", "-c", guest],
                                    input=json.dumps(payload), text=True).returncode)
        print("f" * 40 if case == "wrong-ref" else payload["commit"])
    elif action == "agent":
        assert payload["key"] == "fixture-secret"
        if case in {"agent-timeout", "revoke-unavailable", "agent-interrupt"}:
            print("started agent", flush=True)
            deadline = time.monotonic() + 5
            while vm.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not vm.exists():
                sys.exit(4)
        executable = root / "codex"
        marker = "" if case == "missing-marker" else "FACTORY_TRIAL_OK"
        provider_failure = case == "provider-error-with-marker"
        executable.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import os\n"
            "import sys\n"
            'message = Path(sys.argv[sys.argv.index("--output-last-message") + 1])\n'
            f"if {bool(marker)}:\n"
            f"    message.write_text({marker!r})\n"
            f'print({"Provider unavailable" if provider_failure else marker or "No response marker"!r})\n'
            'print(os.environ["FACTORY_MAGPIE_KEY"], file=sys.stderr)\n'
            f"sys.exit({42 if provider_failure else 0})\n"
        )
        executable.chmod(0o700)
        guest_home = root / "guest-home"
        (guest_home / "repo").mkdir(parents=True, exist_ok=True)
        guest = rest[-1].replace('Path("/home/factory")', f"Path({str(guest_home)!r})")
        guest = guest.replace('"/usr/local/bin/codex"', repr(str(executable)))
        sys.exit(subprocess.run([sys.executable, "-I", "-c", guest],
                                input=json.dumps(payload), text=True).returncode)
    else:
        sys.exit(2)
elif command == "delete":
    vm.unlink()
else:
    sys.exit(2)
