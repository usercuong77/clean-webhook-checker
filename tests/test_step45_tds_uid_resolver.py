import unittest
from unittest.mock import Mock, patch

from app_modules.resolvers.facebook_uid_resolver import resolve_uid_from_any_input
from app_modules.resolvers.tds_uid_resolver import resolve_uid_with_tds_api


class Step45TdsUidResolverTests(unittest.TestCase):
    @patch("app_modules.resolvers.tds_uid_resolver.requests.post")
    def test_tds_uid_resolver_returns_uid(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "success": 200,
            "id": "9209278",
            "name": "Nguyen Minh Huy",
        }
        post.return_value = response

        result = resolve_uid_with_tds_api("https://www.facebook.com/zMinhHuyDev/")

        self.assertEqual(result.uid, "9209278")
        self.assertEqual(result.name, "Nguyen Minh Huy")
        self.assertEqual(result.reason, "uid_found_tds_api")

    @patch("app_modules.resolvers.tds_uid_resolver.requests.post")
    def test_tds_uid_resolver_reports_rate_limit(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"error": "Vui long thao tac cham lai"}
        post.return_value = response

        result = resolve_uid_with_tds_api("https://www.facebook.com/example")

        self.assertEqual(result.uid, "")
        self.assertEqual(result.reason, "tds_rate_limited")

    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    @patch("app_modules.resolvers.facebook_uid_resolver.resolve_uid_with_tds_api")
    def test_facebook_resolver_uses_tds_before_public_probe(self, tds_api, fetch_text):
        tds_api.return_value = Mock(
            uid="534838088",
            name="Luan Nguyen",
            source="tds_uid_api",
            reason="uid_found_tds_api",
            http_code=200,
        )

        result = resolve_uid_from_any_input("https://www.facebook.com/luanboy92/")

        self.assertEqual(result.uid, "534838088")
        self.assertEqual(result.source, "tds_uid_api")
        self.assertEqual(result.reason, "uid_found_tds_api")
        self.assertEqual(fetch_text.call_count, 0)

    @patch("app_modules.resolvers.facebook_uid_resolver._resolve_uid_with_cookie_fallback")
    @patch("app_modules.resolvers.facebook_uid_resolver._fetch_text")
    @patch("app_modules.resolvers.facebook_uid_resolver.resolve_uid_with_tds_api")
    def test_facebook_resolver_falls_back_when_tds_fails(self, tds_api, fetch_text, cookie_fallback):
        tds_api.return_value = Mock(
            uid="",
            name="",
            source="tds_uid_api",
            reason="tds_rate_limited",
            http_code=200,
        )
        fetch_text.return_value = Mock(
            http_code=200,
            text='"userVanity":"unknownuser","userID":"100000000000099"',
            final_url="https://www.facebook.com/unknownuser",
            reason="ok",
        )
        cookie_fallback.return_value = Mock(uid="", source="uid_cookie_resolver", reason="no_usable_cookie_accounts", probes=[])

        result = resolve_uid_from_any_input("https://www.facebook.com/unknownuser")

        self.assertEqual(result.uid, "100000000000099")
        self.assertEqual(result.source, "uid_html_probe")
        self.assertTrue(any(probe.get("source") == "tds_uid_api" for probe in result.probes))


if __name__ == "__main__":
    unittest.main()
