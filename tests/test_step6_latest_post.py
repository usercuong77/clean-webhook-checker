import unittest
from unittest.mock import patch

from app_modules.api.controller import LatestPostRequest, checkpost_direct_input, latest_post_input
from app_modules.features.latest_post import (
    DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE,
    FetchResult,
    build_cookie_candidates,
    build_direct_latest_post_probe_urls,
    build_facebook_latest_post_probe_urls,
    build_latest_post_link,
    clean_facebook_post_content,
    extract_latest_post_content_from_html,
    extract_profile_username_from_url,
    get_latest_post_direct_from_input,
    is_trusted_no_cookie_latest_post,
    parse_latest_post_from_html,
)
from app_modules.resolvers.uid_resolver import ResolvedInput


class Step6LatestPostTests(unittest.TestCase):
    def setUp(self):
        DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE.clear()

    def test_probe_urls_prefer_fast_public_desktop_urls(self):
        urls = build_facebook_latest_post_probe_urls("100000000000001", "test.user", with_cookie=False)

        self.assertEqual(urls[0], "https://www.facebook.com/test.user?sk=posts")
        self.assertIn("https://www.facebook.com/test.user", urls)
        self.assertIn("https://www.facebook.com/profile.php?id=100000000000001", urls)
        self.assertFalse(any("mbasic.facebook.com" in item or "m.facebook.com" in item for item in urls))

    def test_cookie_urls_include_username_and_desktop_posts(self):
        urls = build_facebook_latest_post_probe_urls("100000000000001", "test.user", with_cookie=True)

        self.assertEqual(urls[0], "https://www.facebook.com/test.user?sk=posts")
        self.assertIn("https://www.facebook.com/test.user", urls)
        self.assertIn("https://www.facebook.com/profile.php?id=100000000000001", urls)
        self.assertFalse(any("mbasic.facebook.com" in item or "m.facebook.com" in item for item in urls))

    def test_cookie_candidates_try_no_cookie_last(self):
        candidates = build_cookie_candidates(request_cookies={"c_user": "100000000000001", "xs": "token"})

        self.assertTrue(candidates[0].has_cookie)
        self.assertEqual(candidates[-1].source, "no_cookie")

    def test_no_cookie_rejects_field_exception_without_timestamp(self):
        self.assertFalse(
            is_trusted_no_cookie_latest_post(
                {"postId": "123456789012345", "timestamp": 0},
                "A server error field_exception occured. Check server logs for details.",
            )
        )
        self.assertTrue(is_trusted_no_cookie_latest_post({"postId": "123456789012345", "timestamp": 1760000000}, ""))

    def test_extracts_profile_username_from_login_next_redirect(self):
        username = extract_profile_username_from_url(
            "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Flamquoccuong.media%2F"
        )

        self.assertEqual(username, "lamquoccuong.media")

    def test_build_direct_latest_post_urls_strip_comment_query(self):
        urls = build_direct_latest_post_probe_urls(
            "https://www.facebook.com/phuc121296?comment_id=abc"
        )

        self.assertEqual(urls[0], "https://www.facebook.com/phuc121296?sk=posts")
        self.assertIn("https://www.facebook.com/phuc121296", urls)

    def test_parse_latest_post_pair(self):
        html = '"post_id":"123456789012345" abc "publish_time":1760000000'

        parsed = parse_latest_post_from_html(html)

        self.assertEqual(parsed["postId"], "123456789012345")
        self.assertEqual(parsed["timestamp"], 1760000000)

    def test_extract_content_near_post_id(self):
        html = (
            'noise "post_id":"123456789012345" '
            '"message":{"text":"Hello\\nworld from latest post"}'
        )

        content = extract_latest_post_content_from_html(html, "123456789012345")

        self.assertEqual(content, "Hello\nworld from latest post")

    def test_build_latest_post_link_supports_numeric_and_pfbid(self):
        self.assertEqual(
            build_latest_post_link("100000000000001", "123456789012345"),
            "https://www.facebook.com/100000000000001/posts/123456789012345",
        )
        self.assertIn("permalink.php?story_fbid=pfbidABC12345", build_latest_post_link("100000000000001", "pfbidABC12345"))

    def test_clean_post_content_rejects_login_wall_text(self):
        self.assertEqual(clean_facebook_post_content("Log in or sign up to view"), "")

    @patch("app_modules.features.latest_post.load_cookie_accounts", return_value=[])
    @patch("app_modules.features.latest_post._fetch_text")
    @patch("app_modules.api.controller.resolve_input")
    def test_latest_post_response_shape(self, resolve_input, fetch_text, load_cookie_accounts):
        uid = "100000000000001"
        resolve_input.return_value = _resolved(uid=uid, username="test.user", resolver_name="TDS Name")
        fetch_text.return_value = FetchResult(
            200,
            (
                '"post_id":"123456789012345"'
                '"publish_time":1760000000'
                '"message":{"text":"Latest post content"}'
            ),
            "https://mbasic.facebook.com/profile.php?id=100000000000001&v=timeline",
            "ok",
        )

        payload = latest_post_input(LatestPostRequest(input="https://www.facebook.com/test.user"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["uid"], uid)
        self.assertEqual(payload["name"], "TDS Name")
        self.assertEqual(payload["postId"], "123456789012345")
        self.assertEqual(payload["content"], "Latest post content")
        self.assertEqual(payload["method"], "no_cookie")
        self.assertIn("elapsedMs", payload)

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_uses_no_cookie_before_cookie_without_resolver(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [_cookie_account()]
        fetch_text.return_value = FetchResult(
            200,
            (
                '"post_id":"123456789012345"'
                '"publish_time":1760000000'
                '"message":{"text":"Direct latest post content"}'
            ),
            "https://www.facebook.com/test.user?sk=posts",
            "ok",
        )

        payload = checkpost_direct_input(LatestPostRequest(input="https://www.facebook.com/test.user?comment_id=abc"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["uid"], "")
        self.assertEqual(payload["username"], "test.user")
        self.assertEqual(payload["postId"], "123456789012345")
        self.assertEqual(payload["content"], "Direct latest post content")
        self.assertEqual(payload["method"], "direct_no_cookie")
        self.assertEqual(payload["source"], "direct_link_scrape")
        self.assertEqual(fetch_text.call_count, 1)
        first_call_url = fetch_text.call_args.args[0]
        self.assertEqual(first_call_url, "https://www.facebook.com/test.user?sk=posts")

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_falls_back_to_cookie(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [_cookie_account()]
        fetch_text.side_effect = [
            FetchResult(200, "Log in or sign up to view", "https://www.facebook.com/test.user?sk=posts", "ok"),
            FetchResult(
                200,
                (
                    '"post_id":"123456789012345"'
                    '"publish_time":1760000000'
                    '"message":{"text":"Cookie latest post content"}'
                ),
                "https://www.facebook.com/test.user?sk=posts",
                "ok",
            ),
        ]

        payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["method"], "direct_with_cookie")
        self.assertEqual(payload["content"], "Cookie latest post content")
        self.assertEqual(fetch_text.call_count, 2)
        self.assertTrue(payload["probeAttempts"][0]["fastFallbackToCookie"])

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_skips_no_cookie_when_requires_cookie_cached(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [_cookie_account()]
        fetch_text.side_effect = [
            FetchResult(200, "Log in or sign up to view", "https://www.facebook.com/test.user?sk=posts", "ok"),
            FetchResult(
                200,
                (
                    '"post_id":"123456789012345"'
                    '"publish_time":1760000000'
                    '"message":{"text":"Cookie latest post content"}'
                ),
                "https://www.facebook.com/test.user?sk=posts",
                "ok",
            ),
            FetchResult(
                200,
                (
                    '"post_id":"223456789012345"'
                    '"publish_time":1760000001'
                    '"message":{"text":"Cached cookie latest post content"}'
                ),
                "https://www.facebook.com/test.user?sk=posts",
                "ok",
            ),
        ]

        first_payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")
        second_payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")

        self.assertTrue(first_payload["ok"])
        self.assertTrue(second_payload["ok"])
        self.assertEqual(second_payload["method"], "direct_with_cookie")
        self.assertEqual(second_payload["content"], "Cached cookie latest post content")
        self.assertEqual(fetch_text.call_count, 3)
        third_call_headers = fetch_text.call_args_list[2].args[1]
        self.assertIn("Cookie", third_call_headers)


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


def _cookie_account():
    from app_modules.resolvers.facebook_cookies import CookieAccount

    return CookieAccount(
        c_user="100000000000099",
        source="test_cookie_file",
        index=0,
        cookies={"c_user": "100000000000099", "xs": "fake-xs-token"},
    )


if __name__ == "__main__":
    unittest.main()
