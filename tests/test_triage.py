import io, json, os, shutil, sys, tempfile, unittest, contextlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kubetriage import commands as C, detect, patches, report, units       # noqa: E402
from kubetriage.agent import Config, TriageAgent, TriageError              # noqa: E402
from kubetriage.backend import LiveBackend, RecordedModeError, ReplayBackend  # noqa: E402
from kubetriage.kb import load_rules                                       # noqa: E402
from kubetriage.safety import UnsafeCommand, check_readonly, redact       # noqa: E402

RULES = load_rules(ROOT / "runbooks" / "runbooks.json")
QUIET = lambda *a, **k: None                                               # noqa: E731
PARAMS = {"env.APP_MODE": "staging"}


def recorded(name, params=None, fixture_dir=None):
    b = ReplayBackend(fixture_dir or ROOT / "fixtures" / name)
    cfg = Config(namespace="triage-lab", params=dict(params or {}), approval="ask")
    return TriageAgent(b, RULES, cfg, say=QUIET).run(), b


class Recorded(unittest.TestCase):
    EXPECT = {"image-pull-backoff": "image-not-found", "crashloop-missing-env": "crash-missing-config",
              "oomkilled": "oom-killed", "pending-insufficient-cpu": "pending-insufficient-resources"}

    def test_each_scenario_diagnosed_with_patch(self):
        for name, rule in self.EXPECT.items():
            with self.subTest(name):
                s, _ = recorded(name, PARAMS)
                self.assertEqual(len(s.results), 1)
                wr = s.results[0]
                self.assertEqual(wr.best.rule["id"], rule)
                self.assertEqual(wr.best.confidence, "High")
                self.assertTrue(wr.plan.ready, wr.plan.reason_none)
                self.assertFalse(wr.applied)
                self.assertIsNone(wr.verification)

    def test_expected_patch_values(self):
        vals = {"image-pull-backoff": "nginx:1.27-alpine", "oomkilled": "200Mi", "pending-insufficient-cpu": "800m",
                "crashloop-missing-env": "staging"}
        for name, v in vals.items():
            s, _ = recorded(name, PARAMS)
            self.assertIn(v, json.dumps(s.results[0].plan.patch))

    def test_all_four_and_healthy_pod_ignored(self):
        s, _ = recorded("all-four", PARAMS)
        self.assertEqual(sorted(r.problem.deployment for r in s.results),
                         ["broken-web", "crashy-api", "hungry-batch", "leaky-worker"])
        self.assertEqual(s.pods_seen, 5)

    def test_recorded_mode_never_mutates_and_labels_source(self):
        s, b = recorded("all-four", PARAMS)
        kinds = {e["kind"] for e in s.log.entries}
        self.assertFalse(kinds & {"mutation", "dry-run", "approval"})
        for e in s.log.entries:
            if e["kind"] == "read":
                self.assertEqual(e["source"], "recorded-fixture")
        with self.assertRaises(RecordedModeError):
            b.apply_patch("x", "triage-lab", {})
        for r in s.results:
            self.assertIn("recorded mode never applies", r.approval)

    def test_report_labels_recorded_and_never_claims_recovery(self):
        s, _ = recorded("all-four", PARAMS)
        with tempfile.TemporaryDirectory() as d:
            report.write_all(s, d)
            md = (Path(d) / "diagnosis-report.md").read_text(encoding="utf-8")
            self.assertIn("RECORDED / SIMULATED", md)
            self.assertNotIn("**Verification:** RECOVERED", md)
            self.assertIn("recovery is NOT claimed", md)
            log = json.loads((Path(d) / "action-log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["summary"]["mutations"], 0)
            self.assertEqual(log["summary"]["recovered"], 0)
            self.assertTrue((Path(d) / "proposed-fixes" / "leaky-worker.yaml").exists())

    def test_missing_value_is_not_guessed(self):
        s, _ = recorded("crashloop-missing-env")
        plan = s.results[0].plan
        self.assertEqual(plan.needs_input, ["env.APP_MODE"])
        self.assertIsNone(plan.patch if plan.needs_input and not plan.ready else None)
        with tempfile.TemporaryDirectory() as d:
            report.write_all(s, d)
            self.assertFalse((Path(d) / "proposed-fixes").exists())
            self.assertIn("--set env.APP_MODE", (Path(d) / "diagnosis-report.md").read_text(encoding="utf-8"))

    def test_insufficient_evidence_is_stated(self):
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / "fx"
            shutil.copytree(ROOT / "fixtures" / "crashloop-missing-env", dst)
            m = json.loads((dst / "manifest.json").read_text())
            m["commands"] = {k: v for k, v in m["commands"].items() if " logs " not in " " + k + " "}
            (dst / "manifest.json").write_text(json.dumps(m))
            s, _ = recorded(None, PARAMS, dst)
            wr = s.results[0]
            self.assertTrue(any("logs" in x for x in wr.missing))
            self.assertEqual(wr.best.confidence, "Low")
            self.assertFalse(wr.plan.ready)
            out = Path(d) / "out"
            report.write_all(s, out)
            self.assertIn("Evidence is incomplete", (out / "diagnosis-report.md").read_text(encoding="utf-8"))

    def test_no_problem_when_all_healthy(self):
        pods = json.loads((ROOT / "fixtures/all-four/before/pods.json").read_text())
        pods["items"] = [p for p in pods["items"] if p["metadata"]["name"].startswith("web-ok")]
        self.assertEqual(detect.find_problems(pods), [])

    def test_patch_yaml_is_valid(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed (it is not a project dependency)")
        s, _ = recorded("all-four", PARAMS)
        for r in s.results:
            self.assertEqual(yaml.safe_load(patches.to_yaml(r.plan.patch)), r.plan.patch)
        for f in (ROOT / "workloads").glob("*.yaml"):
            list(yaml.safe_load_all(f.read_text()))


class Safety(unittest.TestCase):
    def test_blocked(self):
        for bad in (["delete", "pod", "x"], ["apply", "-f", "x"], ["exec", "p", "--", "sh"], ["patch", "deployment", "x"],
                    ["get", "secrets", "-n", "x"], ["get", "secret/foo"], ["rollout", "restart", "deployment/x"],
                    ["get", "pods", "--token=abc"], ["config", "use-context", "prod"], []):
            with self.assertRaises(UnsafeCommand, msg=str(bad)):
                check_readonly(bad)

    def test_allowed(self):
        for ok in (C.get_pods("ns"), C.describe_pod("ns", "p"), C.pod_logs("ns", "p", "c", True),
                   C.get_deployment("ns", "d"), C.rollout_status("ns", "d"), C.get_nodes(), C.pod_events("ns", "p")):
            self.assertTrue(check_readonly(ok))

    def test_redaction(self):
        t = redact('DB_PASSWORD=hunter2 token: abcdef Authorization: Bearer abcdefgh12345 url=postgres://u:pw@h/db '
                   '{"name": "API_KEY", "value": "zzz"} APP_MODE=staging')
        for leak in ("hunter2", "abcdef ", "abcdefgh12345", "u:pw", "zzz"):
            self.assertNotIn(leak, t)
        self.assertIn("APP_MODE=staging", t)

    def test_units(self):
        self.assertEqual(units.fmt_mem(units.parse_mem("100Mi") * 2), "200Mi")
        self.assertEqual(units.parse_cpu("64"), 64000)
        self.assertEqual(units.fmt_cpu(800), "800m")
        self.assertEqual(units.parse_mem("16254636Ki") // 2**20, 15873)


class Live(unittest.TestCase):
    """LiveBackend + agent against tests/fake_kubectl.py (a stub; no real cluster)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "calls.log")
        os.environ["FAKE_KUBECTL_LOG"] = self.log
        for k in ("FAKE_KUBECTL_BREAK", "FAKE_KUBECTL_CONTEXT"):
            os.environ.pop(k, None)
        self.kubectl = [sys.executable, str(ROOT / "tests" / "fake_kubectl.py")]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        return Path(self.log).read_text().splitlines() if Path(self.log).exists() else []

    def mutated(self):
        return [c for c in self.calls() if c.startswith("patch") and "--dry-run" not in c]

    def run_live(self, answer, ns="triage-lab", dep="crashy-api"):
        answers = iter(answer if isinstance(answer, list) else [answer])

        def fake_input(prompt):
            try:
                v = next(answers)
            except StopIteration:
                raise EOFError
            if isinstance(v, Exception):
                raise v
            return v
        cfg = Config(namespace=ns, deployment=dep, params=dict(PARAMS), approval="ask", input_fn=fake_input,
                     verify_timeout=2, poll_seconds=1, settle_seconds=0)
        agent = TriageAgent(LiveBackend(kubectl=self.kubectl), RULES, cfg, say=QUIET, sleep=QUIET)
        with contextlib.redirect_stdout(io.StringIO()):
            return agent.run()

    def test_yes_applies_and_verifies(self):
        s = self.run_live("yes")
        wr = s.results[0]
        self.assertTrue(wr.applied)
        self.assertEqual(wr.verification["status"], "RECOVERED")
        self.assertEqual(len(self.mutated()), 1)
        self.assertTrue(any("--dry-run=server" in c for c in self.calls()))
        # dry run happens before the real patch
        calls = [c for c in self.calls() if c.startswith("patch")]
        self.assertIn("--dry-run=server", calls[0])
        self.assertTrue(all(e["source"] == "live-kubectl" for e in s.log.entries if e["kind"] in ("read", "mutation")))

    def test_declines_leave_cluster_unchanged(self):
        for ans in ("n", "no", "", "maybe", "ok sure", EOFError()):
            with self.subTest(repr(ans)):
                Path(self.log).unlink(missing_ok=True)
                s = self.run_live(ans)
                wr = s.results[0]
                self.assertFalse(wr.applied)
                self.assertEqual(self.mutated(), [])
                self.assertIsNone(wr.verification)
                self.assertTrue(any(e["kind"] == "approval" and "declined" in e["result"] for e in s.log.entries))

    def test_not_recovered_when_verification_fails(self):
        os.environ["FAKE_KUBECTL_BREAK"] = "1"
        s = self.run_live("yes")
        wr = s.results[0]
        self.assertTrue(wr.applied)
        self.assertEqual(wr.verification["status"], "NOT_RECOVERED")
        self.assertIn("rollout undo", wr.rollback)
        with tempfile.TemporaryDirectory() as d:
            report.write_all(s, d)
            md = (Path(d) / "diagnosis-report.md").read_text(encoding="utf-8")
            self.assertIn("NOT RECOVERED", md)
            self.assertNotIn("**Verification:** RECOVERED", md)

    def test_context_guard(self):
        os.environ["FAKE_KUBECTL_CONTEXT"] = "gke_prod_cluster"
        s = self.run_live("yes")
        self.assertFalse(s.results[0].applied)
        self.assertEqual(self.mutated(), [])
        self.assertIn("refused", s.results[0].approval)

    def test_namespace_and_workload_limits(self):
        with self.assertRaises(TriageError):
            self.run_live("yes", ns="production")
        with self.assertRaises(TriageError):
            self.run_live("yes", dep=None)
        self.assertEqual(self.mutated(), [])

    def test_wrong_workload_has_nothing_to_do(self):
        s = self.run_live("yes", dep="someone-else")
        self.assertEqual(s.results, [])
        self.assertEqual(self.mutated(), [])


if __name__ == "__main__":
    unittest.main()
