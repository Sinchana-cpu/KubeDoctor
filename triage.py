#!/usr/bin/env python3
"""Kubernetes workload triage agent.

Detects unhealthy pods, investigates them step by step with read-only kubectl
commands, matches the evidence to a runbook, explains the root cause, proposes
a YAML patch, applies it ONLY after approval, and verifies recovery.

  python triage.py --scenario all-four --set env.APP_MODE=staging         # recorded demo: no cluster, never changes anything
  python triage.py --live --deployment crashy-api --set env.APP_MODE=staging   # lab cluster; asks (default No) before any change
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from kubetriage import __version__, report                      # noqa: E402
from kubetriage.agent import Config, TriageAgent, TriageError    # noqa: E402
from kubetriage.backend import LiveBackend, ReplayBackend        # noqa: E402
from kubetriage.kb import load_rules                             # noqa: E402
from kubetriage.safety import UnsafeCommand                      # noqa: E402


def list_scenarios():
    out = []
    for m in sorted((ROOT / "fixtures").glob("*/manifest.json")):
        d = json.loads(m.read_text(encoding="utf-8"))
        out.append((m.parent.name, d.get("description", "")))
    return out


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--scenario", "--replay", dest="scenario", metavar="NAME_OR_DIR",
                     help="Use a recorded cluster from fixtures/ (no kubectl needed)")
    src.add_argument("--live", action="store_true", help="Use the real cluster through kubectl")
    src.add_argument("--list-scenarios", action="store_true", help="List recorded scenarios and exit")
    ap.add_argument("-n", "--namespace", default="triage-lab", help="Namespace to inspect (default: triage-lab)")
    ap.add_argument("--deployment", help="The workload to triage (required in --live mode)")
    ap.add_argument("--context", help="kubectl context to use (live mode)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="Supply a value the agent must not guess: env.NAME=value, image=name:tag, memory=256Mi")
    ap.add_argument("--diagnose-only", action="store_true", help="Live mode: diagnose and write patches, never ask to apply")
    ap.add_argument("--allow-namespace", action="append", default=[], metavar="NAME",
                    help="Live mode: also allow this (disposable!) namespace; by default only triage-* namespaces")
    ap.add_argument("--out", help="Output folder (default: reports/<scenario> or reports/live)")
    ap.add_argument("--verify-timeout", type=int, default=120, help="Seconds to wait for recovery (default 120)")
    ap.add_argument("--settle", type=int, default=15, help="Seconds a fixed pod must stay up without restarting (default 15)")
    ap.add_argument("--allow-any-context", action="store_true",
                    help="Allow applying to a cluster that is not kind/minikube/docker-desktop/k3d")
    ap.add_argument("--kubectl", default="kubectl", help="Path to kubectl")
    return ap.parse_args(argv)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = parse_args(argv)
    if a.list_scenarios:
        for name, desc in list_scenarios():
            print(f"{name:24s} {desc}")
        return 0
    if not a.scenario and not a.live:
        print("Choose a mode: --scenario <name> (recorded demo) or --live. Try: python triage.py --list-scenarios", file=sys.stderr)
        return 2
    params = {}
    for kv in a.set:
        if "=" not in kv:
            print(f"--set expects KEY=VALUE, got {kv!r}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        params[k.strip()] = v.strip()

    try:
        rules = load_rules(ROOT / "runbooks" / "runbooks.json")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: runbook problem: {exc}", file=sys.stderr)
        return 2

    if a.scenario:
        d = Path(a.scenario)
        if not (d / "manifest.json").exists():
            d = ROOT / "fixtures" / a.scenario
        if not (d / "manifest.json").exists():
            print(f"Unknown scenario {a.scenario!r}. Available: " + ", ".join(n for n, _ in list_scenarios()), file=sys.stderr)
            return 2
        backend = ReplayBackend(d)
        name = d.name
        verify = dict(verify_timeout=3, poll_seconds=1, settle_seconds=0)
        sleep = lambda s: None                                  # noqa: E731
        ns = backend.manifest.get("namespace", a.namespace)
        print("MODE: RECORDED / SIMULATED - replaying saved kubectl output. No cluster is contacted or changed.")
        print(f"Scenario: {backend.description}")
    else:
        backend = LiveBackend(context=a.context, kubectl=a.kubectl)
        name = "live"
        verify = dict(verify_timeout=a.verify_timeout, poll_seconds=5, settle_seconds=a.settle)
        import time
        sleep = time.sleep
        ns = a.namespace
        print("MODE: LIVE - read-only kubectl diagnostics; any change needs your explicit 'yes'.")

    cfg = Config(namespace=ns, deployment=a.deployment, params=params,
                 approval="no" if a.diagnose_only else "ask",
                 allowed_namespace_patterns=("triage-*", *a.allow_namespace),
                 allow_any_context=a.allow_any_context, **verify)
    out_dir = Path(a.out) if a.out else ROOT / "reports" / name
    agent = TriageAgent(backend, rules, cfg, sleep=sleep, out_dir=out_dir)
    try:
        summary = agent.run()
    except TriageError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 2
    except UnsafeCommand as exc:
        print(f"\nSafety layer stopped the run: {exc}", file=sys.stderr)
        return 2
    report.write_all(summary, out_dir)
    print(f"\nDone. Report: {out_dir / 'diagnosis-report.md'}")
    print(f"      Log:    {out_dir / 'action-log.json'}")
    failed = [r for r in summary.results if r.applied and r.verification and r.verification["status"] != "RECOVERED"]
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
