#!/usr/bin/env python3
"""A stand-in for kubectl used ONLY by the test-suite to exercise LiveBackend.

It serves the recorded 'crashloop-missing-env' fixture, logs every call to
$FAKE_KUBECTL_LOG, and pretends a `patch` fixes the pod (unless FAKE_KUBECTL_BREAK=1).
"""
import json, os, sys
from pathlib import Path

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "crashloop-missing-env"
args = sys.argv[1:]
if args[:1] == ["--context"]:
    args = args[2:]
with open(os.environ["FAKE_KUBECTL_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\n")
state = Path(os.environ["FAKE_KUBECTL_LOG"] + ".patched")
manifest = json.loads((FIX / "manifest.json").read_text())
key = " ".join(args)

if args[:2] == ["config", "current-context"]:
    print(os.environ.get("FAKE_KUBECTL_CONTEXT", "kind-triage-lab")); sys.exit(0)
if args[0] == "patch":
    if "--dry-run=server" in args:
        print("deployment.apps/crashy-api patched (server dry run)"); sys.exit(0)
    if os.environ.get("FAKE_KUBECTL_BREAK") != "1":
        state.write_text("1")
    print("deployment.apps/crashy-api patched"); sys.exit(0)
if args[:2] == ["rollout", "status"]:
    if state.exists():
        print('deployment "crashy-api" successfully rolled out'); sys.exit(0)
    print("error: timed out waiting for the condition", file=sys.stderr); sys.exit(1)
if state.exists() and args[:2] == ["get", "pods"] and "-l" in args:
    pod = json.loads((FIX / "before/selector-crashy-api.json").read_text())["items"][0]
    pod["metadata"]["name"] = "crashy-api-new-pod"
    pod["status"] = {"phase": "Running", "containerStatuses": [
        {"name": "api", "ready": True, "restartCount": 0, "state": {"running": {}}}]}
    print(json.dumps({"items": [pod]})); sys.exit(0)
rel = manifest["commands"].get(key)
if rel is None:
    print("Error from server (NotFound): not recorded: " + key, file=sys.stderr); sys.exit(1)
sys.stdout.write((FIX / rel).read_text())
