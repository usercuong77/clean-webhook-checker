import unittest
from unittest.mock import patch

from app_modules.api.controller import CheckRequest, check_input
from app_modules.checkers.check_modes import ModeConfig, normalize_mode
from app_modules.checkers.probe_result import ProbeResult


class Step48CheckModeRouterTests(unittest.TestCase):
    def test_normalize_mode(self):
        self.assertEqual(normalize_mode(None), "1")
        self.assertEqual(normalize_mode(""), "1")
        self.assertEqual(normalize_mode("bad"), "1")
        self.assertEqual(normalize_mode("1"), "1")
        self.assertEqual(normalize_mode("5"), "5")
        self.assertEqual(normalize_mode("ALL"), "all")

    def test_placeholder_modes_return_mode_not_implemented(self):
        expected_sources = {
            "2": "mode2_graph_app_token",
            "3": "mode3_graph_node",
            "4": "mode4_external_checker",
            "5": "mode5_html_fallback",
        }

        for mode, source in expected_sources.items():
            with self.subTest(mode=mode):
                payload = check_input(
                    CheckRequest(input="61574756686411", mode=mode, includeName=False)
                )
                self.assertEqual(payload["status"], "UNKNOWN")
                self.assertEqual(payload["confidence"], "weak")
                self.assertEqual(payload["source"], source)
                self.assertEqual(payload["reason"], "mode_not_implemented")
                self.assertEqual(payload["probes"][-1]["mode"], mode)
                self.assertEqual(payload["probes"][-1]["requestedMode"], mode)
                self.assertFalse(payload["probes"][-1]["implemented"])

    def test_all_routes_to_mode1_but_marks_requested_mode(self):
        calls = []

        def fake_probe(uid):
            calls.append(uid)
            return ProbeResult(
                status="LIVE",
                confidence="strong",
                source="mode1_graph_public",
                reason="graph_profile_picture_dimensions",
                http_code=200,
                details={"height": 100, "width": 100},
            )

        mode_config = ModeConfig(
            mode="1",
            source="mode1_graph_public",
            description="test mode 1",
            implemented=True,
            handler=fake_probe,
        )

        with patch.dict("app_modules.checkers.check_modes.MODE_CONFIGS", {"1": mode_config}):
            payload = check_input(
                CheckRequest(input="61574756686411", mode="all", includeName=False)
            )

        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["source"], "mode1_graph_public")
        self.assertEqual(payload["reason"], "all_currently_mode1_only:graph_profile_picture_dimensions")
        self.assertEqual(payload["probes"][-1]["mode"], "1")
        self.assertEqual(payload["probes"][-1]["requestedMode"], "all")
        self.assertTrue(payload["probes"][-1]["implemented"])
        self.assertEqual(calls, ["61574756686411"])


if __name__ == "__main__":
    unittest.main()
