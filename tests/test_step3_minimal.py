import unittest
from pathlib import Path
from unittest.mock import patch

from app_modules.api.controller import CheckRequest, check_input, health_payload
from app_modules.resolvers.facebook_uid_resolver import FetchResult
from app_modules.resolvers.uid_resolver import resolve_input


class Step3MinimalTests(unittest.TestCase):
    def test_health_payload(self):
        payload = health_payload()
        self.assertTrue(payload["ok"])
        self.assertIn("service", payload)
        self.assertIn("version", payload)

    def test_direct_uid_response_shape(self):
        payload = check_input(CheckRequest(input="100012345678901", mode="2", includeName=True))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "UNKNOWN")
        self.assertEqual(payload["confidence"], "weak")
        self.assertEqual(payload["uid"], "100012345678901")
        self.assertEqual(payload["username"], "")
        self.assertEqual(payload["canonicalUrl"], "https://www.facebook.com/profile.php?id=100012345678901")
        self.assertIn("source", payload)
        self.assertIn("reason", payload)
        self.assertIsInstance(payload["elapsedMs"], int)
        self.assertIsInstance(payload["probes"], list)
        self.assertIn("resolverDebug", payload)
        self.assertEqual(payload["resolverDebug"]["uid"], "100012345678901")

    def test_profile_php_resolver(self):
        resolved = resolve_input("https://facebook.com/profile.php?id=100012345678901")
        self.assertEqual(resolved.uid, "100012345678901")
        self.assertEqual(resolved.source, "profile_php")

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    @patch("app_modules.resolvers.facebook_cookies.DEFAULT_LOCAL_COOKIE_FILE", Path("Z:/missing/facebook_cookies.txt"))
    def test_username_without_uid_is_die(self, fetch_text):
        fetch_text.return_value = FetchResult(200, "<html></html>", "https://facebook.com/lvmedits", "ok")

        payload = check_input(CheckRequest(input="https://facebook.com/lvmedits", mode="1", includeName=True))
        self.assertEqual(payload["status"], "DIE")
        self.assertEqual(payload["username"], "lvmedits")
        self.assertEqual(payload["name"], "")
        self.assertEqual(payload["source"], "uid_resolver")
        self.assertEqual(payload["reason"], "uid_not_found_after_public_probe_no_cookie_accounts")
        self.assertGreater(payload["resolverDebug"]["probeCount"], 0)
        self.assertIn("uid_html_probe", payload["resolverDebug"]["sources"])

    def test_invalid_link_is_die_when_uid_not_found(self):
        payload = check_input(CheckRequest(input="https://example.com/not-facebook", mode="all", includeName=True))
        self.assertEqual(payload["status"], "DIE")
        self.assertEqual(payload["confidence"], "weak")
        self.assertEqual(payload["uid"], "")
        self.assertEqual(payload["source"], "uid_resolver")

    def test_numeric_path_resolver(self):
        resolved = resolve_input("http://facebook.com/61574756686411")
        self.assertEqual(resolved.uid, "61574756686411")
        self.assertEqual(resolved.source, "numeric_path")
        self.assertEqual(resolved.reason, "path_uid")

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    @patch("app_modules.resolvers.facebook_cookies.DEFAULT_LOCAL_COOKIE_FILE", Path("Z:/missing/facebook_cookies.txt"))
    def test_share_link_without_uid_is_die(self, fetch_text):
        fetch_text.return_value = FetchResult(
            200,
            "<html></html>",
            "https://www.facebook.com/share/1Ay9R878jq/",
            "ok",
        )

        payload = check_input(CheckRequest(input="https://www.facebook.com/share/1Ay9R878jq/", mode="1", includeName=True))
        self.assertEqual(payload["status"], "DIE")
        self.assertEqual(payload["confidence"], "weak")
        self.assertEqual(payload["uid"], "")
        self.assertEqual(payload["source"], "uid_resolver")
        self.assertEqual(payload["reason"], "uid_not_found_after_public_probe_no_cookie_accounts")

    def test_mode1_live_probe_wiring(self):
        from app_modules.checkers.check_modes import ModeConfig
        from app_modules.checkers.probe_result import ProbeResult

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
            payload = check_input(CheckRequest(input="61574756686411", mode="1", includeName=True))

        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["confidence"], "strong")
        self.assertEqual(payload["source"], "mode1_graph_public")
        self.assertEqual(payload["httpCode"], 200)
        self.assertEqual(payload["uid"], "61574756686411")
        self.assertEqual(calls, ["61574756686411"])


if __name__ == "__main__":
    unittest.main()
