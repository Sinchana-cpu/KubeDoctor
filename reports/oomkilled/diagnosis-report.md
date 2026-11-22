# Kubernetes diagnosis report

> **RECORDED / SIMULATED RUN.** Every command output below was replayed from saved fixture files, not read from a live cluster. Nothing was applied to any cluster, and no recovery was (or could be) verified.

- **Run:** 2026-11-19T12:42:40+00:00 (UTC) -> 2026-11-19T12:42:40+00:00
- **Mode:** recorded cluster (replay) - scenario `oomkilled`
- **Cluster context:** `recorded-fixture (no live cluster)`  |  **Scope:** triage-lab
- **Pods inspected:** 1  |  **Unhealthy workloads:** 1
- **Changes applied:** 0  |  **Verified recovered:** 0

## Summary

| Workload | Symptom | Root cause | Confidence | Outcome |
|---|---|---|---|---|
| `triage-lab/leaky-worker` | OOMKilled | Container exceeded its memory limit (OOMKilled) | High | Fix proposed (not applied) |

## 1. `triage-lab/leaky-worker` - OOMKilled - Container exceeded its memory limit (OOMKilled)

**Status found:** container 'worker' was killed for exceeding its memory limit (exit 137, 4 restarts).

### In plain language

The container used more memory than its limit allows, so the Linux kernel killed it instantly (exit code 137). There is usually no error message, because the process is killed before it can say anything.

*Why this usually happens:* The limit is too low for the real workload, or the app has a memory leak or loads too much data.

### What the agent saw

| Evidence | From | Line |
|---|---|---|
| Kubernetes recorded the last exit as OOMKilled | pod | `symptom: OOMKilled` |
| 'describe' shows the last termination reason OOMKilled | describe | `Reason:       OOMKilled` |
| Exit code 137 = killed by SIGKILL (the kernel's out-of-memory kill) | describe | `Exit Code:    137` |

**Confidence: High** - matched 9 of 9 evidence points for rule `oom-killed`.

### How the agent investigated

| # | Command | Why this command |
|---|---|---|
| 1 | `kubectl describe pod leaky-worker-5c7d9b6f44-n8x5t -n triage-lab` | 'describe' is the richest single view of a pod: status, last exit code, resource limits and its Events. |
| 2 | `kubectl get events -n triage-lab --field-selector involvedObject.name=leaky-worker-5c7d9b6f44-n8x5t --sort-by=.lastTimestamp` | Events show what the cluster itself observed (scheduling, pulling, restarting) in time order. |
| 3 | `kubectl logs leaky-worker-5c7d9b6f44-n8x5t -n triage-lab -c worker --tail=50 --previous` | The previous run's log shows what the app was doing when it was killed (often just stops, with no error). |
| 4 | `kubectl get deployment leaky-worker -n triage-lab -o json` | Read the current memory limit so the new value can be computed from it. |

### Proposed fix

Raise the container's memory limit (doubled, capped).

| Field | Before | After |
|---|---|---|
| container 'worker' resources.limits.memory | `100Mi` | `200Mi` |

```yaml
spec:
  template:
    spec:
      containers:
        - name: worker
          resources:
            limits:
              memory: "200Mi"
```

> A larger limit fixes 'too small' but only delays the kill if the app leaks memory. Measure with 'kubectl top pod' when you can.

Saved as `proposed-fixes/leaky-worker.yaml`. To apply it yourself:

```bash
kubectl patch deployment leaky-worker -n triage-lab --type strategic --patch-file proposed-fixes/leaky-worker.yaml
```

### Decision and result

- **Approval:** not applicable - recorded mode never applies patches
- **Change applied:** no
- **Verification:** not run - nothing was applied, so recovery is NOT claimed. After you apply the fix on a real cluster, confirm with `kubectl rollout status deployment/leaky-worker -n triage-lab` and `kubectl get pods -n triage-lab`; recovery means `1/1 Running` with a stable restart count.

> **Learn:** Exit code 137 with Reason OOMKilled = memory limit hit. Raising the limit is a fix for 'too small', not for a leak.

## How this report was produced

- Every cluster read used the read-only allowlist (`get`, `describe`, `logs`, `top`, `rollout status`). Secrets are never read, and credentials in output are masked.
- Recorded mode makes no cluster changes at all. In live mode the only change possible is one `kubectl patch deployment` on the named workload in a lab namespace, preceded by a server-side dry run and applied only after you type `yes` (default is No).
- Recovery is reported only if, after an approved patch, the rollout completes and the pods are Running, Ready and not restarting.
- The diagnosis is rule-based: it matches evidence against the runbook in `runbooks/runbooks.json`. It is a strong lead, not a proof - check the evidence above.
- Every command, reason and result is in `action-log.json`; raw (redacted) outputs are in `evidence/`.
