# KubeDoctor — Kubernetes Workload Triage

KubeDoctor investigates unhealthy Kubernetes workloads using observed pod state, events, `describe`, previous container logs, node capacity and Deployment configuration. It ranks causes against an editable runbook, explains the matching evidence, and drafts a focused YAML patch. The browser application adds a workload health overview, a ranked fix queue, a tabbed investigation view, a log analyzer, a runbook browser, saved history and a command palette (Ctrl+K).

The web app runs in **replay mode**. It reads saved `kubectl` output from `fixtures/`, makes no cluster connection, and never applies a patch. The top bar shows the active mode. CPU/memory metrics and recovery verification are not shown because the recordings do not contain them.

## Start on Windows

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During setup, enable **Add Python to PATH**.
2. Extract the ZIP and open the extracted `k8s-workload-triage-agent` folder in VS Code.
3. Double-click `run-web.bat` (or open a terminal in the folder and run `python webapp.py`).
4. Open [http://127.0.0.1:8765](http://127.0.0.1:8765).
5. Choose a snapshot in the top bar and select **Run investigation**. Open any finding to see the diagnosis, raw evidence, the read-only commands that were run, and the fix (copy or download the patch file). Use **Download report** for the Markdown report, **Export JSON** for the raw results, or **Print** for a PDF.

The web app and recorded triage engine use only Python's standard library. There are no npm steps, paid services, API keys or external runtime dependencies.

To run the command-line demo instead, double-click `run-demo.bat` or run:

```powershell
python triage.py --scenario all-four --set env.APP_MODE=staging
```

Available demo scenarios:

```powershell
python triage.py --list-scenarios
python triage.py --scenario image-pull-backoff
python triage.py --scenario crashloop-missing-env --set env.APP_MODE=staging
python triage.py --scenario oomkilled
python triage.py --scenario pending-insufficient-cpu
```

## Features

- **Evidence-driven diagnosis:** a deterministic agent chooses follow-up checks based on evidence collected so far; it reports missing evidence and low confidence rather than inventing a cause.
- **Four reproducible scenarios:** invalid image reference, missing startup configuration, OOMKilled and Pending due to insufficient CPU, with a healthy control in the combined scenario.
- **Overview:** workload tiles, healthy/critical counts, failure-type breakdown, a prioritised fix queue and a "start here" pointer.
- **Explainable root cause:** tabbed view with the diagnosis and matched signals, raw evidence (describe, events, logs), the read-only investigation path, and the fix with a before/after diff, YAML patch, copy/download and a checklist.
- **Runbook browser:** searchable list of every failure pattern, its signals and suggested fix.
- **Command palette:** press Ctrl+K (Cmd+K on macOS) to jump to any page, finding or snapshot.
- **Export:** Markdown report, JSON and print-friendly layout.
- **Log analyzer:** paste, drop or upload logs, or load a finding's container logs; local patterns highlight critical/error/warning lines and repeated messages. This is pattern triage, not proof of causation.
- **Reports and history:** Markdown diagnosis reports, action logs, evidence files, proposed YAML patches and locally saved web investigation history.
- **Safe live CLI mode:** read-only investigation by default, one named workload, namespace and context guards, server-side dry-run, explicit `yes` approval (default No), and post-change verification.
- **Optional container packaging:** build with `docker build -t kubedoctor .`; run with `docker run --rm -p 8765:8765 kubedoctor`.

## Architecture

```text
web/index.html + app.js + styles.css
             │ JSON API (/api/overview, /diagnose, /runbooks, /analyze-logs, /report, ...) (Python standard library HTTP server)
             ▼
webapp.py ── ReplayBackend / LiveBackend ── TriageAgent
                                              ├── detector
                                              ├── evidence-scored runbooks
                                              ├── safe patch planner
                                              └── report + action log
```

The web app calls the existing `TriageAgent` and `ReplayBackend`; diagnosis rules are shared with the CLI and maintained in `runbooks/runbooks.json`. The app binds to `127.0.0.1` by default. `webdata/` is created on first use for reports and local history.

### AI provider

This release does not call an LLM. The diagnostic core is deterministic and works offline without a key. This keeps the explanations tied to collected evidence and ensures missing API credentials do not break the product. The browser log analyzer uses transparent regex patterns. No generated explanation is represented as an LLM response.

## Live Kubernetes use

Live access is available through the CLI using local `kubectl` credentials. Use only a disposable Kind, Minikube, Docker Desktop or k3d cluster while evaluating. Install `kubectl`, Docker Desktop and Kind/Minikube separately, then follow the detailed safety flow below. The browser UI stays in recorded mode and does not receive kubeconfig credentials.

Create a local Kind cluster and the deliberately unhealthy sample workloads:

```powershell
kind create cluster --name triage-lab
kubectl apply -f workloads/
kubectl get pods -n triage-lab
```

Inspect one workload without applying changes:

```powershell
python triage.py --live --deployment crashy-api --diagnose-only
```

To permit the CLI's guarded approval prompt for a proposed patch, omit `--diagnose-only`. It performs a server-side dry run and asks for `yes` immediately before the only supported mutation. Live mode is limited to a named workload in a `triage-*` namespace and recognized local-cluster context by default. Do not enable `--allow-any-context` on production contexts. Review [SAFETY.md](SAFETY.md) before live use.

`k8s/readonly-rbac.yaml` is an example least-privilege namespace Role for read access. It grants no write permission. The CLI runs as the local user and does not automatically install or select this service account.

Cleanup:

```powershell
kubectl delete -f workloads/
kind delete cluster --name triage-lab
```

## Tests

Run the Python standard-library test suite:

```powershell
python tests/test_triage.py
python tests/test_webapp.py
```

Or double-click `run-tests.bat`. The existing tests cover recorded scenario diagnoses, patch planning, safety checks and live mode against a stub `kubectl`; they do not require a real cluster. No test claims that a real remediation has succeeded.

## Project layout

| Path | Purpose |
|---|---|
| `web/` | Responsive single-page UI (no build step) |
| `webapp.py` | Local JSON API and static file server |
| `triage.py` | CLI entry point for recorded and guarded live diagnosis |
| `kubetriage/` | Agent, detector, evidence scoring, safe command backend, patches and reports |
| `runbooks/runbooks.json` | Editable diagnosis knowledge base |
| `fixtures/` | Recorded `kubectl` outputs and scenario manifests |
| `workloads/` | Disposable example Kubernetes workloads |
| `k8s/readonly-rbac.yaml` | Optional read-only namespace RBAC example |
| `tests/` | Existing unit/integration-style suite and fake kubectl |
| `reports/` | Recorded sample output included with the project |
| `run-web.bat` | Starts the local browser app on Windows |

## Known limitations

- The supplied fixture data is representative recorded output, not a live cluster snapshot.
- The overview reports the share of healthy pods in the snapshot; it is not an SLO.
- No CPU/memory time series, alert delivery, PDF report generation, LLM integration or browser-based live cluster connection is included.
- Log analysis is local pattern matching and may surface false positives; it does not infer causality.
- Browser remediation is intentionally disabled. The CLI's live remediation supports only its existing allowlisted patch kinds and approval flow.
- The local history and reports are stored unencrypted in `webdata/`; do not paste secrets into the log analyzer. Do not expose the web server to an untrusted network.
