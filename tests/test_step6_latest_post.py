import unittest
from unittest.mock import patch

from app_modules.api.controller import LatestPostRequest, checkpost_direct_input, latest_post_input
from app_modules.features.latest_post import (
    DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE,
    DIRECT_CHECKPOST_PREFERRED_COOKIE_FINGERPRINT,
    FetchResult,
    analyze_latest_post_ownership,
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
        import app_modules.features.latest_post as latest_post_module

        latest_post_module.DIRECT_CHECKPOST_PREFERRED_COOKIE_FINGERPRINT = ""

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

    def test_extract_content_prefers_message_over_tagged_context(self):
        post_id = "123456789012345"
        html = (
            f'"post_id":"{post_id}"'
            '"text":"Nh\\u1ecf Th\\u01b0\\u01a1ng c\\u00f9ng v\\u1edbi Vu Tran."'
            + ("x" * 10000)
            + f'"post_id":"{post_id}"'
            '"message":{"text":"N\\u0103m ngo\\u00e1i g\\u00f3p sh c\\u0169ng \\u0110\\u1ed3n tr\\u00fang s\\u1ed1\\nN\\u0103m nay g\\u00f3p \\u00f4t\\u00f4 c\\u0169ng \\u0111\\u1ed3n tr\\u00fang s\\u1ed1"}'
        )

        content = extract_latest_post_content_from_html(html, post_id)

        self.assertIn("Nam nay".replace("Nam", "N\u0103m"), content)
        self.assertNotIn("Vu Tran", content)

    def test_extract_content_prefers_message_over_live_with_others_context(self):
        post_id = "123456789012345"
        html = (
            f'"post_id":"{post_id}"'
            '"text":"V\\u0129nh V\\u0103n \\u0111\\u00e3 ph\\u00e1t tr\\u1ef1c ti\\u1ebfp \\u2014 v\\u1edbi Khuyn Khuyn v\\u00e0 7 ng\\u01b0\\u1eddi kh\\u00e1c."'
            + ("y" * 10000)
            + f'"post_id":"{post_id}"'
            '"message":{"text":"b\\u00e0 con c\\u1ea7n lh 0329556042 ship t\\u1eadn n\\u01a1i bao khoang d\\u1ef1ng cho b\\u00e0 con \\u1ea1"}'
        )

        content = extract_latest_post_content_from_html(html, post_id)

        self.assertIn("0329556042", content)
        self.assertNotIn("phat truc tiep", content.lower())

    def test_build_latest_post_link_supports_numeric_and_pfbid(self):
        self.assertEqual(
            build_latest_post_link("100000000000001", "123456789012345"),
            "https://www.facebook.com/100000000000001/posts/123456789012345",
        )
        self.assertIn("permalink.php?story_fbid=pfbidABC12345", build_latest_post_link("100000000000001", "pfbidABC12345"))

    def test_clean_post_content_rejects_login_wall_text(self):
        self.assertEqual(clean_facebook_post_content("Log in or sign up to view"), "")

    def test_analyze_latest_post_ownership_detects_tagged_actor(self):
        html = (
            '<meta property="al:android:url" content="fb://profile/100005122057274">'
            '"post_id":"965988279745648"'
            '"publish_time":1778113653'
            '"actors":[{"__typename":"User","name":"V\\u0129nh V\\u0103n","id":"100025834400095"}]'
        )

        ownership = analyze_latest_post_ownership(html, "965988279745648")

        self.assertEqual(ownership["ownerUid"], "100005122057274")
        self.assertEqual(ownership["actorUid"], "100025834400095")
        self.assertTrue(ownership["isTaggedOrSharedByOther"])

    @patch("app_modules.features.latest_post.load_cookie_accounts", return_value=[])
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_skips_tagged_actor_post(self, fetch_text, load_cookie_accounts):
        fetch_text.return_value = FetchResult(
            200,
            (
                '<meta property="al:android:url" content="fb://profile/100005122057274">'
                '"post_id":"965988279745648"'
                '"publish_time":1778113653'
                '"actors":[{"__typename":"User","name":"V\\u0129nh V\\u0103n","id":"100025834400095"}]'
                '"message":{"text":"Tagged actor content"}'
            ),
            "https://www.facebook.com/heoximang.kisutl?sk=posts",
            "ok",
        )

        payload = get_latest_post_direct_from_input("https://www.facebook.com/heoximang.kisutl")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "tagged_post_skipped_no_owner_post_found")
        self.assertTrue(payload["taggedPostSkipped"])
        self.assertTrue(payload["needsOwnerResolve"])
        self.assertEqual(payload["ownerUid"], "100005122057274")
        self.assertEqual(payload["actorUid"], "100025834400095")

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

    @patch("app_modules.api.controller.resolve_input")
    @patch("app_modules.api.controller.get_latest_post_direct_from_input")
    def test_checkpost_controller_resolves_owner_after_tagged_skip(self, direct_latest_post, resolve_input):
        resolve_input.return_value = _resolved(
            uid="100005122057274",
            username="heoximang.kisutl",
            resolver_name="Văn Trung",
        )
        direct_latest_post.side_effect = [
            {
                "ok": False,
                "reason": "tagged_post_skipped_no_owner_post_found",
                "taggedPostSkipped": True,
                "needsOwnerResolve": True,
                "ownerUid": "100005122057274",
                "actorUid": "100025834400095",
                "actorName": "Vĩnh Văn",
                "probeAttempts": [],
            },
            {
                "ok": False,
                "reason": "tagged_post_skipped_no_owner_post_found",
                "taggedPostSkipped": True,
                "probeAttempts": [],
            },
        ]

        payload = checkpost_direct_input(LatestPostRequest(input="https://www.facebook.com/heoximang.kisutl"))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["uid"], "100005122057274")
        self.assertEqual(payload["name"], "Văn Trung")
        self.assertEqual(payload["username"], "heoximang.kisutl")
        self.assertTrue(payload["skippedTaggedPost"])
        self.assertEqual(payload["ownerResolveSource"], "test")
        self.assertEqual(direct_latest_post.call_count, 2)
        self.assertEqual(direct_latest_post.call_args.kwargs["owner_uid"], "100005122057274")
        self.assertTrue(direct_latest_post.call_args.kwargs["prefer_cookie"])

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

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_prioritizes_last_working_cookie(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [
            _cookie_account("100000000000077"),
            _cookie_account("100000000000088"),
        ]
        fetch_text.side_effect = [
            FetchResult(200, "Log in or sign up to view", "https://www.facebook.com/test.user?sk=posts", "ok"),
            FetchResult(200, "checkpoint", "https://www.facebook.com/test.user?sk=posts", "ok"),
            FetchResult(200, "checkpoint", "https://www.facebook.com/test.user", "ok"),
            FetchResult(
                200,
                (
                    '"post_id":"123456789012345"'
                    '"publish_time":1760000000'
                    '"message":{"text":"Good cookie latest post content"}'
                ),
                "https://www.facebook.com/test.user?sk=posts",
                "ok",
            ),
            FetchResult(
                200,
                (
                    '"post_id":"223456789012345"'
                    '"publish_time":1760000001'
                    '"message":{"text":"Preferred cookie latest post content"}'
                ),
                "https://www.facebook.com/test.user?sk=posts",
                "ok",
            ),
        ]

        first_payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")
        second_payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")

        self.assertTrue(first_payload["ok"])
        self.assertTrue(second_payload["ok"])
        self.assertEqual(second_payload["content"], "Preferred cookie latest post content")
        fifth_call_headers = fetch_text.call_args_list[4].args[1]
        self.assertIn("c_user=100000000000088", fifth_call_headers["Cookie"])


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


def _cookie_account(c_user="100000000000099"):
    from app_modules.resolvers.facebook_cookies import CookieAccount

    return CookieAccount(
        c_user=c_user,
        source="test_cookie_file",
        index=0,
        cookies={"c_user": c_user, "xs": f"fake-xs-token-{c_user}"},
    )


if __name__ == "__main__":
    unittest.main()
