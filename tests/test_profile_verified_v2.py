import unittest
from unittest.mock import patch

from app_modules.api.controller import CheckRequest, check_tick_v2_input
from app_modules.features.profile_verified.input_normalizer import normalize_profile_target
from app_modules.features.profile_verified.parser import parse_profile_document
from app_modules.features.profile_verified.probes import ProbeDocument
from app_modules.features.profile_verified.service import check_profile_verification
from app_modules.resolvers.facebook_cookies import CookieAccount


UID = "100003717317472"
PROFILE_HEADER = f'ProfileCometHeader profile_owner_id":"{UID}"'


class ProfileVerifiedV2Tests(unittest.TestCase):
    def test_normalizes_short_uid_without_minimum_length(self):
        target = normalize_profile_target("5")
        self.assertEqual(target.uid, "5")
        self.assertEqual(target.canonical_url, "https://www.facebook.com/profile.php?id=5")

    def test_parser_accepts_owner_scoped_verified_marker(self):
        parsed = parse_profile_document(
            f'{PROFILE_HEADER} "is_verified":true',
            f"https://www.facebook.com/profile.php?id={UID}",
            200,
            UID,
            "",
            True,
        )
        self.assertEqual(parsed.verification_state, "VERIFIED")
        self.assertTrue(parsed.conclusive)

    def test_parser_rejects_comment_author_verified_marker(self):
        parsed = parse_profile_document(
            f'{PROFILE_HEADER} CometUFIComment comment_author "is_verified":true',
            f"https://www.facebook.com/profile.php?id={UID}",
            200,
            UID,
            "",
            True,
        )
        self.assertEqual(parsed.verification_state, "NOT_VERIFIED")

    def test_parser_rejects_verified_marker_owned_by_other_uid(self):
        parsed = parse_profile_document(
            f'{PROFILE_HEADER} ProfileCometHeader "id":"999999999999999" "is_verified":true',
            f"https://www.facebook.com/profile.php?id={UID}",
            200,
            UID,
            "",
            True,
        )
        self.assertNotEqual(parsed.verification_state, "VERIFIED")

    @patch("app_modules.features.profile_verified.service.load_cookie_accounts", return_value=[])
    @patch("app_modules.features.profile_verified.service.fetch_profile_document")
    def test_public_verified_returns_without_cookie(self, fetch, _accounts):
        fetch.return_value = document(f'{PROFILE_HEADER} "show_verified_badge_on_profile":true')
        result = check_profile_verification(UID)
        self.assertEqual(result.verification_state, "VERIFIED")
        self.assertFalse(result.used_cookie)
        self.assertEqual(fetch.call_count, 1)

    @patch("app_modules.features.profile_verified.service.load_cookie_accounts", return_value=[])
    @patch("app_modules.features.profile_verified.service.fetch_profile_document")
    def test_public_profile_without_positive_marker_is_not_a_negative_conclusion(self, fetch, _accounts):
        fetch.return_value = document(PROFILE_HEADER)
        result = check_profile_verification(UID)
        self.assertEqual(result.verification_state, "UNKNOWN")
        self.assertFalse(result.conclusive)

    @patch("app_modules.features.profile_verified.service.load_cookie_accounts")
    @patch("app_modules.features.profile_verified.service.fetch_profile_document")
    def test_complete_cookie_profile_can_confirm_not_verified(self, fetch, accounts):
        accounts.return_value = [cookie_account()]
        fetch.side_effect = [
            document(PROFILE_HEADER),
            document(PROFILE_HEADER),
        ]
        result = check_profile_verification(UID)
        self.assertEqual(result.verification_state, "NOT_VERIFIED")
        self.assertTrue(result.conclusive)
        self.assertTrue(result.used_cookie)

    @patch("app_modules.features.profile_verified.service.load_cookie_accounts")
    @patch("app_modules.features.profile_verified.service.fetch_profile_document")
    def test_auth_wall_falls_back_to_cookie(self, fetch, accounts):
        accounts.return_value = [cookie_account()]
        fetch.side_effect = [
            document("Log in to Facebook", final_url=f"https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fprofile.php%3Fid%3D{UID}"),
            document(f'{PROFILE_HEADER} "is_verified":true'),
        ]
        result = check_profile_verification(UID)
        self.assertEqual(result.verification_state, "VERIFIED")
        self.assertTrue(result.used_cookie)
        self.assertEqual(fetch.call_count, 2)

    @patch("app_modules.features.profile_verified.service.load_cookie_accounts")
    @patch("app_modules.features.profile_verified.service.fetch_profile_document")
    def test_network_failures_remain_unknown(self, fetch, accounts):
        accounts.return_value = [cookie_account()]
        fetch.return_value = document("", http_code=0, reason="request_error:ReadTimeout", complete=False)
        result = check_profile_verification(UID)
        self.assertEqual(result.verification_state, "UNKNOWN")
        self.assertFalse(result.conclusive)
        self.assertNotEqual(result.verification_state, "NOT_VERIFIED")

    @patch("app_modules.api.controller.check_profile_verification")
    def test_controller_contract_does_not_request_or_infer_name(self, check):
        check.return_value = check_profile_verification_result()
        payload = check_tick_v2_input(CheckRequest(input=UID, includeName=True))
        self.assertEqual(payload["verificationState"], "VERIFIED")
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["name"], "")
        self.assertEqual(payload["nameReason"], "name_not_requested")


def document(
    text: str,
    *,
    final_url: str = f"https://www.facebook.com/profile.php?id={UID}",
    http_code: int = 200,
    reason: str = "ok",
    complete: bool = True,
) -> ProbeDocument:
    return ProbeDocument(
        http_code=http_code,
        text=text,
        final_url=final_url,
        reason=reason,
        elapsed_ms=10,
        bytes_read=len(text.encode("utf-8")),
        complete=complete,
    )


def cookie_account() -> CookieAccount:
    return CookieAccount(
        c_user="100000000000001",
        source="test",
        index=0,
        cookies={"c_user": "100000000000001", "xs": "test"},
    )


def check_profile_verification_result():
    from app_modules.features.profile_verified.models import ProfileVerificationResult

    return ProfileVerificationResult(
        verification_state="VERIFIED",
        profile_state="VISIBLE",
        conclusive=True,
        uid=UID,
        username="",
        canonical_url=f"https://www.facebook.com/profile.php?id={UID}",
        source="profile_verified_v2_public",
        reason="verified_marker_found",
        http_code=200,
        elapsed_ms=10,
        probes=[],
        used_cookie=False,
    )


if __name__ == "__main__":
    unittest.main()
