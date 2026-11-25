# Woodpecker recovery drill

Use the same scenarios and host commands for Dagu. Substitute its job lookup and queue/stop controls while retaining the logical run ID, issue, recorded SHA, launcher config, VM image and Magpie model.

## Host commands

Run the launcher commands as the launcher service account. Run the adapter commands as the adapter service account.

```bash
python3 /opt/factory/events.py --config /etc/factory/events.json status
python3 /opt/factory/events.py --config /etc/factory/events.json dispatch --once
python3 /opt/factory/events.py --config /etc/factory/events.json retry --run-id "$run_id"

python3 /opt/factory/launch.py --config /etc/factory/trial.json reconcile
python3 /opt/factory/launch.py --config /etc/factory/trial.json reconcile --run-id "$run_id"
python3 /opt/factory/launch.py --config /etc/factory/trial.json cancel --run-id "$run_id"
```

Run the all-runs reconciliation when Incus and Magpie are ready after a host restart and periodically under the launcher account. It skips active runs. A single-run reconciliation waits for its host lock. Both retry unconfirmed cleanup and exit nonzero if any selected recovery cannot be confirmed. Keep the original run root, Incus project, and issuer settings available until existing records have settled.

For operator cancellation, start with the host `cancel` command, which records a durable request even if the job is still queued. It waits for host teardown or returns nonzero if cleanup remains unconfirmed. Then cancel the still-active or queued pipeline in Woodpecker so its scheduler state reflects the request. Woodpecker v3.20.1 exposes `POST /api/repos/{repo_id}/pipelines/{pipeline_number}/cancel`. A late agent invocation finds the host cancellation marker and does zero allocation. Direct orchestrator cancellation can stop the launcher process, so reconcile on the host after any stop issued from the orchestrator UI.

`run.json` preserves the original job, inputs and allocation identity. `state=cleanup_pending` retains the proposed `outcome`, failure reason and last verified cleanup state. `cancelled` and `interrupted` require that cleanup be confirmed. `agent.log` retains streamed output. `output_complete=false` or a null `agent_exit_status` records a missing completion result, including a killed launcher whose guest had already stopped. Replays of a recorded ID do zero additional execution. Deliberate new work needs a new logical run.

If submission is ambiguous, let the dispatcher search for the exact manual marker. When it still reports `submitting`, verify Woodpecker has settled the original request before using the same-ID `retry` command. Retry checks the marker under the dispatcher lock before sending anything. A missing remote marker cannot prove that an in-flight request will never arrive. If multiple jobs match, investigate the scheduler records and cancel any extra jobs. Host locking and the durable run directory prevent a second guest even if two jobs are admitted.

## Reproduce the fixture results

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s factory/tests -v
```

| Scenario | Observed result |
| --- | --- |
| Submit accepted, HTTP response lost | A later dispatch found the exact marker and retained the same run ID and Woodpecker job. |
| Submit refused, then repeated operator retry and launcher replay | Retry admitted one pipeline under the original ID and SHA. The first launcher invocation allocated once. The replay left the host record unchanged and the fixture inventory empty. |
| Launcher killed during allocation | Reconciliation waited for an outstanding host client or server task, then confirmed VM removal and persisted `interrupted`. |
| Launcher killed during execution, guest already gone, key removal temporarily unavailable | The host retained partial output and the original run mapping. First recovery recorded `cleanup_pending` with an unconfirmed key. Repeated recovery revoked it and persisted `interrupted`. |
| Issuer created a key and lost its response | Lookup by exact run name recovered the key ID and revocation removed it. |
| Provider failure and timeout | The failure retained its exit status and output. The timeout retained partial output and a null exit status. Both confirmed VM removal and key revocation. |
| Cancellation while queued and while executing | A queued cancellation fenced a later launch. An active cancellation retained partial output and confirmed guest and key removal. |

The suite uses local HTTP Forgejo and Woodpecker stand-ins and command-level Incus and Magpie fixtures. The failure case runs the guest wrapper with a substitute Codex command. The queue restart was also observed against real Forgejo v17.0.2 and Woodpecker v3.20.1 on loopback on November 25, 2026. Pipeline 1 stayed pending and discoverable by its exact manual marker across server restart. The local agent later ran a substitute launcher once with the original inputs, including the recorded commit. The bundled Magpie issuer was independently checked against Magpie 0.1.4708 in temporary local configuration: issue, exact-name lookup, revoke, repeated revoke and empty lookup passed.

## Verify on the selected KVM host

1. Use the read-only README smoke repository. Capture Forgejo branch refs and all pull request IDs before starting. Submit a signed issue-label trigger. Save the adapter row and the Woodpecker pipeline number, state, and manual marker.
2. Keep the agent stopped while the accepted job is pending. Restart the persisted Woodpecker server, look up the same marker and number, then start the agent. Check that the launcher used the original SHA even if the target branch advances before execution. Record any deliberate advance in the Forgejo baseline.
3. Use a fresh logical run per case. Simulate a lost submission response by dropping the response after Woodpecker accepts the request. Re-run dispatch and retry. For allocation and execution interruption, kill the host launcher process after the VM or partial log appears, then run `reconcile`. Repeat with the guest already stopped. For a provider failure use a trial account whose model route returns an error. For timeout use the trial timeout setting. For operator cancellation run the host command while the guest is active, then observe the pipeline state.
4. For each run, save its `run.json` and available `agent.log`. Compare the recorded issue, commit and job with Forgejo and the scheduler. Use `incus list local: --project "$project" --format=json` to verify that the recorded VM and other trial VMs are absent after the drill. Verify the key ID is absent from the Magpie key inventory and look up retained usage by that ID, recording whether a provider request reached the data plane. Preserve timestamps and request outcomes without recording bearer secrets.
5. Re-read Forgejo branch refs and pull request IDs and compare with the baseline. The trial runs a read-only marker invocation, so expect no new refs or pull requests. For each run, keep the result and service versions with its logical run ID.

Real Incus teardown and Magpie data-plane usage remain to be collected on the selected trial host. The fixtures establish the recovery behavior and the real queue probe establishes scheduler persistence with the substitute launcher.
