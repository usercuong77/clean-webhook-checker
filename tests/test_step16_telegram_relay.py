import os
import unittest
from unittest.mock import Mock, patch

from app_modules.core.config import get_config
from app_modules.telegram.telegram_relay import build_relay_target_url, relay_status, relay_telegram_webhook


class TelegramRelayTests(unittest.TestCase):
    def setUp(self):
        get_config.cache_clear()

    def tearDown(self):
        for key in ("TELEGRAM_RELAY_TARGET_URL", "WEBHOOK_SHARED_SECRET", "TELEGRAM_RELAY_TIMEOUT_SEC"):
            os.environ.pop(key, None)
        get_config.cache_clear()

    def test_build_relay_target_url_appends_secret(self):
        os.environ["TELEGRAM_RELAY_TARGET_URL"] = "https://script.google.com/macros/s/example/exec?a=1"
        os.environ["WEBHOOK_SHARED_SECRET"] = "secret value"
        get_config.cache_clear()

        url = build_relay_target_url()

        self.assertTrue(url.startswith("https://script.google.com/macros/s/example/exec?a=1&"))
        self.assertIn("secret=secret+value", url)

    def test_build_relay_target_url_keeps_existing_secret(self):
        os.environ["TELEGRAM_RELAY_TARGET_URL"] = "https://script.google.com/macros/s/example/exec?secret=already"
        os.environ["WEBHOOK_SHARED_SECRET"] = "new"
        get_config.cache_clear()

        self.assertEqual(
            build_relay_target_url(),
            "https://script.google.com/macros/s/example/exec?secret=already",
        )

    @patch("app_modules.telegram.telegram_relay.requests.post")
    def test_relay_detects_invalid_secret_body(self, post):
        os.environ["TELEGRAM_RELAY_TARGET_URL"] = "https://script.google.com/macros/s/example/exec"
        os.environ["WEBHOOK_SHARED_SECRET"] = "secret"
        get_config.cache_clear()
        post.return_value = Mock(status_code=200, text='{"ok":false,"error":"invalid_webhook_secret"}')

        result = relay_telegram_webhook(b"{}", "application/json")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "telegram_relay_invalid_webhook_secret")
        self.assertEqual(result["statusCode"], 502)

    def test_relay_status_is_safe(self):
        os.environ["TELEGRAM_RELAY_TARGET_URL"] = "https://script.google.com/macros/s/example/exec"
        os.environ["WEBHOOK_SHARED_SECRET"] = "secret"
        get_config.cache_clear()

        status = relay_status()

        self.assertTrue(status["telegramRelayConfigured"])
        self.assertTrue(status["telegramRelaySecretConfigured"])
        self.assertTrue(status["telegramRelayWillAttachSecret"])
        self.assertNotIn("secret", [value for value in status.values() if isinstance(value, str)])


if __name__ == "__main__":
    unittest.main()
