import base64
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


HOME = Path("/home/factory")
REPO = HOME / "repo"
ENV = {"HOME": str(HOME), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
MARKER = "FACTORY_TRIAL_OK"


def reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def probe(payload):
    return all(reachable(*endpoint) for endpoint in payload["required"]) and not any(
        reachable(*endpoint) for endpoint in payload["blocked"]
    )


def checkout(payload):
    env = {**ENV, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_TERMINAL_PROMPT": "0"}

    def git(*args, credentials=False):
        command_env = dict(env)
        if credentials and payload["token"]:
            basic = f'{payload["username"]}:{payload["token"]}'.encode()
            command_env.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": "Authorization: Basic " + base64.b64encode(basic).decode(),
            })
        return subprocess.check_output(
            ["/usr/bin/git", "-c", "http.followRedirects=false", *args],
            cwd=REPO, env=command_env, stderr=subprocess.DEVNULL, text=True,
        ).strip()

    REPO.mkdir(mode=0o700)
    git("init", "--quiet")
    git("fetch", "--quiet", "--depth=1", payload["url"], payload["commit"], credentials=True)
    git("checkout", "--quiet", "--detach", "FETCH_HEAD")
    commit = git("rev-parse", "HEAD")
    if commit != payload["commit"]:
        return False
    print(commit)
    return True


def agent(payload):
    with tempfile.TemporaryDirectory(dir=HOME) as codex_home:
        last_message = Path(codex_home) / "last-message"
        key = payload["key"]
        with subprocess.Popen(
            ["/usr/local/bin/codex", "-a", "never", "exec", "--ephemeral", "--sandbox",
             "read-only", "--output-last-message", str(last_message),
             "-c", 'model_provider="magpie"',
             "-c", 'model_providers.magpie.name="Magpie"',
             "-c", "model_providers.magpie.base_url=" + json.dumps(payload["url"]),
             "-c", 'model_providers.magpie.env_key="FACTORY_MAGPIE_KEY"',
             "-c", 'model_providers.magpie.wire_api="responses"',
             "-c", 'shell_environment_policy.exclude=["FACTORY_MAGPIE_KEY"]',
             "--model", payload["model"],
             f"Reply with exactly {MARKER}. Do not use tools or make any changes."],
            cwd=REPO, env={**ENV, "CODEX_HOME": codex_home, "FACTORY_MAGPIE_KEY": key},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ) as process:
            for line in process.stdout:
                sys.stdout.buffer.write(line.replace(key.encode(), b"[redacted]"))
                sys.stdout.buffer.flush()
        try:
            marker_valid = last_message.read_bytes().strip() == MARKER.encode()
        except OSError:
            marker_valid = False
        print(json.dumps({"exit_status": process.returncode, "marker_valid": marker_valid}),
              file=sys.stderr, flush=True)


try:
    request = json.load(sys.stdin)
    if request["action"] == "agent":
        agent(request)
        sys.exit(0)
    operation = {"checkout": checkout, "probe": probe}[request["action"]]
    sys.exit(0 if operation(request) else 1)
except (OSError, ValueError, subprocess.SubprocessError):
    print("Guest operation failed.", file=sys.stderr)
    sys.exit(1)
