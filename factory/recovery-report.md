# Factory recovery drill and comparison

Run the same scenarios for Woodpecker and Dagu with the same launcher config, VM image and Magpie model. Correlate each scheduler job with its logical run ID, issue and recorded SHA.

## Host commands

Run the launcher commands as the launcher service account. Run the adapter commands as the adapter service account.

```bash
python3 /opt/factory/events.py --config /etc/factory/events.json status
python3 /opt/factory/events.py --config /etc/factory/events.json dispatch --once
python3 /opt/factory/events.py --config /etc/factory/events.json retry --run-id "$run_id"
python3 /opt/factory/events.py --config /etc/factory/events-dagu.json cancel --run-id "$run_id"

python3 /opt/factory/launch.py --config /etc/factory/trial.json reconcile
python3 /opt/factory/launch.py --config /etc/factory/trial.json reconcile --run-id "$run_id"
python3 /opt/factory/launch.py --config /etc/factory/trial.json cancel --run-id "$run_id"
```

Run the all-runs reconciliation when Incus and Magpie are ready after a host restart and periodically under the launcher account. It skips active runs. A single-run reconciliation waits for its host lock. Both retry unconfirmed cleanup and exit nonzero if any selected recovery cannot be confirmed. Keep the original run root, Incus project, and issuer settings available until existing records have settled.

For operator cancellation, start with the launcher `cancel` command. It records a durable request even if the job is still queued, waits for host teardown and returns nonzero if cleanup remains unconfirmed. A late invocation finds the host cancellation marker and does zero allocation. Reconcile the host after any stop issued directly from a scheduler UI.

- For Woodpecker, cancel a still-active or queued pipeline using its UI or `POST /api/repos/{repo_id}/pipelines/{pipeline_number}/cancel`.
- For Dagu, follow with the adapter `cancel` command above. It persists `cancelling` before calling Dagu. It dequeues a queued job with `GET /api/v1/dag-runs/factory-trial/{run_id}/dequeue` and stops a running job with `POST /api/v1/dag-runs/factory-trial/{run_id}/stop`. A later dispatch pass checks any pending cancellation, including a lost dequeue response. Once the job is terminal or lookup returns `404`, the adapter retains `cancelled` and any known job reference and disables submission retries for that ID. The adapter state describes dispatch cancellation. Check Dagu for the actual outcome and the launcher record for host cleanup. If the original enqueue was ambiguous and still in flight, repeat adapter `cancel` after it settles to check for late arrival.

`run.json` preserves the original job, inputs and allocation identity. `state=cleanup_pending` retains the proposed `outcome`, failure reason and last verified cleanup state. `cancelled` and `interrupted` require that cleanup be confirmed. `agent.log` retains streamed output. `output_complete=false` or a null `agent_exit_status` records a missing completion result, including a killed launcher whose guest had already stopped. Replays of a recorded ID do zero additional execution. Deliberate new work needs a new logical run.

If submission is ambiguous, let the dispatcher look up the exact Woodpecker marker or Dagu run ID. When it still reports `submitting`, verify the orchestrator has settled the original request before using the same-ID `retry` command. Retry checks for the existing job under the dispatcher lock. A missing lookup cannot establish that an in-flight request has settled. Dagu v2.27.0 returns `500` when enqueue receives a duplicate ID. The adapter reconciles that response with a by-ID lookup. If multiple Woodpecker jobs match, investigate the scheduler records and cancel any extra jobs. Host locking and the durable run directory prevent a second guest even if two jobs are admitted.

## Reproduce the fixture results

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s factory/tests -v
```

| Scenario | Observed result |
| --- | --- |
| Submit accepted, HTTP response lost | A later dispatch recovered the same logical run through Woodpecker's exact marker or Dagu's run ID. |
| Submit refused, then repeated operator retry and launcher replay | Each backend admitted one job under the original ID and SHA. The first launcher invocation allocated once. The replay left the host record unchanged and the fixture inventory empty. |
| Launcher killed during allocation | Reconciliation waited for an outstanding host client or server task, then confirmed VM removal and persisted `interrupted`. |
| Launcher killed during execution, guest already gone, key removal temporarily unavailable | The host retained partial output and the original run mapping. First recovery recorded `cleanup_pending` with an unconfirmed key. Repeated recovery revoked it and persisted `interrupted`. |
| Issuer created a key and lost its response | Lookup by exact run name recovered the key ID and revocation removed it. |
| Provider failure and timeout | The failure retained its exit status and output. The timeout retained partial output and a null exit status. Both confirmed VM removal and key revocation. |
| Cancellation while queued and while executing | A queued cancellation fenced a later launch. An active launcher cancellation retained partial output and confirmed guest and key removal. The Dagu fixture also recovered a lost dequeue response and retained a cancelled ledger row after remote history disappeared. Running Dagu stop retained an `aborted` run. |

The suite uses local HTTP Forgejo, Woodpecker and Dagu stand-ins and command-level Incus and Magpie fixtures. The failure case runs the guest wrapper with a substitute Codex command. The bundled Magpie issuer was independently checked against Magpie 0.1.4708 in a temporary local configuration. Issue, exact-name lookup, revoke, repeated revoke and empty lookup passed.

## Observed comparison

The November 25, 2026 loopback probes used Forgejo v17.0.2 with Woodpecker v3.20.1 and a separate Dagu v2.27.0 instance. The Dagu workflow probe used signed fixture events and the pinned workflow and entrypoint staged in a temporary directory. The entrypoint ran a substitute launcher and used a real Dagu operator REST API key.

| Dimension | Woodpecker v3.20.1 | Dagu v2.27.0 |
| --- | --- | --- |
| Setup exercised | Forgejo OAuth, a protected control repository, server and dedicated local agent. | One Dagu binary with API and scheduler modes, an operator-installed workflow, local state and a dedicated operator REST key. |
| Queue and recovery | Pipeline 1 remained `pending` after server restart. Exact manual message lookup returned the same pipeline number. Starting the agent invoked the substitute once with the original inputs. | The caller-supplied ID stayed `queued` across API restart. After an intentionally dropped enqueue response, the adapter found the same job. A deliberate duplicate returned HTTP `500`. Starting the scheduler invoked the substitute once for that ID with the original SHA after a branch-head advance in the fixture. |
| Run visibility | The pipeline API exposed pending and successful status, the manual marker and pipeline number. The local agent executed the workflow without a host checkout. | The API exposed queued, running, succeeded, failed and aborted states. A substitute failure yielded a retained step log. The workflow ran without a host checkout. |
| Cancellation and residual state | The shared launcher fixtures confirmed cleanup and late-launch fencing. The loopback Woodpecker probe measured queue persistence and completion. Live scheduler cancellation and resource inventory still need a paired drill. | The real API stopped a running workflow and preserved `aborted` history. Dequeue removed the queued run from remote history and lookup returned `404`. The adapter retained the cancellation and skipped retry. The substitute allocated no VM or key. |

**Provisional candidate: Dagu.** The observed setup needed fewer service integrations, and caller-owned IDs gave direct recovery after a dropped response. Dagu's dequeue behavior makes the adapter ledger essential for queued-cancellation history. Choose a final cutover candidate after both orchestrators finish the paired drill on the selected host, including real Incus cold and warm runs, Magpie usage and revocation, interruption, scheduler cancellation, and independent inventory checks.

## Verify on the selected KVM host

1. Use the read-only README smoke repository. Capture Forgejo branch refs and all pull request IDs before starting. Submit a signed issue-label trigger. Save the adapter row and scheduler job identity and state. For Woodpecker retain the pipeline number and manual marker. For Dagu retain the `factory-trial` run ID and status or dequeue result.
2. Keep the Woodpecker agent or Dagu scheduler stopped while the accepted job is queued. Restart the persisted service, look up the same job, then resume execution. Check that the launcher used the original SHA even if the target branch advances before execution. Record any deliberate advance in the Forgejo baseline.
3. Use a fresh logical run per case. Simulate a lost submission response by dropping the response after the scheduler accepts it. Re-run dispatch and retry. For allocation and execution interruption, kill the host launcher process after the VM or partial log appears, then run `reconcile`. Repeat with the guest already stopped. For a provider failure use a trial account whose model route returns an error. For timeout use the trial timeout setting. Exercise both queued cancellation and cancellation while the guest is active. Record the host and scheduler outcome for each.
4. For each run, save its `run.json` and available `agent.log`. Compare the recorded issue, commit and job with Forgejo and the scheduler. Use `incus list local: --project "$project" --format=json` to verify that the recorded VM and other trial VMs are absent after the drill. Verify the key ID is absent from the Magpie key inventory and look up retained usage by that ID, recording whether a provider request reached the data plane. Preserve timestamps and request outcomes without recording bearer secrets.
5. Re-read Forgejo branch refs and pull request IDs and compare with the baseline. The trial runs a read-only marker invocation, so expect no new refs or pull requests. For each run, keep the result and service versions with its logical run ID.

Real Incus teardown and Magpie data-plane usage remain to be collected on the selected trial host. The fixtures establish the recovery behavior and the real queue probe establishes scheduler persistence with the substitute launcher.
