# Safety notes

**Recorded mode** (`--scenario ...`, `run-demo.bat`)
- Replays saved output from `fixtures/`. It does not contact any cluster and **cannot** apply a patch: the recorded backend has no apply function at all.
- Every report from this mode is stamped *RECORDED / SIMULATED*, and every action-log entry says `"source": "recorded-fixture"`.
- Because nothing is applied, recovery is never claimed.

**Live mode** (`--live`) - only for a disposable kind/Minikube cluster
- Works on **one named workload** (`--deployment`) in a **dedicated lab namespace** (names matching `triage-*`; anything else needs `--allow-namespace`, and you should only do that for a throw-away namespace).
- Refuses to apply when the kubectl context is not a local lab cluster (`kind-*`, `minikube`, `docker-desktop`, `k3d-*`, `rancher-desktop`) unless you pass `--allow-any-context`. Diagnosis stays read-only either way.
- Needs no cluster-admin rights: reading pods/events/logs/nodes in the namespace plus `patch` on that one Deployment is enough.
- Diagnostics use an allowlist: `get`, `describe`, `logs`, `top`, `rollout status|history`. Anything else (delete, exec, apply, edit...) is rejected in code. **Secrets are never read.** Tokens, passwords and keys in output are masked before they reach reports.
- The only change possible is one `kubectl patch deployment` (strategic merge). A server-side `--dry-run=server` runs first, the patch is shown, and you are asked `Apply this change? [y/N]`. **Default is No:** Enter, `n`, an unclear answer, or end-of-input all leave the cluster unchanged, and the decision is logged.
- After an approved patch the agent waits for `kubectl rollout status`, then checks every pod is Running, Ready and not restarting. Only if those pass does it say **RECOVERED**. Otherwise it says NOT RECOVERED and prints the undo command (`kubectl rollout undo ...`).
- The agent never guesses values it cannot know (for example an environment variable's value): you supply them with `--set`.

**Limits to keep in mind**
- The diagnosis is rule-based pattern matching against `runbooks/runbooks.json`. It reports a confidence level and says what evidence is missing; treat it as a strong lead, not proof.
- Proposed values (a known-good image tag, a doubled memory limit, a CPU request sized from node capacity) are starting points. Check them against your own requirements.
- The live path is tested against a stub `kubectl` (`tests/fake_kubectl.py`), not a real cluster, in the environment this project was built in. Try it on your own kind cluster before relying on it.
