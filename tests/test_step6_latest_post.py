import unittest
from unittest.mock import patch

from app_modules.api.controller import LatestPostRequest, latest_post_input
from app_modules.features.latest_post import (
    FetchResult,
    build_facebook_latest_post_probe_urls,
    build_latest_post_link,
    clean_facebook_post_content,
    extract_latest_post_content_from_html,
    extract_profile_username_from_url,
    parse_latest_post_from_html,
)
from app_modules.resolvers.uid_resolver import ResolvedInput


class Step6LatestPostTests(unittest.TestCase):
    def test_probe_urls_prefer_light_no_cookie_urls(self):
        urls = build_facebook_latest_post_probe_urls("100000000000001", "test.user", with_cookie=False)

        self.assertEqual(urls[0], "https://mbasic.facebook.com/profile.php?id=100000000000001&v=timeline")
        self.assertIn("https://m.facebook.com/profile.php?id=100000000000001&v=timeline", urls)
        self.assertFalse(any("www.facebook.com/test.user" in item for item in urls))

    def test_cookie_urls_include_username_and_desktop_posts(self):
        urls = build_facebook_latest_post_probe_urls("100000000000001", "test.user", with_cookie=True)

        self.assertIn("https://www.facebook.com/test.user?sk=posts", urls)
        self.assertIn("https://mbasic.facebook.com/profile.php?id=100000000000001&v=timeline", urls)
        self.assertIn("https://www.facebook.com/profile.php?id=100000000000001&sk=posts", urls)

    def test_extracts_profile_username_from_login_next_redirect(self):
        username = extract_profile_username_from_url(
            "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Flamquoccuong.media%2F"
        )

        self.assertEqual(username, "lamquoccuong.media")

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
        resolve_input.return_value = _resolved(uid=uid, username="test.user")
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
        self.assertEqual(payload["postId"], "123456789012345")
        self.assertEqual(payload["content"], "Latest post content")
        self.assertEqual(payload["method"], "no_cookie")
        self.assertIn("elapsedMs", payload)


def _resolved(uid="", username=""):
    return ResolvedInput(
        input=username or uid,
        uid=uid,
        username=username,
        canonical_url=f"https://www.facebook.com/profile.php?id={uid}" if uid else "",
        source="test",
        reason="test",
    )


if __name__ == "__main__":
    unittest.main()
