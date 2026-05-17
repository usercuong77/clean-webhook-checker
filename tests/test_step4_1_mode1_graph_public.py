import unittest
from unittest.mock import Mock, patch

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
