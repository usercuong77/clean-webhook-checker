import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app_modules.resolvers.facebook_uid_cookie_resolver import (
    CookieFetchResult,
    resolve_uid_with_cookies,
)
from app_modules.resolvers.facebook_uid_resolver import FetchResult, resolve_uid_from_any_input


FAKE_ENV = {
    "UID_CHECKER_FB_COOKIES_JSON": json.dumps(
        [{"c_user": "100000000000001", "xs": "fake-xs-token", "datr": "fake-datr"}]
    )
}


class Step45CookieUidResolverTests(unittest.TestCase):
    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    def test_cookie_resolver_extracts_uid_from_cookie_html(self, fetch_text):
        fetch_text.return_value = CookieFetchResult(
            200,
            '{"userID":"100000000000099"}',
            "https://www.facebook.com/kieu.anh.51176299",
            "ok",
        )

        result = resolve_uid_with_cookies("https://www.facebook.com/kieu.anh.51176299")

        self.assertEqual(result.uid, "100000000000099")
        self.assertEqual(result.source, "uid_cookie_probe")
        self.assertEqual(result.reason, "uid_found_in_cookie_html")
        self.assertEqual(result.probes[0]["cookieAccount"], "1000***0001")
        self.assertNotIn("fake-xs-token", json.dumps(result.probes))

    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    def test_cookie_resolver_skips_logged_in_account_uid(self, fetch_text):
        def fake_fetch(url, headers, timeout):
            if "profile.php?id=100000000000077" in url:
                return CookieFetchResult(
                    200,
                    '<a href="https://www.facebook.com/kieu.anh.511762">profile</a>',
                    "https://www.facebook.com/kieu.anh.511762",
                    "ok",
                )
            return CookieFetchResult(
                200,
                '{"userID":"100000000000001"} profile.php?id=100000000000077',
                "https://www.facebook.com/kieu.anh.511762",
                "ok",
            )

        fetch_text.side_effect = fake_fetch

        result = resolve_uid_with_cookies("https://www.facebook.com/kieu.anh.511762")

        self.assertEqual(result.uid, "100000000000077")
        self.assertEqual(result.reason, "uid_found_in_cookie_html")

    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    def test_cookie_resolver_rejects_username_candidate_when_profile_does_not_match_slug(self, fetch_text):
        def fake_fetch(url, headers, timeout):
            if "profile.php?id=100000000000077" in url:
                return CookieFetchResult(400, "<title>Error</title>", url, "http_400")
            return CookieFetchResult(
                200,
                '{"userID":"100000000000001"} profile.php?id=100000000000077',
                "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fkieu.anh.51176299%2F",
                "ok",
            )

        fetch_text.side_effect = fake_fetch

        result = resolve_uid_with_cookies("https://www.facebook.com/kieu.anh.51176299")

        self.assertEqual(result.uid, "")
        self.assertTrue(
            any(probe.get("reason") == "uid_candidate_rejected_by_slug_verification" for probe in result.probes)
        )

    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    def test_cookie_resolver_rejects_candidate_from_matching_slug_context_when_verify_hits_login(self, fetch_text):
        def fake_fetch(url, headers, timeout):
            if "profile.php?id=100000000000077" in url:
                return CookieFetchResult(
                    200,
                    "<title>Log in to Facebook</title>",
                    "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fprofile.php%3Fid%3D100000000000077",
                    "ok",
                )
            return CookieFetchResult(
                200,
                '{"userID":"100000000000001"} profile.php?id=100000000000077',
                "https://www.facebook.com/kieu.anh.511762",
                "ok",
            )

        fetch_text.side_effect = fake_fetch

        result = resolve_uid_with_cookies("https://www.facebook.com/kieu.anh.511762")

        self.assertEqual(result.uid, "")
        self.assertTrue(
            any(probe.get("reason") == "uid_candidate_rejected_by_slug_verification" for probe in result.probes)
        )

    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    def test_cookie_resolver_rejects_final_url_uid_when_slug_verify_hits_login(self, fetch_text):
        def fake_fetch(url, headers, timeout):
            if "profile.php?id=100000000000077" in url:
                return CookieFetchResult(
                    200,
                    "<title>Log in to Facebook</title>",
                    "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fprofile.php%3Fid%3D100000000000077",
                    "ok",
                )
            return CookieFetchResult(
                200,
                "<html></html>",
                "https://www.facebook.com/profile.php?id=100000000000077",
                "ok",
            )

        fetch_text.side_effect = fake_fetch

        result = resolve_uid_with_cookies("https://www.facebook.com/love.over.21916177")

        self.assertEqual(result.uid, "")
        self.assertTrue(
            any(probe.get("reason") == "uid_final_url_rejected_by_slug_verification" for probe in result.probes)
        )

    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    def test_cookie_resolver_extracts_uid_from_final_url(self, fetch_text):
        fetch_text.return_value = CookieFetchResult(
            200,
            "<html></html>",
            "https://www.facebook.com/profile.php?id=100000000000088",
            "ok",
        )

        result = resolve_uid_with_cookies("https://www.facebook.com/share/example/")

        self.assertEqual(result.uid, "100000000000088")
        self.assertEqual(result.reason, "uid_found_in_cookie_final_url")

    @patch("app_modules.resolvers.facebook_cookies.DEFAULT_LOCAL_COOKIE_FILE", Path("Z:/missing/facebook_cookies.txt"))
    @patch("app_modules.resolvers.facebook_cookies.os.environ", {})
    def test_cookie_resolver_reports_no_cookie_accounts(self):
        result = resolve_uid_with_cookies("https://www.facebook.com/kieu.anh.511762")

        self.assertEqual(result.uid, "")
        self.assertEqual(result.reason, "no_usable_cookie_accounts")

    @patch("app_modules.resolvers.facebook_cookies.os.environ", FAKE_ENV)
    @patch("app_modules.resolvers.facebook_uid_cookie_resolver._fetch_text_with_cookie")
    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    def test_public_resolver_falls_back_to_cookie_resolver(self, public_fetch, cookie_fetch):
        public_fetch.return_value = FetchResult(
            200,
            "<html></html>",
            "https://www.facebook.com/kieu.anh.511762",
            "ok",
        )
        cookie_fetch.return_value = CookieFetchResult(
            200,
            '{"profile_id":100000000000077}',
            "https://www.facebook.com/kieu.anh.511762",
            "ok",
        )

        result = resolve_uid_from_any_input("https://www.facebook.com/kieu.anh.511762")

        self.assertEqual(result.uid, "100000000000077")
        self.assertEqual(result.source, "uid_cookie_probe")
        self.assertEqual(result.reason, "uid_found_in_cookie_html")
        self.assertTrue(any(probe["source"] == "uid_cookie_probe" for probe in result.probes))


if __name__ == "__main__":
    unittest.main()
