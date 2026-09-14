"""Verify Firebase phone identities before accepting a callback recipient.

Only public Firebase configuration and Google's signing certificates are needed.
This module does not create RingTrunk accounts, write Firestore, or place calls.
"""

import json
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import requests
from cachecontrol import CacheControl
from fastapi import HTTPException, Request
from google.auth import exceptions as google_errors
from google.auth.transport import Response as GoogleResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

MAX_TOKEN_BYTES = 8192
PHONE_AUTH_MAX_AGE_SECONDS = 600
PHONE_RE = re.compile(r"\+91[6-9]\d{9}")
_thread_local = threading.local()


@dataclass(frozen=True)
class PhoneIdentity:
    uid: str
    phone_number: str
    expires_at: int


@lru_cache(maxsize=4)
def _read_config(path: str) -> dict[str, str] | None:
    if not path:
        return None
    try:
        content = Path(path).read_text(encoding="utf-8")
        if len(content) > 8192:
            return None
        config = json.loads(content)
        project = config["projectId"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,29}", project):
            return None
        if config["authDomain"] != f"{project}.firebaseapp.com":
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", config["apiKey"]):
            return None
        # Explicit allowlist: a service-account JSON file can never be exposed.
        return {key: config[key] for key in ("apiKey", "authDomain", "projectId", "appId")}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def firebase_config() -> dict[str, str] | None:
    return _read_config(os.environ.get("RINGTRUNK_FIREBASE_CONFIG", ""))


def phone_public_config() -> dict:
    config = firebase_config()
    return {
        "enabled": config is not None,
        "firebase": config,
        "resend_after_seconds": 60,
        "verification_valid_seconds": PHONE_AUTH_MAX_AGE_SECONDS,
        "preview_origin": os.environ.get("RINGTRUNK_PREVIEW_ORIGIN", ""),
    }


def _google_request(url: str, method: str = "GET", **kwargs: Any) -> GoogleResponse:
    # A session per worker thread avoids sharing requests.Session between threads.
    if not hasattr(_thread_local, "google_request"):
        _thread_local.google_request = GoogleRequest(CacheControl(requests.Session()))
    kwargs["timeout"] = 8
    return _thread_local.google_request(url, method=method, **kwargs)


def verify_phone_token(token: str) -> PhoneIdentity:
    config = firebase_config()
    if config is None:
        raise HTTPException(503, "Phone verification is not configured.")
    if not token or len(token) > MAX_TOKEN_BYTES or token.count(".") != 2:
        raise HTTPException(401, "Verify your mobile number to continue.")
    try:
        claims = id_token.verify_firebase_token(
            token, _google_request, audience=config["projectId"]
        )
    except (ValueError, google_errors.InvalidValue, google_errors.MalformedError):
        raise HTTPException(401, "Your verification has expired. Request a new code.") from None
    except (google_errors.GoogleAuthError, requests.RequestException):
        raise HTTPException(503, "Verification is temporarily unavailable. Please retry.") from None

    now = int(time.time())
    phone = claims.get("phone_number")
    uid = claims.get("sub")
    auth_time = claims.get("auth_time")
    expiry = claims.get("exp")
    firebase = claims.get("firebase")
    if (
        claims.get("iss") != f"https://securetoken.google.com/{config['projectId']}"
        or claims.get("aud") != config["projectId"]
        or not isinstance(uid, str)
        or not 1 <= len(uid) <= 128
        or not isinstance(phone, str)
        or not PHONE_RE.fullmatch(phone)
        or not isinstance(firebase, dict)
        or firebase.get("sign_in_provider") != "phone"
        or type(auth_time) is not int
        or not now - PHONE_AUTH_MAX_AGE_SECONDS <= auth_time <= now
        or type(expiry) is not int
        or expiry <= now
    ):
        raise HTTPException(401, "Verify your Indian mobile number with a new SMS code.")

    # Check the current account too: reject disabled/deleted users, changed phone
    # numbers and revoked sessions instead of trusting a still-signed old token.
    try:
        response = httpx.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:lookup",
            params={"key": config["apiKey"]},
            json={"idToken": token},
            timeout=8,
        )
        if response.status_code >= 500 or response.status_code == 429:
            raise HTTPException(503, "Verification is temporarily unavailable. Please retry.")
        if response.status_code != 200:
            raise HTTPException(401, "Your verification has expired. Request a new code.")
        users = response.json().get("users", [])
        if len(users) != 1:
            raise ValueError("Missing account")
        user = users[0]
        if (
            user.get("localId") != uid
            or user.get("phoneNumber") != phone
            or user.get("disabled") is True
            or int(user.get("validSince", 0)) > auth_time
        ):
            raise ValueError("Account no longer matches the phone proof")
    except httpx.HTTPError:
        raise HTTPException(503, "Verification is temporarily unavailable. Please retry.") from None
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Verify your mobile number again.") from None
    return PhoneIdentity(uid, phone, min(expiry, auth_time + PHONE_AUTH_MAX_AGE_SECONDS))


class VerificationThrottle:
    """Bound local verification attempts; Firebase independently limits SMS sends."""

    def __init__(self) -> None:
        self._attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            started, count = self._attempts.get(key, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            if count >= 15:
                raise HTTPException(429, "Too many verification attempts. Try again shortly.",
                                    headers={"Retry-After": "60"})
            self._attempts[key] = (started, count + 1)
            self._attempts.move_to_end(key)
            while len(self._attempts) > 4096:
                self._attempts.popitem(last=False)


verification_throttle = VerificationThrottle()


def verified_callback_identity(request: Request) -> PhoneIdentity:
    """Future call handlers must obtain the recipient here, never from JSON input."""
    verification_throttle.check(request.client.host if request.client else "unknown")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "Verify your mobile number to continue.")
    return verify_phone_token(token)
