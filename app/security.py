from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

SALT = "sport-session-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=SALT)


def issue_session(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=settings.session_ttl_seconds)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return int(uid) if isinstance(uid, int) else None
