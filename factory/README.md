# Factory infrastructure trial

Run a minimal Codex invocation in a fresh Incus VM through a dedicated Woodpecker local agent. The trial is for one operator and a trusted Forgejo repository with a harmless commit. A repository containing agent instructions, plugins, hooks or executable build steps is unsuitable for the smoke test. Use a small README-only repository with a matching test issue.

## Operator setup

These are proposed setup stages and value destinations for operator confirmation before authoring the interactive wizard. The launcher performs the repeatable run steps.

| Stage | Operator selection or action | Value destination |
| --- | --- | --- |
| Host | Confirm the Linux machine, usable KVM, dedicated unprivileged `factory` host user and Woodpecker agent enrollment. Incus access is privileged even when the account is non-root. | Root-owned launcher installation at `/opt/factory`, agent enrollment secret in the host service configuration, `factory`-owned mode `0700` `/var/lib/factory/runs`. |
| Guest | Confirm the Incus project/profile, VM image fingerprint, storage pool, managed network and network rules. | Incus project/profile and network firewall, names and fingerprint in `/etc/factory/trial.json`. Trust the Forgejo and Magpie TLS CAs in the pinned image. |
| Forgejo | Select the trusted trial repo and issue. Use anonymous read access if suitable. For a private trial select a repository-specific read-only credential and approve its lifetime. | Exact repo and HTTPS clone URL in `repositories`. For a private repo, set `username` and an absolute `token_file` in that repo entry. Store the token in a private mode `0600` regular file owned by the launcher user. |
| Magpie | Confirm the local issuer/revoker, scoped parent key, exact model and guest-facing HTTPS data-plane URL. | Host issuer executable at the configured `issuer` path, administration credentials in its protected host configuration, model and endpoint in `/etc/factory/trial.json`. |
| Woodpecker | Create and activate the protected `factory-control/launcher` repository and register the dedicated agent. Grant manual submission and control-repo writes only to the operator or equivalently trusted administrators. | Publish `woodpecker.yml` as `.woodpecker.yml` on the protected `main` branch of the control repo. Install `woodpecker-entrypoint.py` at `/opt/factory/woodpecker-entrypoint.py` with executable permissions, `launch.py` and `guest.py` beside it, and the reviewed config at `/etc/factory/trial.json`. Keep these files and their parent directories writable only by the operator. |
| Run | Select the complete commit SHA, issue reference, a new logical run ID and cold/warm classification. | `FACTORY_COMMIT`, `FACTORY_ISSUE`, `FACTORY_RUN_ID`, `FACTORY_REPO` and `FACTORY_STARTUP` in the Woodpecker manual run. These values contain no secrets. |

Verify KVM on the selected host by opening `/dev/kvm` and performing `KVM_CREATE_VM` with the host's service permissions, or by successfully starting the pinned Incus VM and verifying KVM acceleration. Device presence alone does not verify usability.

Use a credential-free VM image that already contains the Incus guest agent, `/usr/bin/python3`, `/usr/bin/git`, `/usr/local/bin/codex`, and an unprivileged `factory` user with UID/GID 1000 and a writable `/home/factory`. Pin the full fingerprint of a locally imported `virtual-machine` image in the configured Incus project. Keep the image and profile under operator control throughout the trial.

The named profile must have `security.devlxd: "false"` and may specify `limits.cpu` and `limits.memory`. It needs only a `root` disk with `type: disk`, `path: /`, a `pool`, and optional `size`, plus an `eth0` device with `type: nic`, its `name: eth0`, and the configured managed `network`. A dedicated project, isolated network and private disk are required. Apply a deny-by-default policy to guest egress and host input for both IP families. Allow guest DNS as needed and allow only the configured Forgejo and Magpie TLS destinations. Restrict the Magpie ingress to the data plane. Deny the Incus API and unrelated host services, and avoid exposing the control repo or Woodpecker to the guest. Configure `blocked_endpoints` with IP/port pairs for unrelated services known to be listening from the host, including an Incus management endpoint if enabled. The launcher probes these addresses and both required endpoints in the guest before sending any credentials. The probes supplement the network policy.

Use a dedicated Woodpecker local agent with `WOODPECKER_BACKEND=local`, `WOODPECKER_AGENT_LABELS=factory=trial` and server-side agent filter `!repo=factory-control/launcher`. Verify the agent only picks up this repository and that other agents cannot execute this definition. Keep the control repository separate from the trial repo. The local backend executes with host privileges of the agent user and pipeline submitters can supply environment variables. Limit both repository authorship and pipeline submission to operators trusted with that account. Install the launcher as root-owned files and run the agent as the dedicated `factory` user. Do not enable the trial repo on this agent.

### Host issuer contract

Configure an absolute path to an operator-owned executable. It uses the installed Magpie administration interface on the host. Its commands are:

```text
issue <logical-run-id> <one-model> <ttl-seconds>
revoke <key-id>
```

`issue` must write a single JSON object containing `id` (an opaque identifier) and `key` (the bearer secret) to stdout. Issue only a key with the supplied run ID as its name, one allowed model, limited provider accounts and a lifetime no greater than the requested TTL. Disable key issuance, admin, MCP and memory on that key. The launcher currently requests 900 seconds. If the issuer allocates a key and fails before reporting the ID, the issuer must attempt to revoke it, and the operator can look up the run ID in Magpie.

`revoke` must exit zero only once the referenced key is removed or confirmed absent. Keep the returned bearer secret out of process arguments, Woodpecker variables and logs. The launcher reads issuance stdout privately, stores only the ID in the run record, and supplies the secret to the guest on Incus exec stdin. The Codex subprocess sees the secret as its model provider environment variable.

Install the included `issue-magpie-key` at `/opt/factory/issue-magpie-key` with executable permissions to use the local Magpie CLI. In operator-owned `/etc/factory/magpie.json`, set `binary` to the absolute path of the installed `magpie` executable and `issuer_id` to the ID of the restricted parent key. Set `xdg_config_home` if needed to point the CLI at the running gateway's configuration (`magpie/` is a subdirectory). Optionally set `accounts` to the exact approved provider/account selectors for the child. Restrict the parent to the trial model and approved accounts, enable issuance on the parent, and verify the resulting scope in the live gateway. Sharing a Magpie configuration with a service account grants that account access to its contents. Use a dedicated trial instance, or supply a separate operator issuer if the active instance's configuration is too privileged for the launcher user.

The included issuer uses `magpie gateway-key issue`, sets the child scope controls, and uses `magpie gateway-key remove` on cleanup. Its output schema and controls were checked with Magpie CLI 0.1.4708 against an isolated temporary configuration.

## Manual submission

Use Woodpecker v3.20 workflow syntax. From an authenticated operator CLI, submit the control repository's protected branch. Supply the trial repo's full commit SHA independently of the control repository SHA. Keep the trial commit reachable on the Forgejo server until the guest fetches it.

```bash
woodpecker-cli pipeline create --branch main \
  --var FACTORY_RUN_ID=woodpecker-cold-01 \
  --var FACTORY_REPO=factory-trials/smoke \
  --var 'FACTORY_ISSUE=factory-trials/smoke#1' \
  --var "FACTORY_COMMIT=$trial_commit" \
  --var FACTORY_STARTUP=cold \
  factory-control/launcher
```

Check the pipeline number and status in Woodpecker. Check `/var/lib/factory/runs/woodpecker-cold-01/run.json` and `agent.log` as the launcher user. Verify the requested and checked-out SHA match, network probes passed, model output contains `FACTORY_TRIAL_OK`, the agent exited zero, and both cleanup fields confirm completion. Independently look up the recorded VM name in the Incus project and the key ID in Magpie. Correlate Magpie model usage with the run key ID using the gateway's usage view.

Record a cold run under an operator-described baseline such as a fresh host or a freshly imported image in the trial storage pool. Immediately repeat with a new run ID and `FACTORY_STARTUP=warm` using the same image, profile and commit. Report both `startup_seconds` values (VM allocation through guest agent readiness), image fingerprint, the baseline and the relevant host/service versions. The label records the submitted condition.

## Shared launcher interface

```bash
python3 factory/launch.py --config /etc/factory/trial.json \
  --run-id woodpecker-cold-01 --repo factory-trials/smoke \
  --issue 'factory-trials/smoke#1' --commit "$trial_commit" \
  --job woodpecker:factory-control/launcher:123 --startup cold
```

Any orchestrator can supply the same interface with its own `--job` value. The launcher creates a private directory named for the logical run ID, persists the VM name and allocation identity before requesting allocation, and atomically updates `run.json` through startup, checkout, issuance, execution, and teardown. It preserves agent stdout and stderr in `agent.log` and records when collection was incomplete. A duplicate logical ID fails before allocating another VM. The record includes the Incus project, ownership marker and key ID for a later reconciler. Ticket #6 owns restart reconciliation and cancellation. Inspect a failed or unfinished record before taking any manual action on the recorded VM or key.

## Signed Forgejo events

The adapter accepts Forgejo v17.0.2 issue-open and issue-label notifications at `POST /forgejo`. It validates the HMAC over the request bytes and the exact repository name against a subset of the launcher allowlist. It reads current issue labels from Forgejo. When the trigger changes from absent to present, it resolves the configured target branch to a full commit SHA. Before returning HTTP `202`, it commits the delivery UUID, body digest, issue observation and any new dispatch record in a single SQLite transaction. The ledger keeps correlation metadata and excludes issue bodies and authorization headers. A repeated delivery with the same body receives `202`. Reuse of the UUID with a different body receives `409`.

### Operator stages to confirm for the wizard

The wizard is awaiting confirmation of the ordered setup stages. Extend the existing Forgejo and Woodpecker stages and add the cutover stage after both services are ready.

| Stage | Operator action | Value destination |
| --- | --- | --- |
| Forgejo | Confirm the trusted target repo, exact trigger label, target branch, and repository-scoped API access for reading issues and branches (`read:issue` and `read:repository`). Generate a dedicated webhook secret. | `repositories`, `label`, `forgejo.url` and secret file paths in `/etc/factory/events.json`. Store API tokens and the webhook secret as private `0600` regular files owned by the adapter account or mount equivalent files from the host secret store. Use the same webhook secret in Forgejo hook settings. |
| Woodpecker | Confirm v3.20.1, activate the protected `factory-control/launcher` repo and identify its numeric Woodpecker repository ID. Issue a dedicated token for an account authorized to list and submit this control repo's manual pipelines. | `woodpecker.url`, `woodpecker.repo_id` and `woodpecker.token_file` in `/etc/factory/events.json`. Store the token in a private file as above. Keep protected `main` and the dedicated agent restrictions from the manual setup. |
| Adapter and cutover | Install `events.py` as an operator-owned file at `/opt/factory/events.py`. Set up a fixed service account, private `0700` local state directory and reverse-proxy route from Forgejo to `127.0.0.1:8765/forgejo`. Confirm a quiet interval and seed before enabling the webhook. Install supervised processes for `serve` and `dispatch` under the same adapter account. | Operator-owned `/etc/factory/events.json` from `events.example.json`, service-owned `/var/lib/factory/events`, and a repository-level Forgejo webhook. Keep ledger state on durable local storage and preserve it across restarts. |

Use a separate adapter account from the Incus launcher where practical. The adapter needs read access to the launcher config to validate repository names. Keep the VM manager and guest credentials under the launcher account. The reverse proxy should preserve the raw request body, enforce the 1 MiB body limit, and allow enough time for the adapter to commit its Forgejo reads. API endpoints accept HTTPS or local loopback HTTP.

### Quiet cutover

1. Pause issue creation and label changes on the configured repositories. Leave the webhook disabled and let any earlier changes settle. Confirm that the Forgejo service account can see every target issue, including closed issues.
2. As the adapter account, run `python3 /opt/factory/events.py --config /etc/factory/events.json seed`. The seed reads every page of existing issue-label state and creates zero runs. It refuses a second seed on the same ledger.
3. Start the supervised `serve` and `dispatch` commands with the same `--config` path. Register or enable a **Forgejo** repository webhook with JSON content type, the shared secret, and events `issues` and `issue_label`. Point it at the `/forgejo` reverse-proxy URL. Leave issue and label edits paused until the listener and webhook are ready.
4. Open a new smoke issue with the trigger label and verify the delivery received `202`. Run `python3 /opt/factory/events.py --config /etc/factory/events.json status` to find the run ID, issue, SHA, dispatch state and Woodpecker job. Open the job and check the launcher record under the same run ID. Repeat for an added label and for a removal followed by reapplication. Wait for the removal's accepted delivery before reapplying.

Forgejo v17.0.2 sent the previous label set for add, remove, reapply and clear operations in a loopback probe. The payload contained no label delta, and multiple actions shared `updated_at`. An immediate read also sometimes returned the pre-change labels. The adapter allows 250 ms for label updates to settle before it reads the issue's labels endpoint and compares with durable prior state. This delay covered the loopback race and the HTTP fixture, and a longer upstream visibility lag can still hide a transition. The HTTP fixtures exercised these cases:

| Delivery scenario | Observation |
| --- | --- |
| Duplicate UUID or unrelated label edit while the trigger remains present | The run count stays the same. |
| A stale notification arrives after an observed application | The current Forgejo label state wins, so a stale snapshot cannot reset the issue. |
| An application notification is missed but the label remains present | A subsequent eligible delivery for that issue can observe the application and record the SHA at that time. |
| Removal and reapplication both happen before any read observes the removal | The observed state stays present and the adapter cannot reconstruct a second application. The same limitation applies to a brief add followed by removal between observations. |
| Forgejo reads or the SQLite commit fail | The adapter returns `503`. Check the Forgejo delivery history and redeliver after fixing the dependency. The redelivery sees the current issue state. |

The dispatch worker uses the persisted SHA even when the target branch advances. Keep the SHA reachable in Forgejo until the guest fetches it. The control workflow still comes from its own protected `main`. If a submission response is lost, `submitting` remains durable and the worker looks up the exact `MANUAL: <run ID> @ main` marker across manual pipelines. An absent match leaves the run in `submitting` for reconciliation. This avoids resubmitting blindly after an ambiguous request or a worker crash. Ticket #6 owns the retry decision, execution recovery and cancellation. Ticket #7 can use the same dispatch request and implement `submit(run)` and `find(run_id)` for Dagu.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s factory/tests -v
```

The fixture suite exercises the signed HTTP endpoint and dispatch CLI with local Forgejo and Woodpecker HTTP stand-ins. The queued-branch test advances a real Git branch, starts the public launcher with the pipeline's variables and runs the guest checkout against the local repository in a substitute Incus environment. It verifies the recorded SHA, detached checkout and cleanup. The launcher suite also verifies retained output, network probe input, a rejected host directory device, a checkout mismatch, a duplicate run ID and partial output after timeout. Woodpecker CLI v3.20.0 passes strict lint. A local backend check with a substitute launcher executes the manual workflow with an empty workspace and filters out push events.

The adapter received all five signed deliveries from a temporary Forgejo v17.0.2 loopback service. Labeled creation and reapplication each queued one run. Unrelated edits, removal and clearing kept the run count unchanged.

On November 25, 2026, a loopback trial with Forgejo v17.0.2 and Woodpecker v3.20.1 submitted a manual pipeline with the logical run ID in its message. The same pipeline number and pending status survived a Woodpecker server restart. Starting the local agent ran a substitute launcher once with all five original manual inputs and no repository checkout on the host. The workflow uses a command step because Woodpecker passes manual variables to command steps at runtime. A live Incus boot, guest network probes and Magpie model call still require the provisioned trial host.
