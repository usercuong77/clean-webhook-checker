from unittest import TestCase
from unittest.mock import Mock, patch

from app_modules.core import render_scheduler


class RenderSchedulerTests(TestCase):
    def setUp(self) -> None:
        render_scheduler._STARTED = False

    @patch.dict("os.environ", {"RENDER_PUBLIC_URL": "https://clean-webhook-checker.onrender.com"}, clear=True)
    def test_primary_is_enabled_by_default(self) -> None:
        self.assertTrue(render_scheduler._scheduler_enabled())

    @patch.dict("os.environ", {"RENDER_PUBLIC_URL": "https://clean-webhook-checker-a2.onrender.com"}, clear=True)
    def test_clone_is_disabled_by_default(self) -> None:
        self.assertFalse(render_scheduler._scheduler_enabled())

    @patch.dict(
        "os.environ",
        {"CLOUDFLARE_RENDER_REGISTER_URL": "https://gateway.example/render/register"},
        clear=True,
    )
    def test_endpoint_is_derived_from_registration_url(self) -> None:
        self.assertEqual(
            render_scheduler._scheduler_endpoint(),
            "https://gateway.example/internal/scheduled/run",
        )

    @patch.dict(
        "os.environ",
        {
            "CLOUDFLARE_RENDER_REGISTER_URL": "https://gateway.example/render/register",
            "RENDER_REGISTRATION_SECRET": "secret",
        },
        clear=True,
    )
    @patch("app_modules.core.render_scheduler.requests.post")
    def test_trigger_uses_registration_secret(self, post: Mock) -> None:
        post.return_value = Mock(status_code=202)
        result = render_scheduler.trigger_gateway_once()
        self.assertTrue(result["ok"])
        self.assertEqual(post.call_args.kwargs["headers"]["x-render-registration-secret"], "secret")
