import pytest

from app.services import provider_auth


def test_email_normalization_is_stable():
    assert provider_auth.normalize_email("  Person@Example.COM ") == "person@example.com"


@pytest.mark.parametrize("value", ["", "missing-at", "@example.com", "person@"])
def test_invalid_email_is_rejected(value):
    with pytest.raises(ValueError):
        provider_auth.normalize_email(value)


def test_google_authorize_url_contains_oidc_and_state(monkeypatch):
    monkeypatch.setattr(provider_auth.settings, "google_client_id", "client")
    url = provider_auth.google_authorize_url("csrf-state")
    assert "openid+email+profile" in url
    assert "state=csrf-state" in url
