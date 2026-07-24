import unittest
from unittest.mock import Mock, patch

import requests

from app_modules.checkers.probes.mode1_graph_public import probe_mode1_graph_public


class Mode1GraphPublicTests(unittest.TestCase):
    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_graph_dimensions_are_live(self, get):
        get.return_value = _response(
            200,
            {
                "data": {
                    "height": 100,
                    "width": 100,
                    "is_silhouette": False,
                    "url": "https://scontent.xx.fbcdn.net/profile.jpg",
                }
            },
        )

        result = probe_mode1_graph_public("61574756686411")
        self.assertEqual(result.status, "LIVE")
        self.assertEqual(result.confidence, "strong")
        self.assertEqual(result.reason, "graph_profile_picture_dimensions")

    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_default_avatar_with_dimensions_is_live(self, get):
        get.return_value = _response(
            200,
            {
                "data": {
                    "height": 50,
                    "width": 50,
                    "is_silhouette": True,
                    "url": "https://static.xx.fbcdn.net/rsrc.php/v4/yh/r/default.gif",
                }
            },
        )

        result = probe_mode1_graph_public("61574756686411")
        self.assertEqual(result.status, "LIVE")
        self.assertEqual(result.confidence, "strong")
        self.assertEqual(result.reason, "graph_profile_picture_dimensions")

    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_silhouette_with_dimensions_is_live(self, get):
        get.return_value = _response(
            200,
            {
                "data": {
                    "height": 50,
                    "width": 50,
                    "is_silhouette": True,
                    "url": "https://scontent.xx.fbcdn.net/silhouette.jpg",
                }
            },
        )

        result = probe_mode1_graph_public("61574756686411")
        self.assertEqual(result.status, "LIVE")
        self.assertEqual(result.confidence, "strong")
        self.assertEqual(result.reason, "graph_profile_picture_dimensions")

    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_http_404_is_die(self, get):
        get.return_value = _response(404, {})

        result = probe_mode1_graph_public("61574756686411")
        self.assertEqual(result.status, "DIE")
        self.assertEqual(result.reason, "graph_http_404")

    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_transient_http_is_unknown(self, get):
        for status_code in (401, 403, 408, 425, 429, 500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                get.return_value = _response(status_code, {})
                result = probe_mode1_graph_public("61574756686411")
                self.assertEqual(result.status, "UNKNOWN")
                self.assertEqual(result.reason, f"graph_http_{status_code}")

    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_request_error_is_unknown(self, get):
        get.side_effect = requests.RequestException("timeout")

        result = probe_mode1_graph_public("61574756686411")

        self.assertEqual(result.status, "UNKNOWN")
        self.assertTrue(result.reason.startswith("request_error:"))

    @patch("app_modules.checkers.probes.mode1_graph_public.requests.get")
    def test_missing_dimensions_is_die(self, get):
        get.return_value = _response(
            200,
            {
                "data": {
                    "is_silhouette": True,
                    "url": "https://static.xx.fbcdn.net/rsrc.php/v4/yh/r/default.gif",
                }
            },
        )

        result = probe_mode1_graph_public("61574756686411")
        self.assertEqual(result.status, "DIE")
        self.assertEqual(result.confidence, "strong")
        self.assertEqual(result.reason, "graph_missing_picture_dimensions")


def _response(status_code, payload):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


if __name__ == "__main__":
    unittest.main()
