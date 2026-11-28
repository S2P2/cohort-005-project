#!/usr/bin/python3 -I

import os
import pwd
import sys


CONTROL_REPO = "factory-control/launcher"


def woodpecker_job(env):
    if env.get("CI_PIPELINE_EVENT") != "manual" or env.get("CI_REPO") != CONTROL_REPO:
        sys.exit("Expected a manual pipeline in the operator control repository.")
    if env.get("CI_COMMIT_BRANCH") != "main":
        sys.exit("Expected the protected control branch.")
    pipeline = env.get("CI_PIPELINE_NUMBER", "")
    if not pipeline.isascii() or not pipeline.isdecimal():
        sys.exit("Missing Woodpecker pipeline number.")
    return f"woodpecker:{CONTROL_REPO}:{pipeline}"


def dagu_job(env):
    run_id = env.get("FACTORY_RUN_ID")
    if not run_id or run_id != env.get("DAG_RUN_ID"):
        sys.exit("Expected the factory run ID to match the Dagu run ID.")
    return f"dagu:factory-trial:{run_id}"


def main():
    schedulers = {"woodpecker": woodpecker_job, "dagu": dagu_job}
    if len(sys.argv) != 2 or sys.argv[1] not in schedulers:
        sys.exit("Expected a fixed factory scheduler name.")
    env = os.environ
    job = schedulers[sys.argv[1]](env)
    args = ["/usr/bin/python3", "-I", "/opt/factory/launch.py",
            "--config", "/etc/factory/trial.json", f"--job={job}"]
    for option, variable in (
        ("run-id", "FACTORY_RUN_ID"), ("repo", "FACTORY_REPO"), ("issue", "FACTORY_ISSUE"),
        ("commit", "FACTORY_COMMIT"), ("startup", "FACTORY_STARTUP"),
    ):
        value = env.get(variable, "unspecified" if option == "startup" else "")
        if not value:
            sys.exit(f"Missing {variable}.")
        args.append(f"--{option}={value}")
    os.execve(args[0], args, {
        "HOME": pwd.getpwuid(os.geteuid()).pw_dir,
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
    })


if __name__ == "__main__":
    main()
