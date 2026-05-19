import json
import os
import unittest
from unittest.mock import patch

from app_modules.api.controller import CheckRequest, check_input
from app_modules.resolvers.facebook_uid_resolver import (
    FetchResult,
    build_facebook_probe_urls,
    extract_uid_candidates_from_html,
    extract_uid_from_meta_html,
    extract_uid_for_username_from_html,
    extract_username_from_url,
    extract_uid_from_html,
    extract_uid_from_url,
    build_uid_probe_header_candidates,
    resolve_uid_from_any_input,
)
from app_modules.resolvers.facebook_uid_cookie_resolver import CookieUidResolution
from app_modules.checkers.check_modes import ModeConfig
from app_modules.checkers.probe_result import ProbeResult


class Step42UidResolverTests(unittest.TestCase):
    def test_extract_uid_shortcuts_from_url(self):
        self.assertEqual(
            extract_uid_from_url("https://www.facebook.com/profile.php?id=100041775009544"),
            "100041775009544",
        )
        self.assertEqual(
            extract_uid_from_url("https://www.facebook.com/people/Test-Name/100041775009544/"),
            "100041775009544",
        )
        self.assertEqual(
            extract_uid_from_url("http://facebook.com/61574756686411"),
            "61574756686411",
        )
        self.assertEqual(
            extract_uid_from_url("http://facebook.com/9209278"),
            "9209278",
        )
        self.assertEqual(extract_uid_from_url("https://example.com/profile.php?id=100041775009544"), "")

    def test_probe_urls_include_desktop_and_mobile(self):
        urls = build_facebook_probe_urls("https://www.facebook.com/kieu.anh.511762")
        self.assertIn("https://www.facebook.com/kieu.anh.511762", urls)
        self.assertIn("https://m.facebook.com/kieu.anh.511762", urls)

    def test_probe_urls_prioritize_mbasic_then_mobile_then_desktop(self):
        urls = build_facebook_probe_urls("https://www.facebook.com/kieu.anh.511762")

        self.assertEqual(
            urls[:3],
            [
                "https://mbasic.facebook.com/kieu.anh.511762",
                "https://m.facebook.com/kieu.anh.511762",
                "https://www.facebook.com/kieu.anh.511762",
            ],
        )

    def test_share_path_is_not_a_username(self):
        self.assertEqual(extract_username_from_url("https://www.facebook.com/share/1Ay9R878jq/"), "")

    def test_extract_uid_from_html_patterns(self):
        samples = [
            '<meta property="al:ios:url" content="fb://profile/9209278">',
            '<meta property="al:ios:url" content="fb://profile/100000000000008">',
            '<meta property="og:url" content="https://www.facebook.com/profile.php?id=100000000000009">',
            '"profile_owner":"100000000000010"',
            '"owner":{"id":"100000000000011"}',
            '"userID":"100000000000001"',
            '"profile_id":100000000000002',
            '"entity_id":"100000000000003"',
            '"actorID":"100000000000004"',
            '"subject_id":"100000000000005"',
            "profile.php?id=100000000000006",
            "fb://profile/100000000000007",
        ]
        for index, sample in enumerate(samples, start=1):
            with self.subTest(index=index):
                self.assertRegex(extract_uid_from_html(sample), r"^\d{5,20}$")

    def test_extract_uid_candidates_keeps_later_target_uid(self):
        html = '{"userID":"100000000000001"} profile.php?id=100000000000077'

        self.assertEqual(
            extract_uid_candidates_from_html(html),
            ["100000000000001", "100000000000077"],
        )

    def test_extract_uid_from_encoded_html(self):
        html = "https:%5C%2F%5C%2Fwww.facebook.com%5C%2Fprofile.php%3Fid%3D100000000000088"

        self.assertEqual(extract_uid_from_html(html), "100000000000088")

    def test_extract_uid_from_meta_html_ignores_generic_body_uid(self):
        html = (
            '<meta property="al:ios:url" content="fb://profile/100000000000088">'
            'profile.php?id=100000000000077'
        )

        self.assertEqual(extract_uid_from_meta_html(html), "100000000000088")

    def test_extract_uid_from_meta_html_accepts_old_short_uid(self):
        html = '<meta property="al:android:url" content="fb://profile/9209278">'

        self.assertEqual(extract_uid_from_meta_html(html), "9209278")

    def test_extract_uid_for_username_uses_vanity_bound_user_id(self):
        html = (
            '"viewerID":"100084259813312",'
            '"userVanity":"thanhcuongmedia",'
            '"userID":"100002614628083",'
            '"profile_owner":{"id":"100002614628083","name":"Thanh Cuong"}'
        )

        self.assertEqual(
            extract_uid_for_username_from_html(html, "thanhcuongmedia"),
            "100002614628083",
        )

    def test_extract_uid_for_username_ignores_unrelated_profile_links(self):
        html = (
            'profile.php?id=100000638877549 '
            '"userVanity":"thanhcuongmedia","userID":"100002614628083"'
        )

        self.assertEqual(
            extract_uid_for_username_from_html(html, "thanhcuongmedia"),
            "100002614628083",
        )

    def test_extract_uid_for_username_accepts_old_short_uid(self):
        html = '"userVanity":"zMinhHuyDev","userID":"9209278"'

        self.assertEqual(
            extract_uid_for_username_from_html(html, "zMinhHuyDev"),
            "9209278",
        )

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_known_uid_map_resolves_username_before_network(self, fetch_text):
        env = {"UID_RESOLVER_KNOWN_MAP_JSON": json.dumps({"kieu.anh.511762": "100013996607571"})}

        with patch.dict(os.environ, env, clear=False):
            result = resolve_uid_from_any_input("https://www.facebook.com/kieu.anh.511762")

        self.assertEqual(result.uid, "100013996607571")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_hong_duyen_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/hong.duyen.tran.594446")

        self.assertEqual(result.uid, "100004192098772")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_tankiet_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/tankiet.pham.1276/")

        self.assertEqual(result.uid, "100042281496124")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_love_over_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/love.over.219161")

        self.assertEqual(result.uid, "61560438496711")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_bien_trang_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/bien.trang.750/")

        self.assertEqual(result.uid, "100004507923562")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_thanhcuongmedia_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/thanhcuongmedia")

        self.assertEqual(result.uid, "100002614628083")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_zminhhuydev_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/zMinhHuyDev/")

        self.assertEqual(result.uid, "9209278")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_public_resolver_checks_final_url_before_html_body(self, fetch_text):
        fetch_text.return_value = FetchResult(
            200,
            '{"userID":"100000000000077"}',
            "https://www.facebook.com/profile.php?id=100000000000088",
            "ok",
        )

        result = resolve_uid_from_any_input("https://www.facebook.com/share/example/")

        self.assertEqual(result.uid, "100000000000088")
        self.assertEqual(result.source, "uid_final_url")
        self.assertEqual(result.reason, "uid_found_in_final_url")

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_public_resolver_checks_meta_before_generic_body(self, fetch_text):
        fetch_text.return_value = FetchResult(
            200,
            (
                '<meta property="al:android:url" content="fb://profile/100000000000088">'
                'profile.php?id=100000000000077'
            ),
            "https://www.facebook.com/share/example/",
            "ok",
        )

        result = resolve_uid_from_any_input("https://www.facebook.com/share/example/")

        self.assertEqual(result.uid, "100000000000088")
        self.assertEqual(result.source, "uid_html_probe")
        self.assertEqual(result.reason, "uid_found_in_meta_html")

    def test_default_user_agent_file_is_loaded_before_fallbacks(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = build_uid_probe_header_candidates()

        self.assertGreaterEqual(len(headers), 1)
        self.assertIn("Android 13", headers[0]["User-Agent"])

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_four_required_link_shapes_resolve_before_checking(self, fetch_text):
        numeric = resolve_uid_from_any_input("http://facebook.com/61574756686411")
        self.assertEqual(numeric.uid, "61574756686411")
        self.assertEqual(fetch_text.call_count, 0)

        fetch_text.side_effect = _uid_fetcher

        first_username = resolve_uid_from_any_input("https://www.facebook.com/kieu.anh.511762")
        self.assertEqual(first_username.uid, "100000000000001")
        self.assertEqual(first_username.source, "uid_html_probe")

        share = resolve_uid_from_any_input("https://www.facebook.com/share/1Ay9R878jq/")
        self.assertEqual(share.uid, "100000000000002")
        self.assertEqual(share.source, "uid_final_url")

        second_username = resolve_uid_from_any_input("https://www.facebook.com/kieu.anh.51176299")
        self.assertEqual(second_username.uid, "100000000000099")
        self.assertEqual(second_username.source, "uid_html_probe")

    @patch("app_modules.resolvers.facebook_uid_resolver._resolve_uid_with_cookie_fallback")
    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_public_resolver_rejects_username_candidate_when_slug_verify_hits_login(self, fetch_text, cookie_fallback):
        cookie_fallback.return_value = CookieUidResolution("", "uid_cookie_resolver", "no_usable_cookie_accounts", [])

        def fake_fetch(url, headers, timeout):
            if "profile.php?id=61560438496711" in url:
                return FetchResult(
                    200,
                    "<title>Log in to Facebook</title>",
                    "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fprofile.php%3Fid%3D61560438496711",
                    "ok",
                )
            return FetchResult(
                200,
                'profile.php?id=61560438496711',
                "https://www.facebook.com/love.over.21916177",
                "ok",
            )

        fetch_text.side_effect = fake_fetch

        result = resolve_uid_from_any_input("https://www.facebook.com/love.over.21916177")

        self.assertEqual(result.uid, "")
        self.assertTrue(
            any(probe.get("reason") == "uid_candidate_rejected_by_slug_verification" for probe in result.probes)
        )

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_controller_resolves_username_uid_before_mode_probe(self, fetch_text):
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

        fetch_text.return_value = FetchResult(
            200,
            '{"userID":"100000000000001"}',
            "https://www.facebook.com/kieu.anh.511762",
            "ok",
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
                CheckRequest(
                    input="https://www.facebook.com/kieu.anh.511762",
                    mode="1",
                    includeName=False,
                )
            )

        self.assertEqual(payload["uid"], "100000000000001")
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(calls, ["100000000000001"])

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_controller_keeps_mode1_silhouette_live_when_uid_is_resolved(self, fetch_text):
        def fake_fetch(url, headers, timeout):
            if "profile.php?id=61560438496711" in url:
                return FetchResult(
                    200,
                    '<a href="https://www.facebook.com/love.over.219161">profile</a>',
                    "https://www.facebook.com/love.over.219161",
                    "ok",
                )
            return FetchResult(
                200,
                'profile.php?id=61560438496711',
                "https://www.facebook.com/love.over.219161",
                "ok",
            )

        def fake_probe(uid):
            return ProbeResult(
                status="LIVE",
                confidence="strong",
                source="mode1_graph_public",
                reason="graph_profile_picture_dimensions",
                http_code=200,
                details={"height": 100, "width": 100, "isSilhouette": True},
            )

        fetch_text.side_effect = fake_fetch
        mode_config = ModeConfig(
            mode="1",
            source="mode1_graph_public",
            description="test mode 1",
            implemented=True,
            handler=fake_probe,
        )

        with patch.dict("app_modules.checkers.check_modes.MODE_CONFIGS", {"1": mode_config}):
            payload = check_input(
                CheckRequest(
                    input="https://www.facebook.com/love.over.219161",
                    mode="1",
                    includeName=False,
                )
            )

        self.assertEqual(payload["uid"], "61560438496711")
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["confidence"], "strong")
        self.assertEqual(payload["reason"], "graph_profile_picture_dimensions")


def _uid_fetcher(url, headers, timeout):
    if "profile.php?id=100000000000001" in url:
        return FetchResult(
            200,
            '<a href="https://www.facebook.com/kieu.anh.511762">profile</a>',
            "https://www.facebook.com/kieu.anh.511762",
            "ok",
        )
    if "profile.php?id=100000000000099" in url:
        return FetchResult(
            200,
            '<a href="https://www.facebook.com/kieu.anh.51176299">profile</a>',
            "https://www.facebook.com/kieu.anh.51176299",
            "ok",
        )
    if "share/1Ay9R878jq" in url:
        return FetchResult(
            200,
            "<html></html>",
            "https://www.facebook.com/profile.php?id=100000000000002",
            "ok",
        )
    if "kieu.anh.51176299" in url:
        return FetchResult(
            200,
            '"profile_id":100000000000099',
            url,
            "ok",
        )
    if "kieu.anh.511762" in url:
        return FetchResult(
            200,
            '{"userID":"100000000000001"}',
            url,
            "ok",
        )
    return FetchResult(200, "<html></html>", url, "ok")


if __name__ == "__main__":
    unittest.main()
