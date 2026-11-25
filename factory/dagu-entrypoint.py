#!/usr/bin/python3 -I

import os
import pwd
import sys


def main():
    env = os.environ
    run_id = env.get("FACTORY_RUN_ID")
    if not run_id or run_id != env.get("DAG_RUN_ID"):
        sys.exit("Expected the factory run ID to match the Dagu run ID.")

    args = ["/usr/bin/python3", "-I", "/opt/factory/launch.py",
            "--config", "/etc/factory/trial.json", f"--run-id={run_id}",
            f"--job=dagu:factory-trial:{run_id}"]
    for option, variable in (
        ("repo", "FACTORY_REPO"), ("issue", "FACTORY_ISSUE"),
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
