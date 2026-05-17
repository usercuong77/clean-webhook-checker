import unittest
from unittest.mock import patch

from app_modules.api.controller import RealtimeBulkRequest, realtime_check_bulk


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

    def test_bulk_rejects_empty_and_unsupported_jobs(self):
        payload = realtime_check_bulk(
            RealtimeBulkRequest(
                jobs=[
                    {"id": "empty", "type": "uid", "input": ""},
                    {"id": "post", "type": "post", "input": "100000000000001"},
                ]
            )
        )

        self.assertEqual(payload["jobCount"], 2)
        self.assertFalse(payload["results"][0]["ok"])
        self.assertEqual(payload["results"][0]["reason"], "empty_input")
        self.assertFalse(payload["results"][1]["ok"])
        self.assertEqual(payload["results"][1]["reason"], "unsupported_job_type")


if __name__ == "__main__":
    unittest.main()
