# Kubernetes diagnosis report

> **RECORDED / SIMULATED RUN.** Every command output below was replayed from saved fixture files, not read from a live cluster. Nothing was applied to any cluster, and no recovery was (or could be) verified.

- **Run:** 2026-11-19T12:42:40+00:00 (UTC) -> 2026-11-19T12:42:40+00:00
- **Mode:** recorded cluster (replay) - scenario `pending-insufficient-cpu`
- **Cluster context:** `recorded-fixture (no live cluster)`  |  **Scope:** triage-lab
- **Pods inspected:** 1  |  **Unhealthy workloads:** 1
- **Changes applied:** 0  |  **Verified recovered:** 0

## Summary

| Workload | Symptom | Root cause | Confidence | Outcome |
|---|---|---|---|---|
| `triage-lab/hungry-batch` | Pending | No node has enough free CPU or memory for the pod | High | Fix proposed (not applied) |

## 1. `triage-lab/hungry-batch` - Pending - No node has enough free CPU or memory for the pod

**Status found:** pod cannot be scheduled: 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod..

### In plain language

The pod asks for more CPU or memory (its 'request') than any node can provide, so Kubernetes leaves it Pending. Nothing is wrong inside the container - it was never placed anywhere.

*Why this usually happens:* A request far larger than the machine (often a unit typo such as 64 cores instead of 64m), or a cluster that is simply full.

### What the agent saw

| Evidence | From | Line |
|---|---|---|
| The scheduler could not place the pod | events | `2m          Warning   FailedScheduling   pod/hungry-batch-84c6f5d7b9-v6m2c   0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.` |
| Scheduler says a node lacks the requested resources | events | `2m          Warning   FailedScheduling   pod/hungry-batch-84c6f5d7b9-v6m2c   0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.` |
| Pod condition PodScheduled=False (Unschedulable) | pod | `"reason": "Unschedulable",` |

**Confidence: High** - matched 8 of 8 evidence points for rule `pending-insufficient-resources`.

Other explanations considered and ranked lower:

- `pending-scheduling-constraints` (3/8) - Scheduling rules (taints, selectors, affinity) exclude every node
- `pending-storage` (3/8) - Pod is waiting for a volume (PersistentVolumeClaim)

### How the agent investigated

| # | Command | Why this command |
|---|---|---|
| 1 | `kubectl describe pod hungry-batch-84c6f5d7b9-v6m2c -n triage-lab` | 'describe' is the richest single view of a pod: status, last exit code, resource limits and its Events. |
| 2 | `kubectl get events -n triage-lab --field-selector involvedObject.name=hungry-batch-84c6f5d7b9-v6m2c --sort-by=.lastTimestamp` | Events show what the cluster itself observed (scheduling, pulling, restarting) in time order. |
| 3 | `kubectl get nodes -o json` | Compare the pod's request against what the nodes can actually offer. |
| 4 | `kubectl get deployment hungry-batch -n triage-lab -o json` | Read the container's current resource requests. |

### Proposed fix

Lower the resource request to a size a node can fit.

| Field | Before | After |
|---|---|---|
| container 'batch' resources.requests.cpu | `64` | `800m` |

```yaml
spec:
  template:
    spec:
      containers:
        - name: batch
          resources:
            requests:
              cpu: "800m"
```

> This size is a starting point derived from node capacity, not a measurement of what the app needs. Check the units too: '500m' is half a core, '500' is five hundred cores.

Saved as `proposed-fixes/hungry-batch.yaml`. To apply it yourself:

```bash
kubectl patch deployment hungry-batch -n triage-lab --type strategic --patch-file proposed-fixes/hungry-batch.yaml
```

### Decision and result

- **Approval:** not applicable - recorded mode never applies patches
- **Change applied:** no
- **Verification:** not run - nothing was applied, so recovery is NOT claimed. After you apply the fix on a real cluster, confirm with `kubectl rollout status deployment/hungry-batch -n triage-lab` and `kubectl get pods -n triage-lab`; recovery means `1/1 Running` with a stable restart count.

> **Learn:** Requests are what the scheduler reserves; limits are what the kernel enforces. Pending + 'Insufficient cpu' is about requests.

## How this report was produced

- Every cluster read used the read-only allowlist (`get`, `describe`, `logs`, `top`, `rollout status`). Secrets are never read, and credentials in output are masked.
- Recorded mode makes no cluster changes at all. In live mode the only change possible is one `kubectl patch deployment` on the named workload in a lab namespace, preceded by a server-side dry run and applied only after you type `yes` (default is No).
- Recovery is reported only if, after an approved patch, the rollout completes and the pods are Running, Ready and not restarting.
- The diagnosis is rule-based: it matches evidence against the runbook in `runbooks/runbooks.json`. It is a strong lead, not a proof - check the evidence above.
- Every command, reason and result is in `action-log.json`; raw (redacted) outputs are in `evidence/`.
