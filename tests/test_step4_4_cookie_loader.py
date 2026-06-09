import json
import tempfile
import unittest
from pathlib import Path

from app_modules.resolvers.facebook_cookies import (
    CookieAccount,
    DEFAULT_LOCAL_COOKIE_FILE,
    cookie_header,
    load_cookie_accounts,
    masked_accounts,
)
from app_modules.resolvers import fb_uid_lite_latest


class Step44CookieLoaderTests(unittest.TestCase):
    def test_loads_cookie_accounts_from_txt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "cookies.txt"
            cookie_file.write_text(
                json.dumps(
                    [
                        {
                            "c_user": "100000000000001",
                            "xs": "fake-xs-token",
                            "datr": "fake-datr",
                            "fr": "fake-fr",
                            "sb": "fake-sb",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            accounts = load_cookie_accounts(path=cookie_file, env={})

        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].c_user, "100000000000001")
        self.assertTrue(accounts[0].is_usable)
        header = cookie_header(accounts[0])
        self.assertIn("100000000000001", header)
        self.assertIn("fake-xs-token", header)

    def test_loads_cookie_accounts_from_json_env(self):
        env = {
            "UID_CHECKER_FB_COOKIES_JSON": json.dumps(
                [{"c_user": "100000000000002", "xs": "fake-xs-token"}]
            )
        }

        accounts = load_cookie_accounts(env=env)

        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].source, "UID_CHECKER_FB_COOKIES_JSON")
        self.assertTrue(accounts[0].is_usable)

    def test_loads_raw_cookie_header_from_json_string_env(self):
        raw = (
            "datr=fake-datr;sb=fake-sb;c_user=100000000000006;"
            "xs=fake-xs-token;fr=fake-fr;presence=fake-presence;"
            "useragent=TW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkg"
            "Q2hyb21lLzE0MC4wLjAuMCBTYWZhcmkvNTM3LjM2;"
        )
        env = {"UID_CHECKER_FB_COOKIES_JSON": json.dumps(raw)}

        account = load_cookie_accounts(env=env)[0]

        self.assertEqual(account.c_user, "100000000000006")
        self.assertTrue(account.is_usable)
        self.assertIn("fake-xs-token", cookie_header(account))
        self.assertNotIn("useragent=", cookie_header(account))
        self.assertIn("Chrome/140.0.0.0", account.browser_user_agent)

    def test_loads_raw_cookie_header_from_txt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "cookies.txt"
            cookie_file.write_text(
                '"datr=fake-datr; sb=fake-sb; c_user=100000000000007; xs=fake-xs-token;"',
                encoding="utf-8",
            )

            account = load_cookie_accounts(path=cookie_file, env={})[0]

        self.assertEqual(account.c_user, "100000000000007")
        self.assertTrue(account.is_usable)

    def test_loads_browser_cookie_export_array(self):
        env = {
            "UID_CHECKER_FB_COOKIES_JSON": json.dumps(
                [
                    {"domain": ".facebook.com", "name": "datr", "value": "fake-datr"},
                    {"domain": ".facebook.com", "name": "sb", "value": "fake-sb"},
                    {"domain": ".facebook.com", "name": "c_user", "value": "100000000000008"},
                    {"domain": ".facebook.com", "name": "xs", "value": "fake-xs-token"},
                    {"domain": ".facebook.com", "name": "fr", "value": "fake-fr"},
                ]
            )
        }

        account = load_cookie_accounts(env=env)[0]

        self.assertEqual(account.c_user, "100000000000008")
        self.assertTrue(account.is_usable)
        self.assertIn("fake-fr", cookie_header(account))

    def test_loads_individual_cookie_fields_from_env(self):
        env = {
            "UID_CHECKER_FB_C_USER": "100000000000003",
            "UID_CHECKER_FB_XS": "fake-xs-token",
        }

        accounts = load_cookie_accounts(env=env)

        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].source, "individual_env")
        self.assertTrue(accounts[0].is_usable)

    def test_masked_accounts_do_not_expose_secret_cookie_values(self):
        env = {
            "UID_CHECKER_FB_COOKIES_JSON": json.dumps(
                [{"c_user": "100000000000004", "xs": "fake-xs-token", "fr": "fake-fr"}]
            )
        }

        masked = masked_accounts(load_cookie_accounts(env=env))

        self.assertEqual(masked[0]["cUser"], "1000***0004")
        self.assertNotIn("fake-xs-token", json.dumps(masked))
        self.assertNotIn("fake-fr", json.dumps(masked))

    def test_useragent_metadata_is_not_sent_as_cookie(self):
        env = {
            "UID_CHECKER_FB_COOKIES_JSON": json.dumps(
                [
                    {
                        "c_user": "100000000000005",
                        "xs": "fake-xs-token",
                        "useragent": (
                            "TW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkg"
                            "Q2hyb21lLzEzNi4wLjAuMCBTYWZhcmkvNTM3LjM2"
                        ),
                    }
                ]
            )
        }

        account = load_cookie_accounts(env=env)[0]

        self.assertIn("Chrome/136.0.0.0", account.browser_user_agent)
        self.assertNotIn("useragent=", cookie_header(account))

    def test_default_local_cookie_file_points_to_service_root(self):
        self.assertEqual(DEFAULT_LOCAL_COOKIE_FILE.parent.name, "local_secrets")
        self.assertIn(DEFAULT_LOCAL_COOKIE_FILE.parent.parent.name, {"02-render-service", "clean-webhook-checker"})

    def test_lite_client_uses_bot_cookie_pool_only(self):
        account = CookieAccount(
            c_user="100000000000009",
            source="test",
            index=0,
            cookies={
                "c_user": "100000000000009",
                "xs": "fake-xs-token",
                "__user_agent": "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36",
            },
        )

        with unittest.mock.patch.object(fb_uid_lite_latest, "load_cookie_accounts", return_value=[account]):
            client = fb_uid_lite_latest.make_client(False)

        self.assertIn("100000000000009", client.headers.get("Cookie", ""))
        self.assertIn("Chrome/140.0.0.0", client.headers.get("User-Agent", ""))
        self.assertFalse(hasattr(fb_uid_lite_latest, "DEFAULT_COOKIE"))
        self.assertFalse(hasattr(fb_uid_lite_latest, "COOKIE"))


if __name__ == "__main__":
    unittest.main()
