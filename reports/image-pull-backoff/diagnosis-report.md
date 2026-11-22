# Kubernetes diagnosis report

> **RECORDED / SIMULATED RUN.** Every command output below was replayed from saved fixture files, not read from a live cluster. Nothing was applied to any cluster, and no recovery was (or could be) verified.

- **Run:** 2026-11-19T12:42:40+00:00 (UTC) -> 2026-11-19T12:42:40+00:00
- **Mode:** recorded cluster (replay) - scenario `image-pull-backoff`
- **Cluster context:** `recorded-fixture (no live cluster)`  |  **Scope:** triage-lab
- **Pods inspected:** 1  |  **Unhealthy workloads:** 1
- **Changes applied:** 0  |  **Verified recovered:** 0

## Summary

| Workload | Symptom | Root cause | Confidence | Outcome |
|---|---|---|---|---|
| `triage-lab/broken-web` | ImagePullBackOff | Image or tag does not exist in the registry | High | Fix proposed (not applied) |

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

## How this report was produced

- Every cluster read used the read-only allowlist (`get`, `describe`, `logs`, `top`, `rollout status`). Secrets are never read, and credentials in output are masked.
- Recorded mode makes no cluster changes at all. In live mode the only change possible is one `kubectl patch deployment` on the named workload in a lab namespace, preceded by a server-side dry run and applied only after you type `yes` (default is No).
- Recovery is reported only if, after an approved patch, the rollout completes and the pods are Running, Ready and not restarting.
- The diagnosis is rule-based: it matches evidence against the runbook in `runbooks/runbooks.json`. It is a strong lead, not a proof - check the evidence above.
- Every command, reason and result is in `action-log.json`; raw (redacted) outputs are in `evidence/`.
