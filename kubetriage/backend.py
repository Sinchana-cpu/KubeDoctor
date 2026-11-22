"""Cluster backends: LiveBackend (real kubectl) and ReplayBackend (recorded cluster).

Both expose the same interface so the agent does not care which one it has:

    run(args)                    -> Result   read-only commands (allowlisted)
    current_context()            -> str
    apply_patch(dep, ns, patch, dry_run) -> Result   the ONLY mutation
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .safety import check_name, check_readonly


@dataclass
class Result:
    args: list
    rc: int
    out: str = ""
    err: str = ""

    @property
    def text(self):
        return (self.out if self.out.strip() else self.err).strip()

    @property
    def ok(self):
        return self.rc == 0


class LiveBackend:
    """Runs real kubectl. ``kubectl`` may be a string or a list (handy for tests)."""
    name = "live"
    source = "live-kubectl"

    def __init__(self, context=None, kubectl="kubectl", timeout=60):
        self.kubectl = [kubectl] if isinstance(kubectl, str) else list(kubectl)
        self.context = context
        self.timeout = timeout

    def _exec(self, args, timeout=None):
        cmd = [*self.kubectl, *(["--context", self.context] if self.context else []), *args]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout or self.timeout)
            return Result(list(args), p.returncode, p.stdout, p.stderr)
        except FileNotFoundError:
            return Result(list(args), 127, "", "kubectl was not found on PATH. Install it or use --replay.")
        except subprocess.TimeoutExpired:
            return Result(list(args), 124, "", f"Timed out after {timeout or self.timeout}s")

    def run(self, args):
        check_readonly(args)
        # `rollout status --timeout=120s` can legitimately run long; give it headroom.
        t = None
        for a in args:
            if a.startswith("--timeout=") and a.endswith("s") and a[10:-1].isdigit():
                t = int(a[10:-1]) + 15
        return self._exec(args, timeout=t)

    def current_context(self):
        if self.context:
            return self.context
        r = self.run(["config", "current-context"])
        return r.text if r.ok else "(unknown)"

    def apply_patch(self, deployment, namespace, patch, dry_run=False):
        check_name(deployment, "deployment name")
        check_name(namespace, "namespace")
        args = ["patch", "deployment", deployment, "-n", namespace, "--type", "strategic",
                "-p", json.dumps(patch)]
        if dry_run:
            args.append("--dry-run=server")
        return self._exec(args)


class RecordedModeError(Exception):
    """Recorded (fixture) mode can never change a cluster."""


class ReplayBackend:
    """Serves RECORDED kubectl output from a fixture directory. Never mutates anything.

    manifest.json: {"commands": {"get pods -n ns -o json": "before/pods.json", ...}}
    Commands that were not recorded return an explicit "not recorded" error, so the
    agent can say what evidence is missing instead of inventing it.
    """
    name = "replay"
    source = "recorded-fixture"

    def __init__(self, fixture_dir):
        self.dir = Path(fixture_dir)
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))

    @property
    def description(self):
        return self.manifest.get("description", "")

    @property
    def scenario(self):
        return self.manifest.get("scenario", self.dir.name)

    def run(self, args):
        check_readonly(args)
        key = " ".join(args)
        if args[:2] == ["config", "current-context"]:
            return Result(list(args), 0, self.current_context() + "\n")
        rel = self.manifest.get("commands", {}).get(key)
        if rel is None:
            return Result(list(args), 1, "", f"(not recorded in fixture '{self.scenario}'): kubectl {key}")
        return Result(list(args), 0, (self.dir / rel).read_text(encoding="utf-8"))

    def current_context(self):
        return self.manifest.get("context", "recorded-cluster")

    def apply_patch(self, deployment, namespace, patch, dry_run=False):
        raise RecordedModeError("Recorded mode never applies patches.")
