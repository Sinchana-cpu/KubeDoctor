"""The triage agent: detect -> investigate (step by step) -> diagnose -> propose -> approve -> apply -> verify.

The planner is deliberately transparent and rule-driven (no LLM, no network):
after every command it re-scores the runbook rules against the evidence so far
and picks the next read-only command from the best rule's checklist. Each
choice, with its reason, is written to the action log.
"""
import fnmatch
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import commands as C
from . import detect, kb, patches
from .safety import UnsafeCommand, redact

LAB_CONTEXTS = ["kind-*", "minikube", "docker-desktop", "k3d-*", "rancher-desktop", "*(recorded)*"]
DEFAULT_WHY = {
    "describe": "'describe' is the richest single view of a pod: status, last exit code, resource limits and its Events.",
    "events": "Events show what the cluster itself observed (scheduling, pulling, restarting) in time order.",
}


class TriageError(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActionLog:
    def __init__(self):
        self.entries = []

    def add(self, kind, phase, workload, command="", rationale="", rc=None, result="", output="", source="agent"):
        # source: "recorded-fixture" (replayed saved output), "live-kubectl" (real cluster), or "agent" (a decision)
        e = {"seq": len(self.entries) + 1, "ts": utcnow(), "phase": phase, "kind": kind, "source": source,
             "workload": workload, "command": command, "rationale": rationale, "exit_code": rc, "result": result}
        if output:
            e["output_sha256"] = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()
            e["output_excerpt"] = redact(output)[:400]
        self.entries.append(e)
        return e


@dataclass
class Config:
    namespace: str = "triage-lab"        # None = all namespaces
    deployment: str = None               # optional filter
    params: dict = field(default_factory=dict)
    approval: str = "ask"                # ask (live default) | no (diagnose only)
    input_fn: object = None              # injectable for tests; defaults to input()
    allowed_namespace_patterns: tuple = ("triage-*",)
    verify_timeout: int = 120
    poll_seconds: int = 5
    settle_seconds: int = 15
    allow_any_context: bool = False
    max_steps: int = 12


@dataclass
class WorkloadResult:
    problem: detect.Problem
    scores: list = field(default_factory=list)
    best: kb.Score = None
    steps: list = field(default_factory=list)       # (command string, why, rc)
    skipped: list = field(default_factory=list)     # (diagnostic, why)
    evidence: dict = field(default_factory=dict)
    deployment_json: dict = None
    plan: patches.PatchPlan = None
    patch_file: str = None
    approval: str = "not requested"
    applied: bool = False
    apply_note: str = ""
    verification: dict = None
    rollback: str = ""
    missing: list = field(default_factory=list)     # evidence that could not be collected

    @property
    def outcome(self):
        if self.verification:
            return "Recovered" if self.verification["status"] == "RECOVERED" else "Applied, NOT recovered"
        if self.applied:
            return "Applied"
        if self.plan and self.plan.ready:
            return "Fix proposed (not applied)"
        if self.best:
            return "Diagnosed; manual fix needed"
        return "Unknown cause; manual investigation"


@dataclass
class RunSummary:
    results: list
    log: ActionLog
    started: str
    finished: str = ""
    mode: str = ""
    context: str = ""
    namespace: str = ""
    pods_seen: int = 0
    scenario: str = ""


class TriageAgent:
    def __init__(self, backend, rules, cfg, say=print, sleep=time.sleep, out_dir=None):
        self.b, self.rules, self.cfg, self.say, self.sleep = backend, rules, cfg, say, sleep
        self.log = ActionLog()
        self.out_dir = out_dir
        self.context = "(unknown)"

    # ---------------------------------------------------------------- reads
    def _read(self, args, why, phase, workload="-"):
        cmd = C.display(args)
        try:
            r = self.b.run(args)
        except UnsafeCommand as exc:       # defence in depth: should be impossible
            self.log.add("blocked", phase, workload, cmd, why, None, f"BLOCKED by safety layer: {exc}", source=self.b.source)
            raise
        shown = (r.out if r.ok else r.text)
        first = shown.strip().splitlines()[0][:100] if shown.strip() else "(no output)"
        self.log.add("read", phase, workload, cmd, why, r.rc, ("ok: " if r.ok else "FAILED: ") + first, r.text, source=self.b.source)
        return r

    # ------------------------------------------------------------------ run
    def run(self):
        started = utcnow()
        cfg = self.cfg
        if self.b.name == "live":
            if not cfg.deployment:
                raise TriageError("Live mode works on ONE named workload: add --deployment <name>.")
            if not cfg.namespace or not any(fnmatch.fnmatch(cfg.namespace, pat) for pat in cfg.allowed_namespace_patterns):
                raise TriageError(f"Live mode is limited to dedicated lab namespaces matching "
                                  f"{', '.join(cfg.allowed_namespace_patterns)}; got {cfg.namespace!r}. "
                                  f"Use --allow-namespace NAME only for a disposable namespace.")
        self.context = self.b.current_context()
        scope = "all namespaces" if not cfg.namespace else f"namespace '{cfg.namespace}'"
        self.say(f"Cluster context: {self.context}")
        self.log.add("info", "start", "-", "", f"Triage of {scope}; read-only diagnostics, mutations only after approval.",
                     None, f"context={self.context}; evidence source={self.b.source}")
        self.say(f"[1] Detect: listing pods in {scope} ...")
        r = self._read(C.get_pods(cfg.namespace), "Find pods that are not healthy by reading every pod's status.", "detect")
        if not r.ok:
            raise TriageError("Could not list pods. Is kubectl pointed at the right cluster/namespace?\n" + r.text)
        try:
            pod_list = json.loads(r.out)
        except json.JSONDecodeError:
            raise TriageError("kubectl did not return valid JSON for the pod list:\n" + r.text[:300])
        pods = {p["metadata"]["name"]: p for p in pod_list.get("items", [])}
        problems = detect.find_problems(pod_list)
        if cfg.deployment:
            problems = [p for p in problems if p.deployment == cfg.deployment]
        self.log.add("decision", "detect", "-", "", f"{len(pods)} pod(s) read; {len(problems)} unhealthy workload(s) found.",
                     None, ", ".join(f"{p.workload}={p.symptom}" for p in problems) or "all pods healthy")
        if not problems:
            self.say("    All pods look healthy. Nothing to do.")
        results = []
        for i, p in enumerate(problems, 1):
            self.say(f"    Unhealthy: {p.workload}  {p.symptom}  ({p.detail})")
        for p in problems:
            results.append(self._triage(p, pods.get(p.pod, {})))
        return RunSummary(results=results, log=self.log, started=started, finished=utcnow(),
                          mode=self.b.name, context=self.context, namespace=cfg.namespace or "(all)",
                          pods_seen=len(pods), scenario=getattr(self.b, "scenario", ""))

    # -------------------------------------------------------------- triage
    def _diag_args(self, did, p, state):
        """Return (args, skip_reason, evidence_key)."""
        if did == "describe":
            return C.describe_pod(p.namespace, p.pod), None, "describe"
        if did == "events":
            return C.pod_events(p.namespace, p.pod), None, "events"
        if did in ("logs", "logs_previous"):
            if not p.ever_started:
                return None, "the container never started, so there are no application logs to read", "logs"
            return C.pod_logs(p.namespace, p.pod, p.container, previous=p.restarts > 0), None, "logs"
        if did == "deployment":
            if not p.deployment:
                return None, "the pod has no Deployment owner, so there is nothing to patch", "deployment"
            return C.get_deployment(p.namespace, p.deployment), None, "deployment"
        if did == "nodes":
            return C.get_nodes(), None, "nodes"
        return None, f"unknown diagnostic '{did}'", did

    def _triage(self, p, pod_json):
        wr = WorkloadResult(problem=p)
        wl = p.workload
        ev = wr.evidence
        ev["pod"] = f"symptom: {p.symptom}\ndetail: {p.detail}\n" + json.dumps(pod_json.get("status", {}), indent=1)
        done = set()
        nodes = []
        self.say(f"\n[2] Investigate {wl} ({p.symptom})")

        def run_diag(did, why):
            nonlocal nodes
            if did in done:
                return
            done.add(did)
            args, skip, key = self._diag_args(did, p, wr)
            if skip:
                wr.skipped.append((did, skip))
                if did == "deployment":
                    wr.missing.append("The owning Deployment could not be read, so no patch can be drafted.")
                self.log.add("decision", "investigate", wl, "", f"Skipped '{did}': {skip}.")
                self.say(f"    - skip {did}: {skip}")
                return
            r = self._read(args, why, "investigate", wl)
            wr.steps.append((C.display(args), why, r.rc))
            if not r.ok:
                wr.missing.append(f"`{C.display(args)}` could not be collected ({r.text[:120]})")
            self.say(f"    - {C.display(args)}" + ("" if r.ok else "   (failed)"))
            if not r.ok:
                return
            if key == "deployment":
                try:
                    wr.deployment_json = json.loads(r.out)
                except json.JSONDecodeError:
                    pass
            elif key == "nodes":
                try:
                    nodes = [{"name": n["metadata"]["name"], "allocatable": n["status"]["allocatable"]}
                             for n in json.loads(r.out)["items"]]
                    ev["nodes"] = "\n".join(f"node {n['name']}: allocatable cpu={n['allocatable']['cpu']}, "
                                            f"memory={n['allocatable']['memory']}" for n in nodes)
                except (json.JSONDecodeError, KeyError):
                    pass
            else:
                ev[key] = r.out

        for did in ("describe", "events"):
            run_diag(did, DEFAULT_WHY[did])
        for _ in range(self.cfg.max_steps):
            scores = kb.rank(self.rules, p.symptom, ev)
            top = scores[0] if scores and scores[0].got > 0 else None
            pending = [d for d in (top.rule["diagnostics"] if top else []) if d["id"] not in done]
            if not pending:
                break
            self.log.add("decision", "investigate", wl, "", f"Leading hypothesis: {top.rule['id']} "
                         f"({top.got}/{top.total}). Next check: {pending[0]['id']}.")
            run_diag(pending[0]["id"], pending[0]["why"])
        scores = kb.rank(self.rules, p.symptom, ev)
        if not scores or scores[0].got == 0 or scores[0].confidence == "Low":
            self.say("    ! evidence is inconclusive; widening the search")
            for did in ("logs_previous", "nodes", "deployment"):
                run_diag(did, "The evidence so far is inconclusive, so the search is widened.")
            scores = kb.rank(self.rules, p.symptom, ev)
        wr.scores = scores
        wr.best = scores[0] if scores and scores[0].got > 0 else None

        self.say(f"[3] Diagnose {wl}")
        if not wr.best:
            self.log.add("decision", "diagnose", wl, "", "No runbook rule matched the evidence; manual investigation advised.")
            self.say("    No runbook rule matched. Evidence was preserved; investigate manually.")
            return wr
        rule = wr.best.rule
        self.log.add("decision", "diagnose", wl, "", f"Matched runbook rule '{rule['id']}' "
                     f"(score {wr.best.got}/{wr.best.total}, confidence {wr.best.confidence}).",
                     None, rule["title"])
        self.say(f"    {rule['title']}  [rule {rule['id']}, confidence {wr.best.confidence}]")
        self._propose(wr, rule, nodes)
        return wr

    # ------------------------------------------------------------- propose
    def _propose(self, wr, rule, nodes):
        p, wl, cfg = wr.problem, wr.problem.workload, self.cfg
        params = cfg.params
        plan = patches.build(rule, p, wr.deployment_json, nodes, wr.evidence, params)
        if plan.needs_input and self.b.name == "live" and cfg.approval == "ask":
            for key in list(plan.needs_input):
                try:
                    ans = (cfg.input_fn or input)(f"    Need a value for {key} (leave empty to skip): ").strip()
                except EOFError:
                    break
                if ans:
                    params[key] = ans
            plan = patches.build(rule, p, wr.deployment_json, nodes, wr.evidence, params)
        wr.plan = plan
        self.say(f"[4] Propose fix for {wl}")
        if plan.patch is None or plan.needs_input:
            why = plan.reason_none or "More information is needed."
            self.log.add("decision", "propose", wl, "", "No automatic patch drafted: " + why,
                         None, "; ".join(plan.needs_input) and ("needs input: " + ", ".join(plan.needs_input)) or "manual fix")
            self.say(f"    No automatic patch: {why}")
            return
        for field_, before, after in plan.changes:
            self.say(f"    {field_}: {before} -> {after}")
        self.log.add("decision", "propose", wl, "", plan.summary, None,
                     "; ".join(f"{f}: {b} -> {a}" for f, b, a in plan.changes))
        if self.b.name != "live":
            wr.approval = "not applicable - recorded mode never applies patches"
            self.log.add("decision", "propose", wl, "", "Recorded mode: patch is shown for review only; no cluster exists to change.")
            return
        if cfg.approval == "no":
            wr.approval = "not requested (diagnose-only mode)"
            return
        if p.deployment != cfg.deployment or p.namespace != cfg.namespace:
            wr.approval = "refused: workload is outside the named lab target"
            return
        self._apply_flow(wr)

    # --------------------------------------------------------------- apply
    def _context_ok(self):
        if self.b.name != "live" or self.cfg.allow_any_context:
            return True
        return any(fnmatch.fnmatch(self.context, pat) for pat in LAB_CONTEXTS)

    def _patch_cmd(self, wr, dry=False):
        p = wr.problem
        return (f"kubectl patch deployment {p.deployment} -n {p.namespace} --type strategic -p "
                f"'{json.dumps(wr.plan.patch)}'" + (" --dry-run=server" if dry else ""))

    def _apply_flow(self, wr):
        p, wl = wr.problem, wr.problem.workload
        if not p.deployment:
            wr.approval = "not applicable (no Deployment owner)"
            return
        if not self._context_ok():
            wr.approval = "refused by safety guard"
            wr.apply_note = (f"Context '{self.context}' does not look like a local lab cluster "
                             f"(kind-*, minikube, docker-desktop, k3d-*). Re-run with --allow-any-context if you are sure.")
            self.log.add("blocked", "apply", wl, "", wr.apply_note, None, "refused: context guard")
            self.say("    REFUSED: " + wr.apply_note)
            return
        dry = self.b.apply_patch(p.deployment, p.namespace, wr.plan.patch, dry_run=True)
        self.log.add("dry-run", "apply", wl, self._patch_cmd(wr, dry=True),
                     "Server-side dry run: asks the API server to validate the patch without changing anything.",
                     dry.rc, ("ok: " if dry.ok else "FAILED: ") + dry.text[:100], dry.text, source="live-kubectl")
        if not dry.ok:
            wr.approval = "not asked (dry run failed)"
            wr.apply_note = "The API server rejected the patch in a dry run: " + dry.text[:200]
            self.say("    Dry run failed: " + dry.text[:160])
            return
        self.say("    Dry run OK (nothing changed).")
        approved, how = self._approve(wr)
        wr.approval = how
        self.log.add("approval", "approve", wl, "", "Human approval is required before any change.", None, how)
        if not approved:
            self.say(f"    Not applied: {how}")
            return
        self.say(f"[5] Apply to {wl}")
        r = self.b.apply_patch(p.deployment, p.namespace, wr.plan.patch)
        self.log.add("mutation", "apply", wl, self._patch_cmd(wr),
                     "Apply the approved patch (the only change this tool ever makes).", r.rc,
                     ("ok: " if r.ok else "FAILED: ") + r.text[:100], r.text, source="live-kubectl")
        if not r.ok:
            wr.apply_note = "kubectl patch failed: " + r.text[:200]
            self.say("    " + wr.apply_note)
            return
        wr.applied = True
        wr.rollback = f"kubectl rollout undo deployment/{p.deployment} -n {p.namespace}"
        self.say("    Patched. " + r.text.strip())
        self._verify(wr)

    def _approve(self, wr):
        """Explicit confirmation immediately before applying. Default is No: anything except
        'y'/'yes', an empty answer, an unclear answer or EOF all leave the cluster unchanged."""
        if self.cfg.approval != "ask":
            return False, "not approved: approval not requested"
        ask = self.cfg.input_fn or input
        print()
        print(f"    About to change {wr.problem.workload} on cluster context '{self.context}':")
        for f, b, a in wr.plan.changes:
            print(f"      {f}: {b} -> {a}")
        try:
            ans = ask("    Apply this change? Type 'yes' to apply [y/N]: ").strip().lower()
        except EOFError:
            return False, "declined: no answer (end of input) - default is No"
        if ans in ("y", "yes"):
            return True, "approved by user at the prompt"
        if ans in ("", "n", "no"):
            return False, "declined by user (default is No)"
        return False, f"declined: unclear answer {ans[:20]!r} treated as No"

    # -------------------------------------------------------------- verify
    @staticmethod
    def _pod_rows(pods):
        rows = []
        for q in pods:
            sts = q.get("status", {}).get("containerStatuses") or []
            ready = sum(1 for c in sts if c.get("ready"))
            state = q.get("status", {}).get("phase", "")
            for c in sts:
                w = c.get("state", {}).get("waiting")
                if w:
                    state = w.get("reason", state)
            rows.append({"pod": q["metadata"]["name"], "ready": f"{ready}/{len(sts) or '?'}", "status": state,
                         "restarts": sum(c.get("restartCount", 0) for c in sts)})
        return rows

    def _get_selector_pods(self, wr, selector, why):
        p = wr.problem
        r = self._read(C.get_pods_selector(p.namespace, selector), why, "verify", p.workload)
        try:
            items = json.loads(r.out).get("items", []) if r.ok else []
        except json.JSONDecodeError:
            items = []
        return [i for i in items if not i["metadata"].get("deletionTimestamp")]

    def _verify(self, wr):
        p, wl, cfg = wr.problem, wr.problem.workload, self.cfg
        self.say(f"[6] Verify {wl}")
        try:
            labels = wr.deployment_json["spec"]["selector"]["matchLabels"]
        except (TypeError, KeyError):
            wr.verification = {"status": "NOT_RECOVERED", "detail": "Could not determine the Deployment's pod selector.", "pods": []}
            return
        selector = C.selector_string(labels)
        timeout = max(1, cfg.verify_timeout)
        rs = self._read(C.rollout_status(p.namespace, p.deployment, f"{timeout}s"),
                        "Wait for Kubernetes to finish replacing the old pods with new ones.", "verify", wl)
        self.say(f"    - rollout status: {rs.text.splitlines()[-1] if rs.text else '(none)'}")
        attempts = max(1, math.ceil(timeout / max(1, cfg.poll_seconds)))
        pods, healthy = [], False
        for i in range(attempts):
            pods = self._get_selector_pods(wr, selector, "Check that the new pods are Running and Ready.")
            healthy = bool(pods) and all(detect.pod_is_healthy(q) for q in pods)
            if healthy or i == attempts - 1:
                break
            self.sleep(cfg.poll_seconds)
        detail = ""
        if healthy and cfg.settle_seconds:
            before = {q["metadata"]["name"]: self._pod_rows([q])[0]["restarts"] for q in pods}
            self.say(f"    - waiting {cfg.settle_seconds}s to confirm it stays up (no new restarts) ...")
            self.sleep(cfg.settle_seconds)
            pods = self._get_selector_pods(wr, selector, "Re-check after a settle period to catch a crash loop that starts late.")
            after = {q["metadata"]["name"]: self._pod_rows([q])[0]["restarts"] for q in pods}
            healthy = bool(pods) and all(detect.pod_is_healthy(q) for q in pods) and all(after.get(k, 0) <= v for k, v in before.items())
            if not healthy:
                detail = "The pod became Ready but then restarted or stopped being Ready."
        rows = self._pod_rows(pods)
        status = "RECOVERED" if healthy else "NOT_RECOVERED"
        if not healthy and not detail:
            detail = "Pods did not become Running and Ready within the timeout."
        wr.verification = {"status": status, "detail": detail, "pods": rows, "rollout": rs.text.strip()}
        self.log.add("verify", "verify", wl, "", "Pods must be Running, Ready and not restarting.", None,
                     status + (": " + detail if detail else ""))
        if healthy:
            self.say("    RECOVERED: " + ", ".join(f"{r['pod']} {r['ready']} {r['status']}" for r in rows))
        else:
            self.say("    NOT RECOVERED. " + detail)
            self.say(f"    To undo the change: {wr.rollback}")
