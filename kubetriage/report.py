"""Write the diagnosis report (Markdown), action log (JSON), patch files and raw evidence."""
import json
from pathlib import Path

from . import __version__, patches
from .safety import ALLOWED_VERBS, redact


def _fence(text, lang="text"):
    return f"```{lang}\n{text.rstrip()}\n```"


def _esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _headline(wr):
    p = wr.problem
    cause = wr.best.rule["title"] if wr.best else "Cause not identified"
    return f"{p.symptom} - {cause}"


def write_patch_files(summary, out_dir):
    d = Path(out_dir) / "proposed-fixes"
    for wr in summary.results:
        if wr.plan and wr.plan.patch is not None and not wr.plan.needs_input and wr.problem.deployment:
            p = wr.problem
            d.mkdir(parents=True, exist_ok=True)
            header = (f"Proposed fix for deployment/{p.deployment} in namespace {p.namespace}\n"
                      f"Rule: {wr.best.rule['id']}. Review before applying:\n"
                      f"kubectl patch deployment {p.deployment} -n {p.namespace} --type strategic --patch-file proposed-fixes/{p.deployment}.yaml")
            path = d / f"{p.deployment}.yaml"
            path.write_text(patches.render_patch_file(wr.plan, header), encoding="utf-8")
            wr.patch_file = f"proposed-fixes/{p.deployment}.yaml"


def write_evidence(summary, out_dir):
    for wr in summary.results:
        name = wr.problem.deployment or wr.problem.pod
        d = Path(out_dir) / "evidence" / name
        d.mkdir(parents=True, exist_ok=True)
        for key, text in wr.evidence.items():
            (d / f"{key}.txt").write_text(redact(text) + "\n", encoding="utf-8")
        if wr.deployment_json:
            (d / "deployment.json").write_text(redact(json.dumps(wr.deployment_json, indent=2)) + "\n", encoding="utf-8")


def write_action_log(summary, out_dir):
    results = summary.results
    doc = {
        "schema": "k8s-triage-action-log/2",
        "tool_version": __version__,
        "started": summary.started, "finished": summary.finished,
        "mode": summary.mode, "scenario": summary.scenario,
        "context": summary.context, "namespace": summary.namespace,
        "safety": {
            "read_only_verbs": sorted(ALLOWED_VERBS) + ["rollout status", "rollout history"],
            "secrets_never_read": True,
            "only_mutation": "kubectl patch deployment (server dry run first, then only after approval)",
        },
        "summary": {
            "pods_seen": summary.pods_seen,
            "unhealthy_workloads": len(results),
            "read_commands": sum(1 for e in summary.log.entries if e["kind"] == "read"),
            "mutations": sum(1 for e in summary.log.entries if e["kind"] == "mutation"),
            "recovered": sum(1 for r in results if r.verification and r.verification["status"] == "RECOVERED"),
        },
        "workloads": [{
            "workload": wr.problem.workload, "symptom": wr.problem.symptom,
            "runbook_rule": wr.best.rule["id"] if wr.best else None,
            "confidence": wr.best.confidence if wr.best else None,
            "patch_file": wr.patch_file, "approval": wr.approval, "patch_applied": wr.applied,
            "verification": (wr.verification or {}).get("status", "not run"),
            "outcome": wr.outcome,
        } for wr in results],
        "entries": summary.log.entries,
    }
    (Path(out_dir) / "action-log.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def render_report(summary):
    L = []
    a = L.append
    a("# Kubernetes diagnosis report\n")
    if summary.mode == "replay":
        a("> **RECORDED / SIMULATED RUN.** Every command output below was replayed from saved fixture files, not read from a live cluster. "
          "Nothing was applied to any cluster, and no recovery was (or could be) verified.\n")
    else:
        a("> **LIVE RUN.** Command output below came from the cluster in the context named here.\n")
    a(f"- **Run:** {summary.started} (UTC) -> {summary.finished}")
    a(f"- **Mode:** {'recorded cluster (replay)' if summary.mode == 'replay' else 'live cluster'}"
      + (f" - scenario `{summary.scenario}`" if summary.scenario else ""))
    a(f"- **Cluster context:** `{summary.context}`  |  **Scope:** {summary.namespace}")
    a(f"- **Pods inspected:** {summary.pods_seen}  |  **Unhealthy workloads:** {len(summary.results)}")
    applied = sum(1 for r in summary.results if r.applied)
    rec = sum(1 for r in summary.results if r.verification and r.verification["status"] == "RECOVERED")
    a(f"- **Changes applied:** {applied}  |  **Verified recovered:** {rec}\n")

    if not summary.results:
        a("All pods are healthy. Nothing to diagnose.\n")
    else:
        a("## Summary\n")
        a("| Workload | Symptom | Root cause | Confidence | Outcome |")
        a("|---|---|---|---|---|")
        for wr in summary.results:
            a(f"| `{wr.problem.workload}` | {wr.problem.symptom} | {_esc(wr.best.rule['title'] if wr.best else 'not identified')} "
              f"| {wr.best.confidence if wr.best else '-'} | {wr.outcome} |")
        a("")
        for i, wr in enumerate(summary.results, 1):
            L.extend(_workload_section(i, wr))

    a("## How this report was produced\n")
    a("- Every cluster read used the read-only allowlist (`get`, `describe`, `logs`, `top`, `rollout status`). Secrets are never read, and credentials in output are masked.")
    a("- Recorded mode makes no cluster changes at all. In live mode the only change possible is one `kubectl patch deployment` on the named workload in a lab namespace, preceded by a server-side dry run and applied only after you type `yes` (default is No).")
    a("- Recovery is reported only if, after an approved patch, the rollout completes and the pods are Running, Ready and not restarting.")
    a("- The diagnosis is rule-based: it matches evidence against the runbook in `runbooks/runbooks.json`. It is a strong lead, not a proof - check the evidence above.")
    a("- Every command, reason and result is in `action-log.json`; raw (redacted) outputs are in `evidence/`.\n")
    return "\n".join(L)


def _workload_section(i, wr):
    p = wr.problem
    L = []
    a = L.append
    a(f"## {i}. `{p.workload}` - {_headline(wr)}\n")
    a(f"**Status found:** {p.detail}." + (f" ({p.pods_affected} pods affected)" if p.pods_affected > 1 else "") + "\n")
    if not wr.best:
        a("**No runbook rule matched the evidence, so the agent will not guess a cause.** What it collected is in `evidence/`.\n")
        a("**What is missing / what to collect next:**\n")
        for m in wr.missing or ["Nothing failed to collect, but none of the runbook patterns matched. Add a rule to `runbooks/runbooks.json` once the cause is understood."]:
            a(f"- {m}")
        a("")
        a("Next steps for a human:\n")
        a(_fence(f"kubectl describe pod {p.pod} -n {p.namespace}\nkubectl logs {p.pod} -n {p.namespace} --previous\n"
                 f"kubectl get events -n {p.namespace} --sort-by=.lastTimestamp", "bash"))
        a("")
        return L
    rule = wr.best.rule
    if wr.best.confidence == "Low" or wr.missing:
        a("> **Evidence is incomplete.** " + ("Confidence is Low. " if wr.best.confidence == "Low" else "")
          + "Missing: " + ("; ".join(wr.missing) if wr.missing else "stronger matching signals (see the table below)")
          + ". Treat the diagnosis below as a lead to check, not a conclusion.\n")
    a("### In plain language\n")
    a(rule["plain_language"] + "\n")
    a(f"*Why this usually happens:* {rule['why_it_happens']}\n")
    a("### What the agent saw\n")
    a("| Evidence | From | Line |")
    a("|---|---|---|")
    for m in wr.best.matches:
        a(f"| {_esc(m.label)} | {m.source} | `{_esc(m.line)}` |")
    a("")
    a(f"**Confidence: {wr.best.confidence}** - matched {wr.best.got} of {wr.best.total} evidence points for rule `{rule['id']}`.")
    others = [s for s in wr.scores[1:] if s.got > 0]
    if others:
        a("\nOther explanations considered and ranked lower:\n")
        for s in others:
            a(f"- `{s.rule['id']}` ({s.got}/{s.total}) - {s.rule['title']}")
    a("")
    a("### How the agent investigated\n")
    a("| # | Command | Why this command |")
    a("|---|---|---|")
    for n, (cmd, why, rc) in enumerate(wr.steps, 1):
        a(f"| {n} | `{_esc(cmd)}`{'' if rc == 0 else ' (failed)'} | {_esc(why)} |")
    for did, why in wr.skipped:
        a(f"| - | *skipped `{did}`* | {_esc(why)} |")
    a("")
    a("### Proposed fix\n")
    plan = wr.plan
    if plan and plan.patch is not None and not plan.needs_input:
        a(rule["fix"]["summary"] + "\n")
        a("| Field | Before | After |")
        a("|---|---|---|")
        for f, b, af in plan.changes:
            a(f"| {_esc(f)} | `{_esc(b)}` | `{_esc(af)}` |")
        a("")
        a(_fence(patches.to_yaml(plan.patch), "yaml"))
        for n in plan.notes:
            a(f"\n> {n}")
        if wr.patch_file:
            a(f"\nSaved as `{wr.patch_file}`. To apply it yourself:\n")
            a(_fence(f"kubectl patch deployment {p.deployment} -n {p.namespace} --type strategic --patch-file {wr.patch_file}", "bash"))
    else:
        a("**No automatic patch.** " + ((plan.reason_none if plan else "") or rule["fix"]["summary"]) + "\n")
        if plan and plan.needs_input:
            a("The agent will not guess missing values. Provide them and re-run, for example: "
              + ", ".join(f"`--set {k}=<value>`" for k in plan.needs_input) + "\n")
        for n in (plan.notes if plan else []):
            a(f"> {n}\n")
        a("Manual steps:\n")
        for s in rule.get("manual_steps", []):
            a(f"1. {s}")
    a("")
    a("### Decision and result\n")
    a(f"- **Approval:** {wr.approval}")
    if wr.apply_note:
        a(f"- **Note:** {wr.apply_note}")
    a(f"- **Change applied:** {'yes' if wr.applied else 'no'}")
    v = wr.verification
    if v:
        a(f"- **Verification:** {'RECOVERED' if v['status'] == 'RECOVERED' else 'NOT RECOVERED'}"
          + (f" - {v['detail']}" if v.get("detail") else ""))
        if v.get("rollout"):
            a(f"- **Rollout:** {_esc(v['rollout'].splitlines()[-1])}")
        if v["pods"]:
            a("\n| Pod | Ready | Status | Restarts |")
            a("|---|---|---|---|")
            for r in v["pods"]:
                a(f"| `{r['pod']}` | {r['ready']} | {r['status']} | {r['restarts']} |")
        if v["status"] != "RECOVERED":
            a(f"\nTo undo the change: `{wr.rollback}`")
    elif not wr.applied:
        a("- **Verification:** not run - nothing was applied, so recovery is NOT claimed. After you apply the fix on a real cluster, confirm with "
          f"`kubectl rollout status deployment/{p.deployment or '<name>'} -n {p.namespace}` and `kubectl get pods -n {p.namespace}`; "
          "recovery means `1/1 Running` with a stable restart count.")
    a("")
    a(f"> **Learn:** {rule['learn']}\n")
    return L


def write_all(summary, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_patch_files(summary, out)
    write_evidence(summary, out)
    write_action_log(summary, out)
    (out / "diagnosis-report.md").write_text(render_report(summary), encoding="utf-8")
