import unittest
from unittest.mock import Mock, patch

from app_modules.api.controller import CheckRequest, check_input, check_tick_input
from app_modules.checkers.live_die import LiveDieResult
from app_modules.checkers.probe_result import ProbeResult
from app_modules.features import profile_name as profile_name_module
from app_modules.features.profile_name import (
    build_profile_name_urls,
    choose_profile_name,
    clear_profile_name_cache,
    extract_profile_name,
    extract_profile_verified_label,
    is_valid_profile_name,
    resolve_profile_tick_from_input,
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

    def test_extracts_verified_from_profile_markers(self):
        self.assertEqual(
            extract_profile_verified_label(
                'profile_header_renderer Độ Phùng "show_verified_badge_on_profile":true',
                "Độ Phùng",
            ),
            "Tài khoản đã xác minh",
        )
        self.assertEqual(
            extract_profile_verified_label('ProfileCometHeader "is_verified":true', "Độ Phùng"),
            "Tài khoản đã xác minh",
        )

    def test_verified_marker_ignores_comment_context(self):
        html = """
        profile_header_renderer <h1>Nth Wag</h1>
        CometUFIComment comment_author "is_verified":true Verified account
        """

        self.assertEqual(extract_profile_verified_label(html, "Nth Wag"), "")

    def test_builds_username_and_uid_urls(self):
        resolved = _resolved(uid="100000000000001", username="test.user")
        urls = build_profile_name_urls(resolved)
        self.assertIn("https://www.facebook.com/test.user", urls)
        self.assertIn("https://m.facebook.com/test.user", urls)
        self.assertIn("https://touch.facebook.com/test.user", urls)
        self.assertIn("https://mbasic.facebook.com/test.user", urls)
        self.assertIn("https://www.facebook.com/profile.php?id=100000000000001", urls)

    def test_checktick_normalizes_profile_uid_input(self):
        target = profile_name_module._normalize_profile_tick_input(
            " https://www.facebook.com/profile.php?id=1000037073983819&sk=about# "
        )

        self.assertEqual(target, "https://www.facebook.com/profile.php?id=1000037073983819")

    def test_checktick_normalizes_username_input(self):
        target = profile_name_module._normalize_profile_tick_input(
            "https://m.facebook.com/thanh.duyen.37570?comment_id=abc#"
        )

        self.assertEqual(target, "https://www.facebook.com/thanh.duyen.37570")

    def test_checktick_normalizes_login_next_input(self):
        target = profile_name_module._normalize_profile_tick_input(
            "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fprofile.php%3Fid%3D100003717317472%26sk%3Dabout"
        )

        self.assertEqual(target, "https://www.facebook.com/profile.php?id=100003717317472")

    def test_checktick_keeps_share_input_canonical(self):
        target = profile_name_module._normalize_profile_tick_input(
            "https://www.facebook.com/share/18NhB6zRpS/?comment_id=abc#frag"
        )

        self.assertEqual(target, "https://www.facebook.com/share/18NhB6zRpS")

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
    @patch("app_modules.features.profile_name._fetch_limited_text")
    @patch.dict("app_modules.api.controller.os.environ", {"PROFILE_NAME_LOOKUP_ENABLED": "0"}, clear=False)
    def test_checktick_bypasses_default_check_name_gate(self, fetch_limited, dispatch_mode):
        fetch_limited.return_value = _fetch_result(
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
        self.assertEqual(tick_payload["name"], "Name Command")
        self.assertEqual(tick_payload["displayName"], "Name Command")
        self.assertTrue(tick_payload["verified"])
        self.assertEqual(tick_payload["verifiedLabel"], "Tài khoản đã xác minh")
        self.assertEqual(tick_payload["nameSource"], "profile_tick_no_cookie")
        self.assertEqual(tick_payload["checkTickMode"], "no_cookie")

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_returns_no_cookie_result_without_cookie_fallback(self, fetch_limited, load_accounts):
        load_accounts.return_value = [_cookie_account()]
        fetch_limited.return_value = _fetch_result(
            200,
            '<meta property="og:title" content="No Cookie Name">',
            "https://www.facebook.com/no.cookie",
            "ok",
        )

        result = check_tick_input(CheckRequest(input="https://www.facebook.com/no.cookie", mode="1", includeName=True))

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "No Cookie Name")
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")
        self.assertEqual(load_accounts.call_count, 0)

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_no_cookie_continues_after_name_only_until_verified(self, fetch_limited, load_accounts):
        load_accounts.return_value = [_cookie_account()]
        fetch_limited.side_effect = [
            _fetch_result(
                200,
                '<meta property="og:title" content="No Cookie Name">',
                "https://www.facebook.com/no.cookie",
                "ok",
            ),
            _fetch_result(
                200,
                '<meta property="og:title" content="No Cookie Name Verified account">',
                "https://www.facebook.com/no.cookie/about",
                "ok",
            ),
        ]

        result = check_tick_input(CheckRequest(input="https://www.facebook.com/no.cookie", mode="1", includeName=True))

        self.assertEqual(result["name"], "No Cookie Name")
        self.assertTrue(result["verified"])
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")
        self.assertEqual(fetch_limited.call_count, 2)
        self.assertEqual(load_accounts.call_count, 0)

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_force_cookie_continues_after_name_only_until_verified(self, fetch_limited, load_accounts):
        load_accounts.return_value = [_cookie_account()]
        fetch_limited.side_effect = [
            _fetch_result(
                200,
                '<meta property="og:title" content="Cookie Name">',
                "https://www.facebook.com/cookie.only",
                "ok",
            ),
            _fetch_result(
                200,
                '<meta property="og:title" content="Cookie Name Verified account">',
                "https://www.facebook.com/cookie.only/about",
                "ok",
            ),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/cookie.only", mode="1", includeName=True, forceCookie=True)
        )

        self.assertEqual(result["name"], "Cookie Name")
        self.assertTrue(result["verified"])
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")
        self.assertEqual(fetch_limited.call_count, 2)

    def test_checktick_public_candidates_use_fast_profile_urls_only(self):
        candidates = profile_name_module._public_tick_probe_candidates(
            "https://www.facebook.com/fast.user",
            "",
            "fast.user",
        )

        self.assertEqual(
            [url for url, _headers, _label in candidates],
            [
                "https://www.facebook.com/fast.user",
                "https://www.facebook.com/fast.user",
                "https://www.facebook.com/fast.user/about",
                "https://www.facebook.com/fast.user/about",
            ],
        )
        self.assertEqual(
            [label for _url, _headers, label in candidates],
            ["facebookcatalog", "facebookexternalhit", "facebookcatalog", "facebookexternalhit"],
        )

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_default_cookie_fallback_can_confirm_verified_on_second_cookie(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        load_accounts.return_value = [_cookie_account("100000000000001"), _cookie_account("100000000000002")]
        public_candidates.return_value = [("https://www.facebook.com/no.public.name", {}, "public")]
        cookie_candidates.return_value = [("https://www.facebook.com/no.public.name", {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/no.public.name", "ok"),
            _fetch_result(
                200,
                '<meta property="og:title" content="Cookie Name">',
                "https://www.facebook.com/no.public.name",
                "ok",
            ),
            _fetch_result(
                200,
                '<meta property="og:title" content="Cookie Name Verified account">',
                "https://www.facebook.com/no.public.name",
                "ok",
            ),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/no.public.name", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "Cookie Name")
        self.assertTrue(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(fetch_limited.call_count, 3)
        self.assertEqual(cookie_candidates.call_count, 2)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_default_cookie_fallback_stops_when_first_cookie_has_no_name(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        load_accounts.return_value = [_cookie_account("100000000000001"), _cookie_account("100000000000002")]
        public_candidates.return_value = [("https://www.facebook.com/no.public.name", {}, "public")]
        cookie_candidates.return_value = [("https://www.facebook.com/no.public.name", {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/no.public.name", "ok"),
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/no.public.name", "ok"),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/no.public.name", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "DIE")
        self.assertFalse(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(result["reason"], "no_cookie_and_cookie_name_not_found")
        self.assertEqual(fetch_limited.call_count, 2)
        self.assertEqual(cookie_candidates.call_count, 1)

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_profile_php_retries_redirected_username_about(self, fetch_limited, load_accounts):
        load_accounts.return_value = [_cookie_account()]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/profile.php?id=100037073983819", "ok"),
            _fetch_result(
                200,
                "<title>Facebook</title>",
                "https://www.facebook.com/profile.php?id=100037073983819&sk=about",
                "ok",
            ),
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/thanh.duyen.37570/", "ok"),
            _fetch_result(
                200,
                "<title>Facebook</title>",
                "https://www.facebook.com/thanh.duyen.37570/about/?id=100037073983819&sk=about",
                "ok",
            ),
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/thanh.duyen.37570", "ok"),
            _fetch_result(
                200,
                '<meta property="og:title" content="Thanh Duyen">',
                "https://www.facebook.com/thanh.duyen.37570/about",
                "ok",
            ),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/profile.php?id=100037073983819", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "Thanh Duyen")
        self.assertEqual(result["username"], "thanh.duyen.37570")
        self.assertTrue(result["usedCookie"])
        self.assertEqual(fetch_limited.call_count, 6)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_share_retries_clean_profile_uid_redirect_for_verified(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        profile_url = "https://www.facebook.com/profile.php?id=100003717317472"
        share_redirect = (
            "https://www.facebook.com/profile.php?id=100003717317472"
            "&rdid=abc&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2F18NhB6zRpS%2F"
        )
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/share/18NhB6zRpS/", {}, "public")]

        def cookie_candidate_urls(normalized, uid, username, account):
            return [(normalized, {}, "cookie")]

        cookie_candidates.side_effect = cookie_candidate_urls
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", share_redirect, "ok"),
            _fetch_result(200, "<title>Facebook</title>", share_redirect, "ok"),
            _fetch_result(
                200,
                '<meta property="og:title" content="Độ Phùng Tài khoản đã xác minh">',
                profile_url,
                "ok",
            ),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/share/18NhB6zRpS/", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["uid"], "100003717317472")
        self.assertEqual(result["name"], "Độ Phùng")
        self.assertTrue(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(cookie_candidates.call_args_list[-1].args[0], profile_url)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_force_cookie_can_confirm_verified_on_second_cookie(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        load_accounts.return_value = [_cookie_account("100000000000001"), _cookie_account("100000000000002")]
        public_candidates.return_value = [("https://www.facebook.com/cookie.verify", {}, "public")]
        cookie_candidates.return_value = [("https://www.facebook.com/cookie.verify", {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/cookie.verify", "ok"),
            _fetch_result(
                200,
                '<meta property="og:title" content="Cookie Name">',
                "https://www.facebook.com/cookie.verify",
                "ok",
            ),
            _fetch_result(
                200,
                '<meta property="og:title" content="Cookie Name Verified account">',
                "https://www.facebook.com/cookie.verify",
                "ok",
            ),
        ]

        result = check_tick_input(
            CheckRequest(
                input="https://www.facebook.com/cookie.verify",
                mode="1",
                includeName=True,
                forceCookie=True,
            )
        )

        self.assertEqual(result["name"], "Cookie Name")
        self.assertTrue(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(fetch_limited.call_count, 3)
        self.assertEqual(cookie_candidates.call_count, 2)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_retries_login_next_target_before_cookie_fallback(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        target = "https://www.facebook.com/thanh.duyen.37570"
        login_url = (
            "https://www.facebook.com/login/?next="
            "https%3A%2F%2Fwww.facebook.com%2Fthanh.duyen.37570%3Frdid%3Dabc"
            "%26share_url%3Dhttps%253A%252F%252Fwww.facebook.com%252Fshare%252F1BUu51wPpb%252F"
        )
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/share/1BUu51wPpb/", {}, "public")]
        cookie_candidates.return_value = [(target, {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", login_url, "ok"),
            _fetch_result(200, "<title>Facebook</title>", login_url, "ok"),
            _fetch_result(200, "<title>Facebook</title>", target + "/about", "ok"),
            _fetch_result(200, '<meta property="og:title" content="Thanh Duyen">', target, "ok"),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/share/1BUu51wPpb/", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "Thanh Duyen")
        self.assertTrue(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "cookie")
        self.assertEqual(cookie_candidates.call_args.args[0], target)
        self.assertEqual(fetch_limited.call_count, 4)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_retries_same_login_next_target_per_header(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        target = "https://www.facebook.com/tintucvtv24"
        login_url = "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Ftintucvtv24"
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [
            ("https://www.facebook.com/share/17Q7NRNi2T/", {}, "facebookcatalog"),
            ("https://www.facebook.com/share/17Q7NRNi2T/", {}, "facebookexternalhit"),
        ]
        cookie_candidates.return_value = [(target, {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", login_url, "ok"),
            _fetch_result(200, "<title>Facebook</title>", target, "ok"),
            _fetch_result(200, "<title>Facebook</title>", target + "/about", "ok"),
            _fetch_result(200, "<title>Facebook</title>", login_url, "ok"),
            _fetch_result(200, '<meta property="og:title" content="VTV24 Verified account">', target, "ok"),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/share/17Q7NRNi2T/", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "VTV24")
        self.assertTrue(result["verified"])
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")
        self.assertEqual(fetch_limited.call_count, 5)
        cookie_candidates.assert_not_called()

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_login_next_404_falls_back_to_cookie(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        target = "https://www.facebook.com/vtvgiaitri"
        login_url = "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fvtvgiaitri"
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/vtvgiaitri", {}, "facebookcatalog")]
        cookie_candidates.return_value = [(target, {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", login_url, "ok"),
            _fetch_result(404, "not found", target, "ok"),
            _fetch_result(404, "not found", target + "/about", "ok"),
            _fetch_result(200, '<meta property="og:title" content="VTV Giai tri Verified account">', target, "ok"),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/vtvgiaitri", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertTrue(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "cookie")
        self.assertEqual(fetch_limited.call_count, 4)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_share_name_only_confirms_with_cookie(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        target = "https://www.facebook.com/tintucvtv24"
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/share/17Q7NRNi2T/", {}, "facebookcatalog")]
        cookie_candidates.return_value = [(target, {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, '<meta property="og:title" content="VTV24">', target, "ok"),
            _fetch_result(200, '<meta property="og:title" content="VTV24 Verified account">', target, "ok"),
        ]

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/share/17Q7NRNi2T/", mode="1", includeName=True)
        )

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "VTV24")
        self.assertTrue(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "cookie")
        self.assertEqual(fetch_limited.call_count, 2)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_profile_uid_keeps_public_name_when_cookie_empty(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        profile_url = "https://www.facebook.com/profile.php?id=61561467565550"
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [(profile_url, {}, "facebookcatalog")]
        cookie_candidates.return_value = [(profile_url, {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, '<meta property="og:title" content="Do Phung">', profile_url, "ok"),
            _fetch_result(200, "<title>Facebook</title>", profile_url, "ok"),
        ]

        result = check_tick_input(CheckRequest(input=profile_url, mode="1", includeName=True))

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["name"], "Do Phung")
        self.assertFalse(result["verified"])
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_force_cookie_retries_login_next_target(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        target = "https://www.facebook.com/thanh.duyen.37570"
        login_url = "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fthanh.duyen.37570"
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/share/1BUu51wPpb/", {}, "public")]
        cookie_candidates.return_value = [("https://www.facebook.com/share/1BUu51wPpb/", {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", login_url, "ok"),
            _fetch_result(200, "<title>Facebook</title>", target, "ok"),
            _fetch_result(200, "<title>Facebook</title>", target + "/about", "ok"),
            _fetch_result(200, '<meta property="og:title" content="Thanh Duyen Verified account">', target, "ok"),
        ]

        result = check_tick_input(
            CheckRequest(
                input="https://www.facebook.com/share/1BUu51wPpb/",
                mode="1",
                includeName=True,
                forceCookie=True,
            )
        )

        self.assertEqual(result["name"], "Thanh Duyen")
        self.assertTrue(result["verified"])
        self.assertTrue(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "cookie")
        self.assertEqual(fetch_limited.call_count, 4)

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_reports_cookie_fallback_failure_reason(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/no.name", {}, "public")]
        cookie_candidates.return_value = [("https://www.facebook.com/no.name", {}, "cookie")]
        fetch_limited.side_effect = [
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/no.name", "ok"),
            _fetch_result(200, "<title>Facebook</title>", "https://www.facebook.com/no.name", "ok"),
        ]

        result = check_tick_input(CheckRequest(input="https://www.facebook.com/no.name", mode="1", includeName=True))

        self.assertEqual(result["status"], "DIE")
        self.assertTrue(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "cookie")
        self.assertEqual(result["reason"], "no_cookie_and_cookie_name_not_found")

    @patch("app_modules.features.profile_name._cookie_tick_probe_candidates")
    @patch("app_modules.features.profile_name._public_tick_probe_candidates")
    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_public_unavailable_skips_cookie_fallback(
        self,
        fetch_limited,
        load_accounts,
        public_candidates,
        cookie_candidates,
    ):
        load_accounts.return_value = [_cookie_account()]
        public_candidates.return_value = [("https://www.facebook.com/missing.profile", {}, "public")]
        cookie_candidates.return_value = [("https://www.facebook.com/missing.profile", {}, "cookie")]
        fetch_limited.return_value = _fetch_result(
            200,
            "This content isn't available right now",
            "https://www.facebook.com/missing.profile",
            "ok",
        )

        result = check_tick_input(CheckRequest(input="https://www.facebook.com/missing.profile", mode="1"))

        self.assertEqual(result["status"], "DIE")
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")
        self.assertEqual(result["reason"], "no_cookie_content_unavailable")
        self.assertEqual(fetch_limited.call_count, 1)
        self.assertEqual(load_accounts.call_count, 0)
        self.assertEqual(cookie_candidates.call_count, 0)

    @patch("app_modules.features.profile_name.load_cookie_accounts")
    @patch("app_modules.features.profile_name._fetch_limited_text")
    def test_checktick_force_cookie_skips_no_cookie(self, fetch_limited, load_accounts):
        load_accounts.return_value = [_cookie_account()]
        fetch_limited.return_value = _fetch_result(
            200,
            '<meta property="og:title" content="Cookie Only Verified account">',
            "https://www.facebook.com/cookie.only",
            "ok",
        )

        result = check_tick_input(
            CheckRequest(input="https://www.facebook.com/cookie.only", mode="1", includeName=True, forceCookie=True)
        )

        self.assertEqual(result["name"], "Cookie Only")
        self.assertTrue(result["verified"])
        self.assertFalse(result["usedCookie"])
        self.assertEqual(result["checkTickMode"], "no_cookie")
        self.assertEqual(load_accounts.call_count, 0)

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
    def test_profile_name_fetches_fresh_each_time(self, fetch_text, load_accounts):
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
        self.assertEqual(fetch_text.call_count, 2)


def _cookie_account(c_user="100000000000099"):
    return CookieAccount(
        c_user=c_user,
        source="test",
        index=0,
        cookies={"c_user": c_user, "xs": "fake-xs-token"},
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
