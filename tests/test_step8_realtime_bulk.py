import unittest
from unittest.mock import patch

from app_modules.api.controller import (
    RealtimeBulkRequest,
    _realtime_bulk_post_worker_count,
    realtime_check_bulk,
)


class Step8RealtimeBulkTests(unittest.TestCase):
    @patch("app_modules.api.controller.check_input")
    def test_bulk_uid_jobs_keep_id_and_type(self, check_input):
        check_input.return_value = {
            "ok": True,
            "status": "LIVE",
            "confidence": "strong",
            "uid": "100000000000001",
            "reason": "ok",
            "httpCode": 200,
            "elapsedMs": 1,
        }

        payload = realtime_check_bulk(
            RealtimeBulkRequest(
                jobs=[
                    {
                        "id": "W1",
                        "type": "uid",
                        "input": "100000000000001",
                        "mode": "1",
                        "includeName": False,
                    }
                ]
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["jobCount"], 1)
        self.assertEqual(payload["results"][0]["id"], "W1")
        self.assertEqual(payload["results"][0]["type"], "uid")
        self.assertEqual(payload["results"][0]["status"], "LIVE")

    @patch("app_modules.api.controller.latest_post_input")
    def test_bulk_post_jobs_keep_id_and_type(self, latest_post_input):
        latest_post_input.return_value = {
            "ok": True,
            "uid": "100000000000001",
            "postId": "123456789012345",
            "link": "https://www.facebook.com/100000000000001/posts/123456789012345",
            "content": "Latest post",
            "reason": "ok",
            "httpCode": 200,
            "elapsedMs": 1,
        }

        payload = realtime_check_bulk(
            RealtimeBulkRequest(
                jobs=[
                    {
                        "id": "P1",
                        "type": "post",
                        "input": "100000000000001",
                    }
                ]
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["jobCount"], 1)
        self.assertEqual(payload["results"][0]["id"], "P1")
        self.assertEqual(payload["results"][0]["type"], "post")
        self.assertEqual(payload["results"][0]["postId"], "123456789012345")

    def test_bulk_rejects_empty_and_unsupported_jobs(self):
        payload = realtime_check_bulk(
            RealtimeBulkRequest(
                jobs=[
                    {"id": "empty", "type": "uid", "input": ""},
                    {"id": "bad", "type": "viplike", "input": "100000000000001"},
                ]
            )
        )

        self.assertEqual(payload["jobCount"], 2)
        self.assertFalse(payload["results"][0]["ok"])
        self.assertEqual(payload["results"][0]["reason"], "empty_input")
        self.assertFalse(payload["results"][1]["ok"])
        self.assertEqual(payload["results"][1]["reason"], "unsupported_job_type")

    @patch.dict("os.environ", {"REALTIME_BULK_POST_MAX_WORKERS": "4"})
    def test_post_worker_count_uses_env_cap(self):
        self.assertEqual(_realtime_bulk_post_worker_count(10), 4)
        self.assertEqual(_realtime_bulk_post_worker_count(2), 2)

    @patch.dict("os.environ", {"REALTIME_BULK_POST_MAX_WORKERS": "bad"})
    def test_post_worker_count_falls_back_to_safe_default(self):
        self.assertEqual(_realtime_bulk_post_worker_count(10), 2)


if __name__ == "__main__":
    unittest.main()
