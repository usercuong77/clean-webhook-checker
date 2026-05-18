import json
import os
import unittest
from unittest.mock import patch

from app_modules.api.controller import CheckRequest, check_input
from app_modules.resolvers.facebook_uid_resolver import (
    FetchResult,
    build_facebook_probe_urls,
    _clear_uid_resolution_cache_for_tests,
    extract_uid_candidates_from_html,
    extract_username_from_url,
    extract_uid_from_html,
    extract_uid_from_url,
    build_uid_probe_header_candidates,
    resolve_uid_from_any_input,
)
from app_modules.checkers.check_modes import ModeConfig
from app_modules.checkers.probe_result import ProbeResult


class Step42UidResolverTests(unittest.TestCase):
    def tearDown(self):
        _clear_uid_resolution_cache_for_tests()

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
        self.assertEqual(extract_uid_from_url("https://example.com/profile.php?id=100041775009544"), "")

    def test_probe_urls_include_desktop_and_mobile(self):
        urls = build_facebook_probe_urls("https://www.facebook.com/kieu.anh.511762")
        self.assertIn("https://www.facebook.com/kieu.anh.511762", urls)
        self.assertIn("https://m.facebook.com/kieu.anh.511762", urls)

    def test_share_path_is_not_a_username(self):
        self.assertEqual(extract_username_from_url("https://www.facebook.com/share/1Ay9R878jq/"), "")

    def test_extract_uid_from_html_patterns(self):
        samples = [
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
                self.assertRegex(extract_uid_from_html(sample), r"^\d{8,20}$")

    def test_extract_uid_candidates_keeps_later_target_uid(self):
        html = '{"userID":"100000000000001"} profile.php?id=100000000000077'

        self.assertEqual(
            extract_uid_candidates_from_html(html),
            ["100000000000001", "100000000000077"],
        )

    def test_extract_uid_from_encoded_html(self):
        html = "https:%5C%2F%5C%2Fwww.facebook.com%5C%2Fprofile.php%3Fid%3D100000000000088"

        self.assertEqual(extract_uid_from_html(html), "100000000000088")

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
    def test_builtin_confirmed_uid_map_resolves_ng_trinh_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/ng.trinh.498077")

        self.assertEqual(result.uid, "100080441816993")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_builtin_confirmed_uid_map_resolves_vo_duy_before_network(self, fetch_text):
        result = resolve_uid_from_any_input("https://www.facebook.com/vo.duy.0910")

        self.assertEqual(result.uid, "100010211341364")
        self.assertEqual(result.source, "uid_known_map")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._resolve_uid_with_cookie_fallback")
    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_username_cookie_preflight_runs_before_public_probe(self, fetch_text, cookie_fallback):
        cookie_fallback.return_value = type(
            "CookieResult",
            (),
            {
                "uid": "100000000000321",
                "source": "uid_cookie_probe",
                "reason": "uid_found_in_cookie_html",
                "probes": [{"source": "uid_cookie_probe", "reason": "uid_found_in_cookie_html"}],
            },
        )()

        result = resolve_uid_from_any_input("https://www.facebook.com/preflight.test.0910")

        self.assertEqual(result.uid, "100000000000321")
        self.assertEqual(result.source, "uid_cookie_probe")
        self.assertEqual(result.reason, "uid_found_in_cookie_preflight:uid_found_in_cookie_html")
        self.assertEqual(fetch_text.call_count, 0)
        self.assertEqual(cookie_fallback.call_count, 1)
        self.assertEqual(cookie_fallback.call_args.kwargs["max_accounts"], 1)
        self.assertEqual(cookie_fallback.call_args.kwargs["max_requests"], 3)

    @patch("app_modules.resolvers.facebook_uid_resolver._resolve_uid_with_cookie_fallback")
    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_successful_username_resolution_is_cached(self, fetch_text, cookie_fallback):
        cookie_fallback.return_value = type(
            "CookieResult",
            (),
            {
                "uid": "",
                "source": "uid_cookie_resolver",
                "reason": "not_called",
                "probes": [],
            },
        )()
        fetch_text.return_value = FetchResult(
            200,
            '{"userID":"100000000000123"}',
            "https://mbasic.facebook.com/cache.test.12345",
            "ok",
        )

        first = resolve_uid_from_any_input("https://www.facebook.com/cache.test.12345")
        fetch_text.reset_mock()
        cookie_fallback.reset_mock()
        second = resolve_uid_from_any_input("https://www.facebook.com/cache.test.12345")

        self.assertEqual(first.uid, "100000000000123")
        self.assertEqual(first.source, "uid_html_probe")
        self.assertEqual(second.uid, "100000000000123")
        self.assertEqual(second.source, "uid_memory_cache")
        self.assertEqual(fetch_text.call_count, 0)
        self.assertEqual(cookie_fallback.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._resolve_uid_with_cookie_fallback")
    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_unresolved_username_uses_public_probe_budget(self, fetch_text, cookie_fallback):
        fetch_text.return_value = FetchResult(
            200,
            "<html></html>",
            "https://mbasic.facebook.com/nguyen.trung.hieu.77803077",
            "ok",
        )
        cookie_fallback.return_value = type(
            "CookieResult",
            (),
            {
                "uid": "",
                "source": "uid_cookie_resolver",
                "reason": "uid_not_found_after_cookie_probe_budget",
                "probes": [],
            },
        )()

        env = {
            "UID_PUBLIC_PROBE_MAX_REQUESTS": "2",
            "UID_PUBLIC_PROBE_TIMEOUT_SEC": "1",
            "UID_PUBLIC_PROBE_DEADLINE_SEC": "5",
        }
        with patch.dict(os.environ, env, clear=False):
            result = resolve_uid_from_any_input("https://www.facebook.com/nguyen.trung.hieu.77803077")

        self.assertEqual(result.uid, "")
        self.assertEqual(result.username, "nguyen.trung.hieu.77803077")
        self.assertEqual(result.reason, "uid_not_found_after_public_and_cookie_probe")
        self.assertEqual(fetch_text.call_count, 2)

    def test_default_user_agent_file_is_loaded_before_fallbacks(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = build_uid_probe_header_candidates()

        self.assertGreaterEqual(len(headers), 1)
        self.assertIn("Windows NT 10.0", headers[0]["User-Agent"])

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


def _uid_fetcher(url, headers, timeout):
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
