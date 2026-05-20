import unittest
from unittest.mock import Mock, patch

from app_modules.api.controller import CheckRequest, check_input, check_tick_input
from app_modules.checkers.live_die import LiveDieResult
from app_modules.checkers.probe_result import ProbeResult
from app_modules.features.profile_name import (
    build_profile_name_urls,
    choose_profile_name,
    clear_profile_name_cache,
    extract_profile_name,
    is_valid_profile_name,
    resolve_profile_name,
)
from app_modules.resolvers.facebook_cookies import CookieAccount
from app_modules.resolvers.uid_resolver import ResolvedInput


class Step5ProfileNameTests(unittest.TestCase):
    def setUp(self):
        clear_profile_name_cache()

    def test_extracts_og_title_first(self):
        html = """
        <html>
          <head>
            <meta property="og:title" content="Kiều Anh">
            <title>Facebook</title>
          </head>
        </html>
        """
        self.assertEqual(extract_profile_name(html), "Kiều Anh")

    def test_extracts_title_and_strips_facebook_suffix(self):
        html = "<html><head><title>Nguyễn Liên | Facebook</title></head></html>"
        self.assertEqual(extract_profile_name(html), "Nguyễn Liên")

    def test_rejects_auth_wall_titles(self):
        self.assertFalse(is_valid_profile_name("Facebook"))
        self.assertFalse(is_valid_profile_name("Log in to Facebook"))
        self.assertFalse(is_valid_profile_name("Error"))
        self.assertFalse(is_valid_profile_name("Lỗi"))
        self.assertFalse(is_valid_profile_name("Trình duyệt này không được hỗ trợ"))
        self.assertFalse(is_valid_profile_name("This browser isn't supported"))
        self.assertFalse(is_valid_profile_name("Đăng nhập hoặc đăng ký để xem"))
        self.assertFalse(is_valid_profile_name("Tin nhắn (1)"))
        self.assertFalse(is_valid_profile_name("Messages (1)"))
        self.assertFalse(is_valid_profile_name("123456789"))

    def test_extract_profile_name_skips_facebook_ui_labels(self):
        html = """
        <html>
          <head><title>Tin nhắn (1)</title></head>
          <body><h1>Độ Phùng Verified account</h1></body>
        </html>
        """

        self.assertEqual(extract_profile_name(html), "Độ Phùng Verified account")

    def test_builds_username_and_uid_urls(self):
        resolved = _resolved(uid="100000000000001", username="test.user")
        urls = build_profile_name_urls(resolved)
        self.assertIn("https://www.facebook.com/test.user", urls)
        self.assertIn("https://m.facebook.com/test.user", urls)
        self.assertIn("https://touch.facebook.com/test.user", urls)
        self.assertIn("https://mbasic.facebook.com/test.user", urls)
        self.assertIn("https://www.facebook.com/profile.php?id=100000000000001", urls)

    @patch("app_modules.features.profile_name.load_cookie_accounts", return_value=[])
    def test_resolve_profile_name_falls_back_to_resolver_name(self, load_accounts):
        result = resolve_profile_name(
            _resolved(uid="100000000000001", username="test.user", resolver_name="TDS Name")
        )

        self.assertEqual(result.name, "TDS Name")
        self.assertEqual(result.source, "resolver_name")
        self.assertEqual(result.reason, "name_found_resolver")

    @patch("app_modules.features.profile_name.load_cookie_accounts", return_value=[])
    def test_choose_profile_name_fetches_only_for_live(self, load_accounts):
        live_name = choose_profile_name(
            _resolved(uid="100000000000001", username="test.user", resolver_name="TDS Name"),
            _live_die("LIVE"),
            include_name=True,
        )
        die_name = choose_profile_name(
            _resolved(uid="100000000000002", username="dead.user", resolver_name="Dead Name"),
            _live_die("DIE"),
            include_name=True,
        )

        self.assertEqual(live_name, "TDS Name")
        self.assertEqual(die_name, "")

    @patch("app_modules.checkers.live_die.dispatch_mode")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_text")
    @patch.dict("app_modules.api.controller.os.environ", {"PROFILE_NAME_LOOKUP_ENABLED": "0"}, clear=False)
    def test_checktick_bypasses_default_check_name_gate(self, fetch_text, load_accounts, dispatch_mode):
        load_accounts.return_value = [_cookie_account()]
        fetch_text.return_value = _fetch_result(
            200,
            '100000000000001<meta property="og:title" content="Name Command Tài khoản đã xác minh">',
            "https://www.facebook.com/profile.php?id=100000000000001",
            "ok",
        )
        dispatch_mode.return_value = (
            "1",
            ProbeResult(
                status="LIVE",
                confidence="strong",
                source="mode1_graph_public",
                reason="graph_profile_picture_dimensions",
                http_code=200,
                details={},
            ),
        )

        check_payload = check_input(CheckRequest(input="100000000000001", mode="1", includeName=True))
        tick_payload = check_tick_input(CheckRequest(input="100000000000001", mode="1", includeName=True))

        self.assertEqual(check_payload["name"], "")
        self.assertEqual(tick_payload["name"], "Name Command Tài khoản đã xác minh")
        self.assertEqual(tick_payload["displayName"], "Name Command")
        self.assertTrue(tick_payload["verified"])
        self.assertEqual(tick_payload["verifiedLabel"], "Tài khoản đã xác minh")
        self.assertEqual(tick_payload["nameSource"], "profile_name_cookie")
        self.assertEqual(tick_payload["nameReason"], "name_found_cookie")

    @patch("app_modules.features.profile_name.load_cookie_accounts", return_value=[])
    def test_live_falls_back_to_username_when_name_missing(self, load_accounts):
        name = choose_profile_name(
            _resolved(uid="100000000000001", username="test.user"),
            _live_die("LIVE"),
            include_name=True,
        )

        self.assertEqual(name, "test.user")

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_text")
    def test_cookie_desktop_probe_runs_first(self, fetch_text, load_accounts):
        uid = "100000000000001"
        load_accounts.return_value = [_cookie_account()]
        fetch_text.return_value = _fetch_result(
            200,
            f'{uid}<meta property="og:title" content="Cookie Name">',
            "https://www.facebook.com/test.user",
            "ok",
        )

        result = resolve_profile_name(_resolved(uid=uid, username="test.user"))

        self.assertEqual(result.name, "Cookie Name")
        self.assertEqual(result.source, "profile_name_cookie")
        self.assertEqual(result.reason, "name_found_cookie")
        self.assertTrue(any(probe.get("header") == "desktop_logged_in" for probe in result.probes))
        self.assertEqual(fetch_text.call_count, 1)

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_text")
    def test_cache_reuses_name_by_uid(self, fetch_text, load_accounts):
        load_accounts.return_value = [_cookie_account()]
        fetch_text.return_value = _fetch_result(
            200,
            '100000000000001<meta property="og:title" content="Kiều Anh">',
            "https://www.facebook.com/test.user",
            "ok",
        )
        resolved = _resolved(uid="100000000000001", username="test.user")

        first = choose_profile_name(resolved, _live_die("LIVE"), include_name=True)
        second = choose_profile_name(resolved, _live_die("LIVE"), include_name=True)

        self.assertEqual(first, "Kiều Anh")
        self.assertEqual(second, "Kiều Anh")
        self.assertEqual(fetch_text.call_count, 1)


def _cookie_account():
    return CookieAccount(
        c_user="100000000000099",
        source="test",
        index=0,
        cookies={"c_user": "100000000000099", "xs": "fake-xs-token"},
    )


def _resolved(uid="", username="", resolver_name=""):
    return ResolvedInput(
        input=username or uid,
        uid=uid,
        username=username,
        canonical_url=f"https://www.facebook.com/profile.php?id={uid}" if uid else "",
        source="test",
        reason="test",
        resolver_name=resolver_name,
    )


def _live_die(status):
    return LiveDieResult(
        status=status,
        confidence="strong" if status == "LIVE" else "weak",
        source="test",
        reason="test",
        http_code=200 if status == "LIVE" else 0,
        probes=[],
    )


def _fetch_result(http_code, text, final_url, reason):
    result = Mock()
    result.http_code = http_code
    result.text = text
    result.final_url = final_url
    result.reason = reason
    return result


if __name__ == "__main__":
    unittest.main()
