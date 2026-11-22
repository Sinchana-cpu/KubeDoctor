#!/usr/bin/env python3
"""Local KubeDoctor web app. Uses the project's real replay/live triage engine."""
import json
import os
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from kubetriage.agent import Config, TriageAgent
from kubetriage.backend import LiveBackend, ReplayBackend
from kubetriage.kb import load_rules
from kubetriage import report, patches
from kubetriage.safety import redact

WEB = ROOT / "web"
DATA = ROOT / "webdata"
DATA.mkdir(exist_ok=True)
RULES = load_rules(ROOT / "runbooks" / "runbooks.json")
SCENARIO_TITLES = {
    "all-four": "Full namespace sweep",
    "image-pull-backoff": "Image pull failure",
    "crashloop-missing-env": "Crash loop on startup",
    "oomkilled": "Out-of-memory kill",
    "pending-insufficient-cpu": "Unschedulable pod",
}
SCENARIOS = {
    "all-four": "Four broken workloads plus a healthy control",
    "image-pull-backoff": "Invalid image tag prevents container startup",
    "crashloop-missing-env": "Required application setting is missing",
    "oomkilled": "Container exceeded its configured memory limit",
    "pending-insufficient-cpu": "Pod cannot fit on available node capacity",
}

def json_response(handler, value, status=200):
    body = json.dumps(value, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)

def pod_overview(scenario):
    """Name, phase and restart count for every pod in the recorded snapshot."""
    f = ROOT / "fixtures" / scenario / "before" / "pods.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return []
    rows = []
    for it in items:
        meta, st = it.get("metadata", {}), it.get("status", {})
        cs = st.get("containerStatuses") or []
        waiting = next((c["state"]["waiting"].get("reason") for c in cs if "waiting" in c.get("state", {})), None)
        rows.append({"name": meta.get("name", ""), "app": (meta.get("labels") or {}).get("app") or meta.get("name", ""),
                     "namespace": meta.get("namespace", ""), "phase": st.get("phase", "Unknown"),
                     "reason": waiting or "", "restarts": sum(c.get("restartCount", 0) for c in cs),
                     "ready": bool(cs) and all(c.get("ready") for c in cs)})
    return rows

def diagnose(scenario, workload=None, save=True):
    if scenario not in SCENARIOS:
        raise ValueError("Choose a supported recorded scenario.")
    backend = ReplayBackend(ROOT / "fixtures" / scenario)
    cfg = Config(namespace="triage-lab", deployment=workload, params={"env.APP_MODE": "staging"}, approval="no")
    agent = TriageAgent(backend, RULES, cfg, say=lambda *a, **k: None)
    summary = agent.run()
    out = DATA / "reports" / scenario
    if save:
        report.write_all(summary, out)
    incidents = []
    for item in summary.results:
        p = item.problem
        rule = item.best.rule if item.best else {}
        matches = item.best.matches if item.best else []
        incidents.append({
            "id": f"{scenario}-{p.workload}", "workload": p.workload, "pod": p.pod,
            "namespace": p.namespace, "symptom": p.symptom, "detail": p.detail,
            "severity": "critical" if p.symptom in ("CrashLoopBackOff", "OOMKilled") else "warning",
            "cause": rule.get("title", "Cause not identified"), "ruleId": rule.get("id", ""),
            "explanation": rule.get("plain_language", "More evidence is needed before a cause can be stated."),
            "why": rule.get("why_it_happens", ""), "learn": rule.get("learn", ""),
            "confidence": item.best.confidence if item.best else "Low",
            "score": f"{item.best.got}/{item.best.total}" if item.best else "0/0",
            "evidence": [{"label": m.label, "source": m.source, "line": m.line} for m in matches],
            "steps": [{"command": c, "why": w, "exitCode": rc} for c, w, rc in item.steps],
            "skipped": [{"check": n, "reason": w} for n, w in item.skipped],
            "missing": item.missing,
            "patch": item.plan.patch if item.plan and item.plan.patch else None,
            "changes": [{"field": f, "before": b, "after": a} for f, b, a in (item.plan.changes if item.plan else [])],
            "fixSummary": rule.get("fix", {}).get("summary", "Review the evidence and follow the manual steps."),
            "manualSteps": rule.get("manual_steps", []),
            "outcome": item.outcome, "source": "recorded-fixture",
            "deployment": p.deployment,
            "evidenceFiles": {k: redact(str(v))[:8000] for k, v in item.evidence.items()},
            "patchYaml": patches.to_yaml(item.plan.patch) if item.plan and item.plan.patch else "",
            "patchCommand": (f"kubectl patch deployment {p.deployment} -n {p.namespace} --type strategic --patch-file {p.deployment}.yaml"
                             if item.plan and item.plan.patch and p.deployment else ""),
        })
    if save:
        history = DATA / "history.json"
        rows = json.loads(history.read_text()) if history.exists() else []
        rows.insert(0, {"id": f"{scenario}-{int(datetime.now().timestamp())}", "scenario": scenario,
                         "when": datetime.now(timezone.utc).isoformat(), "count": len(incidents),
                         "status": "Diagnosis saved", "workloads": [i["workload"] for i in incidents]})
        history.write_text(json.dumps(rows[:100], indent=2), encoding="utf-8")
        (DATA / "latest-report.txt").write_text(report.render_report(summary), encoding="utf-8")
    return {"scenario": scenario, "description": SCENARIOS[scenario], "title": SCENARIO_TITLES[scenario], "mode": "Replay",
            "context": summary.context, "namespace": summary.namespace, "podsSeen": summary.pods_seen,
            "incidents": incidents, "actionCount": len(summary.log.entries), "report": str(out / "diagnosis-report.md")}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[web] " + fmt % args)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/scenarios":
            return json_response(self, [{"id": k, "label": v, "title": SCENARIO_TITLES[k]} for k, v in SCENARIOS.items()])
        if path == "/api/runbooks":
            return json_response(self, [{"id": r["id"], "title": r["title"], "symptoms": r.get("symptoms", []),
                "explanation": r.get("plain_language", ""), "why": r.get("why_it_happens", ""),
                "fix": r.get("fix", {}).get("summary", ""), "fixKind": r.get("fix", {}).get("kind", "manual"),
                "signals": [{"label": s_.get("label", ""), "source": s_.get("source", ""), "weight": s_.get("weight", 1)} for s_ in r.get("signals", [])],
                "fallback": bool(r.get("fallback"))} for r in RULES])
        if path == "/api/safety":
            f = ROOT / "SAFETY.md"
            return json_response(self, {"text": f.read_text(encoding="utf-8") if f.exists() else ""})
        if path == "/api/overview":
            scenario = parse_qs(urlparse(self.path).query).get("scenario", ["all-four"])[0]
            try: data = diagnose(scenario, save=False)
            except Exception as e: return json_response(self, {"error": str(e)}, 400)
            counts = {"pods": data["podsSeen"], "incidents": len(data["incidents"]), "critical": sum(x["severity"] == "critical" for x in data["incidents"]),
                      "healthy": max(0, data["podsSeen"] - len(data["incidents"])), "pending": sum(x["symptom"] == "Pending" for x in data["incidents"]),
                      "restarting": sum(x["symptom"] in ("CrashLoopBackOff", "OOMKilled") for x in data["incidents"])}
            return json_response(self, {**data, "counts": counts, "pods": pod_overview(scenario), "cpu": None, "memory": None,
                                       "metricsMessage": "Recorded snapshots do not include CPU or memory metrics."})
        if path == "/api/history":
            f = DATA / "history.json"
            return json_response(self, json.loads(f.read_text()) if f.exists() else [])
        if path == "/api/report":
            q = parse_qs(urlparse(self.path).query).get("scenario", [None])[0]
            if q in SCENARIOS:
                diagnose(q)
            f = DATA / "latest-report.txt"
            body = f.read_bytes() if f.exists() else b"Run a recorded diagnosis first."
            self.send_response(200); self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=kubernetes-diagnosis-report.md")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if path.startswith("/api/"):
            return json_response(self, {"error": "Not found"}, 404)
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents and target != WEB.resolve():
            self.send_error(403); return
        if not target.is_file(): self.send_error(404); return
        body = target.read_bytes()
        content_type = {".html":"text/html; charset=utf-8", ".css":"text/css; charset=utf-8", ".js":"text/javascript; charset=utf-8", ".svg":"image/svg+xml"}.get(target.suffix, "application/octet-stream")
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 2_000_000: return json_response(self, {"error":"Request is too large (2 MB limit)."}, 413)
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict): return json_response(self,{"error":"Expected a JSON object."},400)
            if self.path == "/api/diagnose":
                result = diagnose(str(payload.get("scenario", "all-four")), payload.get("workload"))
                return json_response(self, result)
            if self.path == "/api/analyze-logs":
                text = str(payload.get("text", ""))[:1_500_000]
                lines = text.splitlines()
                patterns = [("critical", re.compile(r"(?i)fatal|panic|out of memory|oomkilled|killed process|segmentation fault|permission denied")),
                            ("error", re.compile(r"(?i)\berror\b|exception|failed|failure|traceback|refused|timed? ?out")),
                            ("warning", re.compile(r"(?i)\bwarn(?:ing)?\b|retry|back.?off|deprecated"))]
                findings=[]; counts={k:0 for k,_ in patterns}
                for n,line in enumerate(lines,1):
                    for level,rx in patterns:
                        if rx.search(line):
                            counts[level]+=1
                            if len(findings)<100: findings.append({"line":n,"severity":level,"text":line[:1000]})
                            break
                repeat={}
                for f in findings:
                    key=re.sub(r"\b\d+\b", "#", f["text"].strip().lower())[:120]
                    if key: repeat[key]=repeat.get(key,0)+1
                repeated=[{"pattern":k,"count":v} for k,v in repeat.items() if v>1]
                return json_response(self,{"source":"user-supplied logs","lines":len(lines),"counts":counts,"findings":findings,"repeated":repeated[:20],"note":"Pattern-based analysis of the text you supplied; these are signals to investigate, not proof of a root cause."})
            return json_response(self,{"error":"Not found"},404)
        except (ValueError, json.JSONDecodeError) as e: return json_response(self,{"error":str(e)},400)
    def do_DELETE(self):
        if self.path == "/api/history":
            f = DATA / "history.json"
            if f.exists(): f.unlink()
            return json_response(self, {"ok": True})
        return json_response(self, {"error": "Not found"},404)

if __name__ == "__main__":
    host = os.environ.get("KUBEDOCTOR_HOST", "127.0.0.1")
    port = int(os.environ.get("KUBEDOCTOR_PORT", "8765"))
    print(f"KubeDoctor is running at http://{host}:{port} (replay mode)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
