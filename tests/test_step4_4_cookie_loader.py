import json
import tempfile
import unittest
from pathlib import Path

from app_modules.resolvers.facebook_cookies import (
    DEFAULT_LOCAL_COOKIE_FILE,
    cookie_header,
    load_cookie_accounts,
    masked_accounts,
)


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

    def test_default_local_cookie_file_points_to_service_root(self):
        self.assertEqual(DEFAULT_LOCAL_COOKIE_FILE.parent.name, "local_secrets")
        self.assertEqual(DEFAULT_LOCAL_COOKIE_FILE.parent.parent.name, "clean-webhook-checker")


if __name__ == "__main__":
    unittest.main()
