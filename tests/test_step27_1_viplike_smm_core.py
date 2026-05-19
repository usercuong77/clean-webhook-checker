import os
import unittest
from unittest.mock import patch

from app_modules.core.config import get_config
from app_modules.features.viplike import (
    CANONICAL_FACEBOOK_LIKE_PACKAGE_NAMES,
    SmmApiResult,
    build_viplike_order_dedupe_key,
    build_viplike_order_payload,
    create_viplike_order,
    get_viplike_packages,
    normalize_viplike_package_name,
)


class Step271VipLikeSmmCoreTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        for key in (
            "SMM_API_KEY",
            "SMM_API_BASE_URL",
            "SMM_API_DOMAIN",
            "VIPLIKE_ORDER_ENABLED",
        ):
            os.environ.pop(key, None)
        get_config.cache_clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        get_config.cache_clear()

    def test_fallback_packages_without_api_key(self):
        payload = get_viplike_packages()

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["apiConfigured"])
        self.assertGreaterEqual(payload["count"], 5)
        self.assertEqual(payload["source"], "fallback_smm_api_key_missing")

    def test_live_api_package_payload_is_normalized(self):
        os.environ["SMM_API_KEY"] = "test-key"
        get_config.cache_clear()
        api_payload = {
            "data": [
                {
                    "name": "Facebook Services",
                    "path": "/api/facebook-like-gia-re/buy",
                    "url_api": "/api/facebook-like-gia-re/buy",
                    "package": [
                        {
                            "id": "401",
                            "name": "S1 Like clone xin",
                            "package_name": "facebook like v401",
                            "min": "50",
                            "max": "100000",
                            "price_per": "30",
                        }
                    ],
                }
            ]
        }
        api_result = SmmApiResult(True, 200, "https://example.test/api/prices", api_payload, "{}", "", 3)

        with patch("app_modules.features.viplike.call_smm_api", return_value=api_result):
            payload = get_viplike_packages(refresh=True)

        self.assertTrue(payload["apiConfigured"])
        self.assertEqual(payload["source"], "api")
        self.assertEqual(payload["count"], 1)
        item = payload["packages"][0]
        self.assertEqual(item["packageName"], "facebook_like_v401")
        self.assertEqual(item["endpoint"], "/api/facebook-like-gia-re/buy")
        self.assertTrue(item["supportsReactionChoice"])
        self.assertIn("love", item["allowedReactionTypes"])

    def test_live_api_package_payload_is_limited_to_nine_facebook_like_packages(self):
        os.environ["SMM_API_KEY"] = "test-key"
        get_config.cache_clear()
        package_rows = [
            {"id": "twitter", "name": "X - Twitter Likes", "package_name": "twitter_likes", "min": "10", "max": "1000", "price_per": "614.79"},
            {"id": "shopee", "name": "Shopee like", "package_name": "shopee_like", "min": "5", "max": "1000", "price_per": "55"},
            {"id": "cheap", "name": "Tim gia re", "package_name": "like_fast_low_quality", "min": "100", "max": "100000", "price_per": "67.85"},
        ]
        for index, package_name in enumerate(CANONICAL_FACEBOOK_LIKE_PACKAGE_NAMES, start=1):
            package_rows.append(
                {
                    "id": str(index),
                    "name": f"Facebook package {index}",
                    "package_name": package_name,
                    "min": "50",
                    "max": "100000",
                    "price_per": str(index),
                }
            )

        api_payload = {
            "data": [
                {
                    "name": "Mixed like services",
                    "path": "/api/facebook-like-gia-re/buy",
                    "url_api": "/api/facebook-like-gia-re/buy",
                    "package": package_rows,
                }
            ]
        }
        api_result = SmmApiResult(True, 200, "https://example.test/api/prices", api_payload, "{}", "", 3)

        with patch("app_modules.features.viplike.call_smm_api", return_value=api_result):
            payload = get_viplike_packages(refresh=True)

        self.assertEqual(payload["source"], "api")
        self.assertEqual(payload["count"], 9)
        self.assertEqual(
            [item["packageName"] for item in payload["packages"]],
            list(CANONICAL_FACEBOOK_LIKE_PACKAGE_NAMES),
        )

    def test_order_dry_run_builds_payload_without_network(self):
        with patch("app_modules.features.viplike.call_smm_api") as call_smm_api:
            payload = create_viplike_order(
                {
                    "uid": "100000000000001",
                    "postId": "999888777",
                    "packageName": "facebook like v401",
                    "quantity": 100,
                    "reactionTypes": ["love", "like", "love"],
                }
            )

        call_smm_api.assert_not_called()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertFalse(payload["created"])
        self.assertEqual(payload["payload"]["object_id"], "999888777")
        self.assertEqual(payload["payload"]["package_name"], "facebook_like_v401")
        self.assertEqual(payload["payload"]["object_type"], "love|like")
        self.assertTrue(payload["dedupeKey"].startswith("vip1|100000000000001|999888777|"))

    def test_confirmed_order_is_blocked_until_enabled(self):
        with patch("app_modules.features.viplike.call_smm_api") as call_smm_api:
            payload = create_viplike_order(
                {
                    "uid": "100000000000001",
                    "postId": "999888777",
                    "packageName": "facebook_like_v3",
                    "quantity": 50,
                    "reactionType": "like",
                    "confirm": True,
                }
            )

        call_smm_api.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["created"])
        self.assertEqual(payload["reason"], "viplike_order_disabled")

    def test_dedupe_key_sorts_reactions(self):
        key_a = build_viplike_order_dedupe_key(
            {
                "uid": "100",
                "postId": "200",
                "packageName": "facebook like v3",
                "quantity": 50,
                "reactionTypes": ["love", "like"],
            }
        )
        key_b = build_viplike_order_dedupe_key(
            {
                "uid": "100",
                "postId": "200",
                "packageName": "facebook_like_v3",
                "quantity": "50",
                "reactionTypes": ["like", "love"],
            }
        )

        self.assertEqual(key_a, key_b)
        self.assertEqual(key_a, "vip1|100|200|facebook_like_v3|50|like,love")

    def test_payload_validation(self):
        self.assertEqual(build_viplike_order_payload({})["reason"], "missing_post_object_id")
        self.assertEqual(
            build_viplike_order_payload({"postId": "1", "quantity": 0, "packageName": "facebook_like"})["reason"],
            "invalid_quantity",
        )
        self.assertEqual(normalize_viplike_package_name("Facebook Like V401"), "facebook_like_v401")


if __name__ == "__main__":
    unittest.main()
