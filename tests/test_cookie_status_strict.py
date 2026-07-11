import unittest

from app_modules.features.cookie_status import classify_cookie_response
from app_modules.resolvers.facebook_cookies import CookieAccount


class CookieStatusStrictTests(unittest.TestCase):
    def setUp(self):
        self.account = CookieAccount(
            c_user="100000000000001",
            source="test",
            index=0,
            cookies={"c_user": "100000000000001", "xs": "test"},
        )

    def test_cookie_id_mentioned_alone_is_not_live(self):
        status, reason = classify_cookie_response(
            self.account,
            "https://www.facebook.com/",
            'generic shell 100000000000001',
        )
        self.assertEqual(status, "UNKNOWN")
        self.assertEqual(reason, "no_login_or_logged_in_marker")

    def test_matching_actor_id_is_live(self):
        status, reason = classify_cookie_response(
            self.account,
            "https://www.facebook.com/",
            '"actorID":"100000000000001"',
        )
        self.assertEqual(status, "LIVE")
        self.assertEqual(reason, "viewer_id_matches_cookie")

    def test_explicit_mbasic_logout_is_live(self):
        status, reason = classify_cookie_response(
            self.account,
            "https://mbasic.facebook.com/notifications.php",
            '<a id="mbasic_logout_button" href="/logout.php?h=test">Logout</a>',
        )
        self.assertEqual(status, "LIVE")
        self.assertEqual(reason, "explicit_logout_marker_found")

    def test_login_redirect_is_expired(self):
        status, reason = classify_cookie_response(
            self.account,
            "https://www.facebook.com/login/?next=x",
            '<form id="login_form">Log in to Facebook</form>',
        )
        self.assertEqual(status, "EXPIRED_OR_LOGIN")
        self.assertEqual(reason, "redirected_to_login")


if __name__ == "__main__":
    unittest.main()
