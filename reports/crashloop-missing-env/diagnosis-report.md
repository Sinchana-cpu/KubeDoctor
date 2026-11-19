# Kubernetes diagnosis report

> **RECORDED / SIMULATED RUN.** Every command output below was replayed from saved fixture files, not read from a live cluster. Nothing was applied to any cluster, and no recovery was (or could be) verified.

- **Run:** 2026-11-19T12:42:40+00:00 (UTC) -> 2026-11-19T12:42:40+00:00
- **Mode:** recorded cluster (replay) - scenario `crashloop-missing-env`
- **Cluster context:** `recorded-fixture (no live cluster)`  |  **Scope:** triage-lab
- **Pods inspected:** 1  |  **Unhealthy workloads:** 1
- **Changes applied:** 0  |  **Verified recovered:** 0

## Summary

| Workload | Symptom | Root cause | Confidence | Outcome |
|---|---|---|---|---|
| `triage-lab/crashy-api` | CrashLoopBackOff | App exits at startup because a required setting is missing | High | Fix proposed (not applied) |

## 1. `triage-lab/crashy-api` - CrashLoopBackOff - App exits at startup because a required setting is missing

**Status found:** container 'api' is waiting: CrashLoopBackOff (5 restarts, last exit code 1).

### In plain language

The program started, checked its configuration, found something required missing, and quit on purpose. Kubernetes keeps restarting it, waiting longer each time - that waiting is the 'BackOff' in CrashLoopBackOff.

*Why this usually happens:* An environment variable or config value that exists in another environment was never added to this Deployment.

### What the agent saw

| Evidence | From | Line |
|---|---|---|
| Container keeps crashing and restarting | pod | `symptom: CrashLoopBackOff` |
| The app's own log says a required setting is missing | logs | `FATAL: required environment variable APP_MODE is not set` |
| Container exited with code 1 (generic application error) | describe | `Exit Code:    1` |

**Confidence: High** - matched 8 of 8 evidence points for rule `crash-missing-config`.

Other explanations considered and ranked lower:

- `crash-generic` (5/5) - Application crashes at startup (cause not identified)
- `crash-bad-command` (2/10) - Container command or entrypoint cannot run

### How the agent investigated

| # | Command | Why this command |
|---|---|---|
| 1 | `kubectl describe pod crashy-api-7f9d6c5b8d-q4w8z -n triage-lab` | 'describe' is the richest single view of a pod: status, last exit code, resource limits and its Events. |
| 2 | `kubectl get events -n triage-lab --field-selector involvedObject.name=crashy-api-7f9d6c5b8d-q4w8z --sort-by=.lastTimestamp` | Events show what the cluster itself observed (scheduling, pulling, restarting) in time order. |
| 3 | `kubectl logs crashy-api-7f9d6c5b8d-q4w8z -n triage-lab -c api --tail=50 --previous` | The container restarts, so the log of the PREVIOUS run holds the crash message. |
| 4 | `kubectl get deployment crashy-api -n triage-lab -o json` | Read the Deployment's current env so the patch adds only what is missing. |

### Proposed fix

Add the missing environment variable to the container.

| Field | Before | After |
|---|---|---|
| container 'api' env APP_MODE | `(not set)` | `staging` |

```yaml
spec:
  template:
    spec:
      containers:
        - name: api
          env:
            - name: APP_MODE
              value: staging
```

> The value was supplied by you; the agent did not choose it.

Saved as `proposed-fixes/crashy-api.yaml`. To apply it yourself:

```bash
kubectl patch deployment crashy-api -n triage-lab --type strategic --patch-file proposed-fixes/crashy-api.yaml
```

### Decision and result

- **Approval:** not applicable - recorded mode never applies patches
- **Change applied:** no
- **Verification:** not run - nothing was applied, so recovery is NOT claimed. After you apply the fix on a real cluster, confirm with `kubectl rollout status deployment/crashy-api -n triage-lab` and `kubectl get pods -n triage-lab`; recovery means `1/1 Running` with a stable restart count.

> **Learn:** For a crash loop, run 'kubectl logs <pod> --previous': plain 'logs' may show the fresh, still-empty restart.

## How this report was produced

- Every cluster read used the read-only allowlist (`get`, `describe`, `logs`, `top`, `rollout status`). Secrets are never read, and credentials in output are masked.
- Recorded mode makes no cluster changes at all. In live mode the only change possible is one `kubectl patch deployment` on the named workload in a lab namespace, preceded by a server-side dry run and applied only after you type `yes` (default is No).
- Recovery is reported only if, after an approved patch, the rollout completes and the pods are Running, Ready and not restarting.
- The diagnosis is rule-based: it matches evidence against the runbook in `runbooks/runbooks.json`. It is a strong lead, not a proof - check the evidence above.
- Every command, reason and result is in `action-log.json`; raw (redacted) outputs are in `evidence/`.
