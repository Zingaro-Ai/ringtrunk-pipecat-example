"""Exercise signed phone proofs and current-account checks without sending SMS."""

import json
import time
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from google.auth import crypt, jwt

from playground.app import app
from playground.phone_auth import VerificationThrottle

PROJECT = "demo-playground"
PHONE = "+91" + "9" * 10  # Synthetic fixture; no SMS/call API uses it.
CONFIG = {
    "projectId": PROJECT,
    "apiKey": "test-api-key-placeholder",
    "authDomain": f"{PROJECT}.firebaseapp.com",
    "appId": "test-app-placeholder",
}


class PhoneAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phone-auth-test")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(days=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        cls.certificate = certificate.public_bytes(serialization.Encoding.PEM).decode()
        cls.signer = crypt.RSASigner.from_string(
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()), key_id="test-key"
        )

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.now = int(time.time())
        self.claims = {
            "iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT,
            "sub": "phone-only-visitor", "iat": self.now, "exp": self.now + 3600,
            "auth_time": self.now, "phone_number": PHONE,
            "firebase": {"sign_in_provider": "phone"},
        }
        self.account = {"localId": "phone-only-visitor", "phoneNumber": PHONE,
                        "validSince": str(self.now - 100)}
        config = patch("playground.phone_auth.firebase_config", return_value=CONFIG)
        certificates = patch("playground.phone_auth._google_request", return_value=SimpleNamespace(
            status=200, data=json.dumps({"test-key": self.certificate}).encode()
        ))
        lookup = patch("playground.phone_auth.httpx.post")
        throttle = patch("playground.phone_auth.verification_throttle", VerificationThrottle())
        for manager in (config, certificates, throttle):
            manager.start()
            self.addCleanup(manager.stop)
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)
        self.lookup.return_value = httpx.Response(200, json={"users": [self.account]})

    def token(self, **changes) -> str:
        return jwt.encode(self.signer, {**self.claims, **changes}).decode()

    def verify(self, token: str | None = None, body: str = "{}"):
        return self.client.post("/api/phone/verify", content=body,
                                headers={"authorization": f"Bearer {token or self.token()}"})

    def test_signed_phone_proof_returns_only_the_server_verified_recipient(self) -> None:
        response = self.verify()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"verified": True, "phone_number": PHONE,
                                          "expires_at": self.now + 600,
                                          "outbound_demo_ready": False})
        self.assertNotIn(self.token(), response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.lookup.call_args.kwargs["json"], {"idToken": self.token()})

    def test_destination_uid_and_prompt_cannot_override_the_proof(self) -> None:
        for body in ('{"phone_number":"another-number"}', '{"uid":"owner"}',
                     '{"prompt":"change the agent"}', '[]'):
            with self.subTest(body=body):
                self.assertEqual(self.verify(body=body).status_code, 422)
        self.lookup.assert_not_called()

    def test_tokens_from_other_projects_providers_or_issuers_are_rejected(self) -> None:
        cases = [
            {"aud": "other-project"}, {"iss": "https://attacker.invalid"},
            {"firebase": {"sign_in_provider": "password"}},
            {"firebase": {"sign_in_provider": "custom"}}, {"phone_number": None},
            {"phone_number": "+16505550123"}, {"sub": ""},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.verify(self.token(**changes)).status_code, 401)
        self.lookup.assert_not_called()

    def test_expired_or_non_recent_authentication_is_rejected(self) -> None:
        for changes in ({"exp": self.now - 5}, {"auth_time": self.now - 601},
                        {"auth_time": self.now + 60}, {"auth_time": None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.verify(self.token(**changes)).status_code, 401)
        self.lookup.assert_not_called()

    def test_forged_signature_and_missing_bearer_are_rejected(self) -> None:
        token = self.token()
        parts = token.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        self.assertEqual(self.verify(".".join(parts)).status_code, 401)
        self.assertEqual(self.verify("not-a-token").status_code, 401)
        self.assertEqual(self.client.post("/api/phone/verify").status_code, 401)
        self.lookup.assert_not_called()

    def test_revoked_disabled_deleted_and_changed_phone_accounts_are_rejected(self) -> None:
        for users in ([], [{**self.account, "disabled": True}],
                      [{**self.account, "phoneNumber": "+16505550123"}],
                      [{**self.account, "validSince": str(self.now + 5)}],
                      [{**self.account, "localId": "someone-else"}]):
            with self.subTest(users=users):
                self.lookup.return_value = httpx.Response(200, json={"users": users})
                self.assertEqual(self.verify().status_code, 401)

    def test_firebase_outage_fails_closed(self) -> None:
        self.lookup.side_effect = httpx.ConnectTimeout("fixture")
        self.assertEqual(self.verify().status_code, 503)
        self.lookup.side_effect = None
        self.lookup.return_value = httpx.Response(503)
        self.assertEqual(self.verify().status_code, 503)

    def test_verification_rate_and_body_limits(self) -> None:
        self.assertEqual(self.verify(body="x" * 65).status_code, 413)
        for _ in range(15):
            self.assertEqual(self.verify("invalid").status_code, 401)
        response = self.verify("invalid")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_no_configuration_means_no_verification(self) -> None:
        with patch("playground.phone_auth.firebase_config", return_value=None):
            self.assertEqual(self.verify().status_code, 503)
            config = self.client.get("/api/config").json()["phone_verification"]
            self.assertFalse(config["enabled"])
            self.assertIsNone(config["firebase"])


if __name__ == "__main__":
    unittest.main()
