# Kubernetes Workload Triage Agent

Diagnoses broken Kubernetes workloads the way a platform engineer would: it finds unhealthy pods, runs **read-only** `kubectl` checks one at a time (choosing each next check from what it has already seen), matches the evidence to an editable **runbook**, explains the root cause in plain language, drafts a **YAML patch**, and - in live mode only, after you type `yes` - applies it and **verifies** recovery.

Covers: `CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff` / `ErrImagePull`, and `Pending` (plus a few related causes in the runbook). Python 3.10+, standard library only - **no pip installs**.

## 1. Recorded demo (no Kubernetes needed)

This replays saved `kubectl` output. It is **recorded / simulated**: no cluster is contacted, nothing can be applied, so recovery is never claimed. It works even if Windows Application Control blocks `kubectl`, Docker or kind.

**Windows:** install Python 3.10+ from python.org (tick *Add Python to PATH*), extract the ZIP, double-click **`run-demo.bat`**.

Or in PowerShell / Command Prompt from the project folder:

```powershell
python triage.py --scenario all-four --set env.APP_MODE=staging
```

One failure at a time (`python triage.py --list-scenarios` lists them):

```powershell
python triage.py --scenario image-pull-backoff
python triage.py --scenario crashloop-missing-env --set env.APP_MODE=staging
python triage.py --scenario oomkilled
python triage.py --scenario pending-insufficient-cpu
```

macOS/Linux: `./run-demo.sh`. Run the tests: `python tests/test_triage.py` (or `run-tests.bat`).

Why `--set env.APP_MODE=staging`? The log says `APP_MODE` is required but cannot say what value is right, and the agent will not guess. Without `--set`, the report says exactly what to provide.

**Outputs** (in `reports/<scenario>/`; ready-made examples are included):

| File | Contents |
|---|---|
| `diagnosis-report.md` | symptoms, evidence, plain-language cause, confidence, alternatives considered, commands run and why, the patch, decision and result |
| `action-log.json` | every command, rationale, exit code and output excerpt; each entry's `source` is `recorded-fixture`, `live-kubectl` or `agent` (a decision) |
| `proposed-fixes/<deployment>.yaml` | the focused patch |
| `evidence/` | raw (secret-masked) command output |

### Sample output

```text
MODE: RECORDED / SIMULATED - replaying saved kubectl output. No cluster is contacted or changed.
[1] Detect: listing pods in namespace 'triage-lab' ...
    Unhealthy: triage-lab/leaky-worker  OOMKilled  (container 'worker' was killed for exceeding its memory limit (exit 137, 4 restarts))
[2] Investigate triage-lab/leaky-worker (OOMKilled)
    - kubectl describe pod leaky-worker-5c7d9b6f44-n8x5t -n triage-lab
    - kubectl get events -n triage-lab --field-selector involvedObject.name=... --sort-by=.lastTimestamp
    - kubectl logs leaky-worker-5c7d9b6f44-n8x5t -n triage-lab -c worker --tail=50 --previous
    - kubectl get deployment leaky-worker -n triage-lab -o json
[3] Diagnose: Container exceeded its memory limit (OOMKilled)  [rule oom-killed, confidence High]
[4] Propose: container 'worker' resources.limits.memory: 100Mi -> 200Mi
    Recorded mode: patch shown for review only.
```

## 2. Optional: live mode on a disposable local cluster

Needs Docker Desktop, `kubectl` and [kind](https://kind.sigs.k8s.io/) (or Minikube). Use a throw-away cluster. In PowerShell from the project folder:

```powershell
kind create cluster --name triage-lab
kubectl config current-context                 # should print kind-triage-lab
kubectl apply -f workloads/                    # creates namespace triage-lab + the broken Deployments
kubectl get pods -n triage-lab                 # wait ~1 minute: broken-web, crashy-api, leaky-worker, hungry-batch unhealthy

# diagnose ONE workload (read-only until you approve):
python triage.py --live --deployment crashy-api --set env.APP_MODE=staging
```

The agent shows the patch, runs a server-side dry run, then asks:

```text
Apply this change? Type 'yes' to apply [y/N]:
```

Only `yes`/`y` applies it. Anything else - Enter, `n`, a typo, Ctrl+Z/Ctrl+D - leaves the cluster unchanged and logs the declined decision. After an approved patch it runs `rollout status`, checks the pods are Running, Ready and not restarting, and says **RECOVERED** only if they are. Try the others: `--deployment broken-web`, `leaky-worker`, `hungry-batch`. Add `--diagnose-only` to never be asked.

Clean up: `kind delete cluster --name triage-lab`

Notes: `leaky-worker` pulls `polinux/stress` and `broken-web` pulls from Docker Hub, so the cluster needs internet. The `hungry-batch` fix is sized from *your* node's capacity, so it will differ from the recorded `800m`. Live reports are written to `reports/live/`.

## 3. Safety (details in `SAFETY.md`)

- Recorded mode has no code path that applies a patch.
- Live mode: one named workload, `triage-*` namespaces only, local-cluster contexts only, allowlisted read-only commands, Secrets never read, credentials masked, server-side dry run first, explicit approval with default **No**, recovery claimed only after verification.

## 4. How it works

```
detect ──> investigate ──> diagnose ──> propose ──> approve ──> apply ──> verify
get pods   describe/events  runbook     YAML patch  y/N (live)  patch     rollout status
           logs --previous  scoring     + dry run   default No  (live)    + pods Ready
           nodes/deployment + confidence
```

After each command the agent re-scores every runbook rule against the evidence so far and runs the next check from the leading rule's checklist (it skips `logs` for a container that never started, and widens the search if the evidence is inconclusive). It is deterministic and rule-based - no LLM, no network. If evidence is missing or no rule matches, the report says so and lists what to collect instead of guessing.

## 5. Teach it a new failure

Edit `runbooks/runbooks.json`. Each rule has `symptoms`, weighted regex `signals` over a named evidence source (`pod`, `describe`, `events`, `logs`, `nodes`), a `diagnostics` checklist, `plain_language` text and a `fix`. Fix kinds that draft a patch: `set_image`, `set_env`, `raise_memory_limit`, `reduce_request`; use `manual` for guidance only.

## 6. Files

| Path | Purpose |
|---|---|
| `triage.py` | command-line entry point |
| `kubetriage/` | `agent` (loop), `detect`, `kb` (runbook scoring), `patches`, `report`, `safety`, `backend` (live / recorded) |
| `runbooks/runbooks.json` | editable knowledge base (10 rules) |
| `fixtures/<scenario>/` | recorded `kubectl` output (`manifest.json` maps command -> file) |
| `workloads/` | deliberately broken manifests (+ one healthy control) for live mode |
| `reports/` | generated example reports, patches and action logs |
| `tests/` | 19 tests, including live mode against a stub `kubectl` |
| `tools/make_fixtures.py` | regenerates `fixtures/` and `workloads/` from one definition |
| `run-demo.bat`, `run-demo.sh`, `run-tests.bat` | one-click helpers |

**Recorded-output honesty:** the fixtures are hand-built to match the format and wording of real `kubectl` output for these failures; they are representative, not captured from your cluster.

## Changes from v2 of this project

v2 handled a single hard-coded image error and never applied or verified anything. This version adds step-by-step investigation, all four failure types plus six more runbook rules, confidence and missing-evidence reporting, approval with default No, dry run, post-patch verification, safety guards, secret masking, a source-tagged action log and tests. The old `--input fixtures/imagepullbackoff.json` option is replaced by `--scenario image-pull-backoff`.
