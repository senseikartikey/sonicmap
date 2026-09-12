import uuid

import jwt

from app.services import jwt_auth


def test_session_token_has_required_claims_and_decodes():
    user_id = uuid.uuid4()
    token = jwt_auth.create_session_token(user_id)
    unverified = jwt.decode(token, options={"verify_signature": False})
    assert {"sub", "iat", "exp", "iss", "aud", "jti"} <= unverified.keys()
    assert jwt_auth.decode_session_token(token) == user_id


def test_documented_development_secret_cannot_forge_session():
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": 1_800_000_000,
            "exp": 1_900_000_000,
            "iss": jwt_auth.ISSUER,
            "aud": jwt_auth.AUDIENCE,
            "jti": str(uuid.uuid4()),
        },
        "dev-only-change-me",
        algorithm="HS256",
    )
    assert jwt_auth.decode_session_token(forged) is None


def test_token_with_missing_security_claims_is_rejected():
    token = jwt.encode({"sub": str(uuid.uuid4())}, jwt_auth._SIGNING_KEY, algorithm="HS256")
    assert jwt_auth.decode_session_token(token) is None
