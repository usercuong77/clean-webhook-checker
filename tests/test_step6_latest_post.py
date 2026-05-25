import unittest
from unittest.mock import patch

from app_modules.api.controller import LatestPostRequest, checkpost_direct_input, latest_post_input
from app_modules.features.latest_post import (
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
    _fetch_text,
    _cookie_account_limit,
    _max_probe_attempts,
)
from app_modules.resolvers.uid_resolver import ResolvedInput


class Step6LatestPostTests(unittest.TestCase):
    def setUp(self):
        pass

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

    @patch.dict("os.environ", {}, clear=True)
    def test_latest_post_probe_defaults_are_capped_for_realtime(self):
        self.assertEqual(_max_probe_attempts(), 6)
        self.assertEqual(_cookie_account_limit(), 2)

    @patch.dict("os.environ", {"LATEST_POST_COOKIE_ACCOUNT_LIMIT": "4"})
    def test_latest_post_cookie_limit_hard_caps_legacy_env(self):
        self.assertEqual(_cookie_account_limit(), 2)

    def test_no_cookie_rejects_field_exception_without_timestamp(self):
        self.assertFalse(
            is_trusted_no_cookie_latest_post(
                {"postId": "123456789012345", "timestamp": 0},
                "A server error field_exception occured. Check server logs for details.",
            )
        )
        self.assertFalse(is_trusted_no_cookie_latest_post({"postId": "123456789012345", "timestamp": 1760000000}, ""))
        self.assertTrue(
            is_trusted_no_cookie_latest_post(
                {"postId": "123456789012345", "timestamp": 1760000000},
                "Direct latest post content",
            )
        )

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

    def test_build_direct_latest_post_urls_prefer_profile_uid_base_before_posts_tab(self):
        urls = build_direct_latest_post_probe_urls(
            "https://www.facebook.com/profile.php?id=100003717317472"
        )

        self.assertEqual(urls[0], "https://www.facebook.com/profile.php?id=100003717317472")
        self.assertEqual(urls[1], "https://www.facebook.com/profile.php?id=100003717317472&sk=posts")

    def test_parse_latest_post_pair(self):
        html = '"post_id":"123456789012345" abc "publish_time":1760000000'

        parsed = parse_latest_post_from_html(html)

        self.assertEqual(parsed["postId"], "123456789012345")
        self.assertEqual(parsed["timestamp"], 1760000000)

    def test_parse_latest_post_pair_prefers_newest_timestamp(self):
        html = (
            '"post_id":"111111111111111" abc "publish_time":1760000000'
            ' noise '
            '"post_id":"222222222222222" abc "publish_time":1760000099'
        )

        parsed = parse_latest_post_from_html(html)

        self.assertEqual(parsed["postId"], "222222222222222")
        self.assertEqual(parsed["timestamp"], 1760000099)

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

    def test_clean_post_content_repairs_facebook_mojibake_text(self):
        mojibake = "T\u00e1\u00ba\u00a1i c\u00c3\u00a1c x\u00c3\u00a3 mi\u00e1\u00bb\u0081n n\u00c3\u00bai"

        self.assertEqual(clean_facebook_post_content(mojibake), "T\u1ea1i c\u00e1c x\u00e3 mi\u1ec1n n\u00fai")
        self.assertEqual(clean_facebook_post_content("Ti\u00e1\u00bb\u0083u th\u00c6\u00b0"), "Ti\u1ec3u th\u01b0")

    def test_clean_post_content_rejects_profile_metadata(self):
        self.assertEqual(clean_facebook_post_content("News & media website"), "")
        self.assertEqual(clean_facebook_post_content("348K ng\u01b0\u1eddi theo d\u00f5i"), "")
        self.assertEqual(
            clean_facebook_post_content("K\u00eanh th\u00f4ng tin ch\u00ednh th\u1ee9c c\u1ee7a VTVcab tr\u00ean m\u1ea1ng x\u00e3 h\u1ed9i\nEmail: test@example.com"),
            "",
        )
        self.assertEqual(
            clean_facebook_post_content("\u0110\u1ed7 Ph\u00f9ng \u0111ang \u1edf tr\u00ean Facebook. Tham gia Facebook \u0111\u1ec3 k\u1ebft n\u1ed1i"),
            "",
        )
        self.assertEqual(clean_facebook_post_content("7 \u0111ang theo d\u00f5i"), "")
        self.assertEqual(clean_facebook_post_content("39 \u0111ang theo d\u00f5i"), "")
        self.assertEqual(clean_facebook_post_content("VTV24 thu\u1ed9c Ban Th\u1eddi s\u1ef1, \u0110\u00e0i Truy\u1ec1n h\u00ecnh Vi\u1ec7t Nam"), "")
        self.assertEqual(
            clean_facebook_post_content(
                "VTV24 - V\u00ec T\u1ea7m V\u00f3c Vi\u1ec7t, H\u00e0 N\u1ed9i. 348.445 l\u01b0\u1ee3t th\u00edch \u00b7 4.868 ng\u01b0\u1eddi \u0111ang n\u00f3i v\u1ec1 \u0111i\u1ec1u n\u00e0y."
            ),
            "",
        )

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
    def test_checkpost_direct_returns_tagged_timeline_post(self, fetch_text, load_cookie_accounts):
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

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["postId"], "965988279745648")
        self.assertEqual(payload["content"], "Tagged actor content")
        self.assertEqual(payload["reason"], "ok")
        self.assertEqual(payload["ownerUid"], "100005122057274")
        self.assertEqual(payload["actorUid"], "100025834400095")

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_returns_first_cookie_timeline_post(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [
            _cookie_account("100000000000077"),
            _cookie_account("100000000000088"),
            _cookie_account("100000000000099"),
        ]
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

        payload = get_latest_post_direct_from_input(
            "https://www.facebook.com/heoximang.kisutl",
            owner_uid="100005122057274",
            prefer_cookie=True,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(fetch_text.call_count, 1)
        self.assertEqual(payload["reason"], "ok")
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

    @patch("app_modules.api.controller.get_latest_post_direct_from_input")
    def test_checkpost_controller_does_not_resolve_owner_for_timeline_post(self, direct_latest_post):
        direct_latest_post.return_value = {
            "ok": True,
            "reason": "ok",
            "uid": "",
            "username": "heoximang.kisutl",
            "postId": "965988279745648",
            "content": "Tagged actor content",
            "ownerUid": "100005122057274",
            "actorUid": "100025834400095",
            "actorName": "Vĩnh Văn",
            "probeAttempts": [],
        }

        payload = checkpost_direct_input(LatestPostRequest(input="https://www.facebook.com/heoximang.kisutl"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["postId"], "965988279745648")
        self.assertEqual(payload["actorUid"], "100025834400095")
        self.assertEqual(payload["username"], "heoximang.kisutl")
        self.assertEqual(direct_latest_post.call_count, 1)
        self.assertNotIn("owner_uid", direct_latest_post.call_args.kwargs)

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
    def test_checkpost_profile_php_retries_username_after_cookie_redirect(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [_cookie_account()]
        fetch_text.side_effect = [
            FetchResult(
                200,
                "Log in or sign up to view",
                "https://www.facebook.com/profile.php?id=100037073983819",
                "ok",
            ),
            FetchResult(
                200,
                "checkpoint",
                "https://www.facebook.com/thanh.duyen.37570/",
                "ok",
            ),
            FetchResult(
                200,
                "checkpoint",
                "https://www.facebook.com/thanh.duyen.37570/",
                "ok",
            ),
            FetchResult(
                200,
                (
                    '"post_id":"123456789012345"'
                    '"publish_time":1760000000'
                    '"message":{"text":"Profile id redirected latest post"}'
                ),
                "https://www.facebook.com/thanh.duyen.37570?sk=posts",
                "ok",
            ),
        ]

        payload = get_latest_post_direct_from_input("https://www.facebook.com/profile.php?id=100037073983819")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["uid"], "100037073983819")
        self.assertEqual(payload["username"], "thanh.duyen.37570")
        self.assertEqual(payload["method"], "direct_with_cookie")
        self.assertEqual(payload["content"], "Profile id redirected latest post")
        self.assertEqual(fetch_text.call_count, 4)
        called_urls = [call.args[0] for call in fetch_text.call_args_list]
        self.assertIn("https://www.facebook.com/thanh.duyen.37570?sk=posts", called_urls)

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_share_redirect_retries_profile_uid_with_cookie(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [_cookie_account()]
        fetch_text.side_effect = [
            FetchResult(
                200,
                "checkpoint",
                "https://www.facebook.com/profile.php?id=100003717317472&rdid=abc&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2F1Ktp9U1VMF%2F",
                "ok",
            ),
            FetchResult(
                200,
                (
                    '"post_id":"3651761724957702"'
                    '"publish_time":1760000000'
                    '"message":{"text":"Redirected share profile latest post"}'
                ),
                "https://www.facebook.com/profile.php?id=100003717317472",
                "ok",
            ),
        ]

        payload = get_latest_post_direct_from_input("https://www.facebook.com/share/1Ktp9U1VMF/")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["uid"], "100003717317472")
        self.assertEqual(payload["content"], "Redirected share profile latest post")
        called_urls = [call.args[0] for call in fetch_text.call_args_list]
        self.assertEqual(called_urls[1], "https://www.facebook.com/profile.php?id=100003717317472")

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_retries_no_cookie_without_requires_cookie_cache(self, fetch_text, load_cookie_accounts):
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
            FetchResult(200, "Log in or sign up to view", "https://www.facebook.com/test.user?sk=posts", "ok"),
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
        self.assertEqual(fetch_text.call_count, 4)
        third_call_headers = fetch_text.call_args_list[2].args[1]
        fourth_call_headers = fetch_text.call_args_list[3].args[1]
        self.assertNotIn("Cookie", third_call_headers)
        self.assertIn("Cookie", fourth_call_headers)

    @patch("app_modules.features.latest_post.load_cookie_accounts")
    @patch("app_modules.features.latest_post._fetch_text")
    def test_checkpost_direct_keeps_cookie_order_without_working_cookie_cache(self, fetch_text, load_cookie_accounts):
        load_cookie_accounts.return_value = [
            _cookie_account("100000000000077"),
            _cookie_account("100000000000088"),
        ]
        fetch_text.side_effect = [
            FetchResult(200, "Log in or sign up to view", "https://www.facebook.com/test.user?sk=posts", "ok"),
            FetchResult(200, "checkpoint", "https://www.facebook.com/test.user?sk=posts", "ok"),
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
            FetchResult(200, "Log in or sign up to view", "https://www.facebook.com/test.user?sk=posts", "ok"),
            FetchResult(
                200,
                (
                    '"post_id":"223456789012345"'
                    '"publish_time":1760000001'
                    '"message":{"text":"First cookie latest post content"}'
                ),
                "https://www.facebook.com/test.user?sk=posts",
                "ok",
            ),
        ]

        first_payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")
        second_payload = get_latest_post_direct_from_input("https://www.facebook.com/test.user")

        self.assertTrue(first_payload["ok"])
        self.assertTrue(second_payload["ok"])
        self.assertEqual(second_payload["content"], "First cookie latest post content")
        self.assertEqual(fetch_text.call_count, 5)
        fifth_call_headers = fetch_text.call_args_list[4].args[1]
        self.assertIn("c_user=100000000000077", fifth_call_headers["Cookie"])

    @patch.dict(
        "os.environ",
        {
            "LATEST_POST_STREAM_CHECK_INTERVAL_BYTES": "65536",
            "LATEST_POST_STREAM_STOP_AFTER_POST_BYTES": "131072",
            "LATEST_POST_MAX_RESPONSE_BYTES": "300000",
        },
    )
    @patch("app_modules.features.latest_post.requests.get")
    def test_fetch_text_streams_and_stops_after_post_payload(self, get):
        post_id = "123456789012345"
        get.return_value = _stream_response(
            [
                b"x" * 70000,
                (
                    f'"post_id":"{post_id}"'
                    '"publish_time":1760000000'
                    '"message":{"text":"Streamed post content"}'
                ).encode("utf-8") + b"y" * 70000,
                b"tail-that-should-not-be-read",
            ]
        )

        fetch = _fetch_text("https://www.facebook.com/test.user?sk=posts", {"User-Agent": "test"}, 7)

        self.assertEqual(fetch.http_code, 200)
        self.assertIn("Streamed post content", fetch.text)
        self.assertNotIn("tail-that-should-not-be-read", fetch.text)

    @patch.dict(
        "os.environ",
        {
            "LATEST_POST_STREAM_CHECK_INTERVAL_BYTES": "65536",
            "LATEST_POST_STREAM_FETCH_DEADLINE_SEC": "4",
            "LATEST_POST_MAX_RESPONSE_BYTES": "300000",
        },
    )
    @patch("app_modules.features.latest_post.perf_counter", side_effect=[0.0, 5.0])
    @patch("app_modules.features.latest_post.requests.get")
    def test_fetch_text_stops_at_total_stream_deadline(self, get, perf_counter):
        get.return_value = _stream_response(
            [
                b"x" * 70000,
                b"tail-that-should-not-be-read",
            ]
        )

        fetch = _fetch_text("https://www.facebook.com/test.user?sk=posts", {"User-Agent": "test"}, 7)

        self.assertEqual(fetch.http_code, 200)
        self.assertNotIn("tail-that-should-not-be-read", fetch.text)
        self.assertGreaterEqual(perf_counter.call_count, 2)


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


class _FakeStreamResponse:
    status_code = 200
    url = "https://www.facebook.com/test.user?sk=posts"
    encoding = "utf-8"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size=65536):
        yield from self._chunks


def _stream_response(chunks):
    return _FakeStreamResponse(chunks)


if __name__ == "__main__":
    unittest.main()
