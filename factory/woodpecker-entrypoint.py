#!/usr/bin/python3 -I

import os
import pwd
import sys


CONTROL_REPO = "factory-control/launcher"


def main():
    env = os.environ
    if env.get("CI_PIPELINE_EVENT") != "manual" or env.get("CI_REPO") != CONTROL_REPO:
        sys.exit("Expected a manual pipeline in the operator control repository.")
    if env.get("CI_COMMIT_BRANCH") != "main":
        sys.exit("Expected the protected control branch.")

    args = ["/usr/bin/python3", "-I", "/opt/factory/launch.py",
            "--config", "/etc/factory/trial.json"]
    for option, variable in (
        ("run-id", "FACTORY_RUN_ID"), ("repo", "FACTORY_REPO"), ("issue", "FACTORY_ISSUE"),
        ("commit", "FACTORY_COMMIT"), ("startup", "FACTORY_STARTUP"),
    ):
        value = env.get(variable, "unspecified" if option == "startup" else "")
        if not value:
            sys.exit(f"Missing {variable}.")
        args.append(f"--{option}={value}")
    pipeline = env.get("CI_PIPELINE_NUMBER", "")
    if not pipeline.isascii() or not pipeline.isdecimal():
        sys.exit("Missing Woodpecker pipeline number.")
    args.append(f"--job=woodpecker:{CONTROL_REPO}:{pipeline}")
    os.execve(args[0], args, {
        "HOME": pwd.getpwuid(os.geteuid()).pw_dir,
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
    })


if __name__ == "__main__":
    main()
