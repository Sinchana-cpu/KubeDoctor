"""API tests for the web layer. Standard library only."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webapp

class WebApi(unittest.TestCase):
    def test_all_four_findings(self):
        d = webapp.diagnose("all-four", save=False)
        self.assertEqual(len(d["incidents"]), 4)
        self.assertEqual(d["mode"], "Replay")
        self.assertTrue(all(i["confidence"] for i in d["incidents"]))

    def test_incident_has_fix_assets(self):
        d = webapp.diagnose("oomkilled", save=False)
        i = d["incidents"][0]
        self.assertIn("memory", i["patchYaml"])
        self.assertTrue(i["patchCommand"].startswith("kubectl patch deployment"))
        self.assertIn("describe", i["evidenceFiles"])

    def test_pod_overview(self):
        pods = webapp.pod_overview("all-four")
        self.assertEqual(len(pods), 5)
        self.assertTrue(any(p["ready"] for p in pods))

    def test_every_scenario_titled(self):
        self.assertEqual(set(webapp.SCENARIOS), set(webapp.SCENARIO_TITLES))

if __name__ == "__main__":
    unittest.main()
