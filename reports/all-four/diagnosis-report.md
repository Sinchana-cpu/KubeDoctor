# Kubernetes diagnosis report

> **RECORDED / SIMULATED RUN.** Every command output below was replayed from saved fixture files, not read from a live cluster. Nothing was applied to any cluster, and no recovery was (or could be) verified.

- **Run:** 2026-11-19T12:51:18+00:00 (UTC) -> 2026-11-19T12:51:18+00:00
- **Mode:** recorded cluster (replay) - scenario `all-four`
- **Cluster context:** `recorded-fixture (no live cluster)`  |  **Scope:** triage-lab
- **Pods inspected:** 5  |  **Unhealthy workloads:** 4
- **Changes applied:** 0  |  **Verified recovered:** 0

## Summary

| Workload | Symptom | Root cause | Confidence | Outcome |
|---|---|---|---|---|
| `triage-lab/broken-web` | ImagePullBackOff | Image or tag does not exist in the registry | High | Fix proposed (not applied) |
| `triage-lab/crashy-api` | CrashLoopBackOff | App exits at startup because a required setting is missing | High | Fix proposed (not applied) |
| `triage-lab/leaky-worker` | OOMKilled | Container exceeded its memory limit (OOMKilled) | High | Fix proposed (not applied) |
| `triage-lab/hungry-batch` | Pending | No node has enough free CPU or memory for the pod | High | Fix proposed (not applied) |

## 1. `triage-lab/broken-web` - ImagePullBackOff - Image or tag does not exist in the registry

**Status found:** container 'web' is waiting: ImagePullBackOff (0 restarts).

### In plain language

Kubernetes tried to download the container image and the registry said it does not exist. Almost always this is a typo in the image name or tag, or a tag that was never published. The container never started, so there are no application logs to read.

*Why this usually happens:* Typos in the tag, a tag that was deleted or never pushed, or a CI pipeline that has not published the image yet.

### What the agent saw

| Evidence | From | Line |
|---|---|---|
| Pod is stuck pulling its image | pod | `symptom: ImagePullBackOff` |
| The registry answered 'not found' for this image or tag | events | `77s         Warning   Failed      pod/broken-web-6b8b94c6f7-xk2qp   Failed to pull image "nginx:99.99-definitely-missing": rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:99.99-` |
| kubelet reported a pull failure | describe | `Warning  Failed     77s (x4 over 3m)   kubelet            Failed to pull image "nginx:99.99-definitely-missing": rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:99.99-definitely` |

**Confidence: High** - matched 7 of 7 evidence points for rule `image-not-found`.

Other explanations considered and ranked lower:

- `image-pull-auth` (3/7) - Registry refused access to the image (authentication)
- `image-registry-unreachable` (3/7) - Cluster cannot reach the image registry (network/DNS)

### How the agent investigated

| # | Command | Why this command |
|---|---|---|
| 1 | `kubectl describe pod broken-web-6b8b94c6f7-xk2qp -n triage-lab` | 'describe' is the richest single view of a pod: status, last exit code, resource limits and its Events. |
| 2 | `kubectl get events -n triage-lab --field-selector involvedObject.name=broken-web-6b8b94c6f7-xk2qp --sort-by=.lastTimestamp` | Events show what the cluster itself observed (scheduling, pulling, restarting) in time order. |
| 3 | `kubectl get deployment broken-web -n triage-lab -o json` | Read the owning Deployment so a patch can be drafted against its real container spec. |

### Proposed fix

Point the Deployment at an image tag that exists.

| Field | Before | After |
|---|---|---|
| container 'web' image | `nginx:99.99-definitely-missing` | `nginx:1.27-alpine` |

```yaml
spec:
  template:
    spec:
      containers:
        - name: web
          image: "nginx:1.27-alpine"
```

> New image comes from the known-good tag from the runbook. Confirm it matches what your application needs.

Saved as `proposed-fixes/broken-web.yaml`. To apply it yourself:

```bash
kubectl patch deployment broken-web -n triage-lab --type strategic --patch-file proposed-fixes/broken-web.yaml
```

### Decision and result

- **Approval:** not applicable - recorded mode never applies patches
- **Change applied:** no
- **Verification:** not run - nothing was applied, so recovery is NOT claimed. After you apply the fix on a real cluster, confirm with `kubectl rollout status deployment/broken-web -n triage-lab` and `kubectl get pods -n triage-lab`; recovery means `1/1 Running` with a stable restart count.

> **Learn:** Read 'kubectl describe pod' from the bottom up: the last few Events usually name the exact problem.

## 2. `triage-lab/crashy-api` - CrashLoopBackOff - App exits at startup because a required setting is missing

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

## 3. `triage-lab/leaky-worker` - OOMKilled - Container exceeded its memory limit (OOMKilled)

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

## 4. `triage-lab/hungry-batch` - Pending - No node has enough free CPU or memory for the pod

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
