import base64
import hashlib
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


AUTHORIZE_URL = "https://access.line.me/oauth2/v2.1/authorize"
TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
FRIENDSHIP_URL = "https://api.line.me/friendship/v1/status"
EXPECTED_ISSUER = "https://access.line.me"


class LineLoginError(Exception):
    """A safe, non-secret LINE Login failure."""


def generate_oauth_values():
    verifier = secrets.token_urlsafe(64).rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "code_verifier": verifier,
        "code_challenge": challenge,
    }


def authorization_url(*, state, nonce, code_challenge):
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.LINE_LOGIN_CHANNEL_ID,
            "redirect_uri": settings.LINE_LOGIN_CALLBACK_URL,
            "state": state,
            "scope": "openid profile",
            "nonce": nonce,
            "bot_prompt": "aggressive",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "ui_locales": "zh-TW",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _request_json(request):
    try:
        with urlopen(request, timeout=settings.LINE_API_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8")), response.status
    except HTTPError as exc:
        raise LineLoginError(f"LINE API rejected the request ({exc.code}).") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LineLoginError("LINE API is temporarily unavailable.") from exc


def _post_form(url, data):
    request = Request(
        url,
        data=urlencode(data).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _request_json(request)[0]


def exchange_code(*, code, code_verifier):
    result = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.LINE_LOGIN_CALLBACK_URL,
            "client_id": settings.LINE_LOGIN_CHANNEL_ID,
            "client_secret": settings.LINE_LOGIN_CHANNEL_SECRET,
            "code_verifier": code_verifier,
        },
    )
    if not result.get("access_token") or not result.get("id_token"):
        raise LineLoginError("LINE token response was incomplete.")
    return result


def verify_id_token(*, id_token, nonce):
    claims = _post_form(
        VERIFY_URL,
        {"id_token": id_token, "client_id": settings.LINE_LOGIN_CHANNEL_ID, "nonce": nonce},
    )
    try:
        expiration = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LineLoginError("LINE ID token was invalid.") from exc
    if claims.get("iss") != EXPECTED_ISSUER:
        raise LineLoginError("LINE ID token issuer was invalid.")
    if claims.get("aud") != settings.LINE_LOGIN_CHANNEL_ID:
        raise LineLoginError("LINE ID token audience was invalid.")
    if claims.get("nonce") != nonce:
        raise LineLoginError("LINE ID token nonce was invalid.")
    if expiration <= int(time.time()):
        raise LineLoginError("LINE ID token has expired.")
    if not claims.get("sub") or not claims.get("name"):
        raise LineLoginError("LINE profile was incomplete.")
    return claims


def get_friendship_status(access_token):
    request = Request(FRIENDSHIP_URL, headers={"Authorization": f"Bearer {access_token}"}, method="GET")
    result, _ = _request_json(request)
    if not isinstance(result.get("friendFlag"), bool):
        raise LineLoginError("LINE friendship response was invalid.")
    return result["friendFlag"]
