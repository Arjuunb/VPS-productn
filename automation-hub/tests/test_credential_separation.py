import pytest

import config


def test_overlapping_control_and_webhook_keys_fail(monkeypatch):
    monkeypatch.setattr(config.settings, "admin_key", "same-key")
    monkeypatch.setattr(config.settings, "webhook_secret", "same-key")
    with pytest.raises(RuntimeError, match="must be different"):
        config.validate_credential_separation()


def test_exchange_key_cannot_overlap_control_key(monkeypatch):
    monkeypatch.setattr(config.settings, "admin_key", "exchange-key")
    monkeypatch.setattr(config.settings, "webhook_secret", "webhook-key")
    monkeypatch.setenv("HUB_EXCHANGE_API_KEY", "exchange-key")
    monkeypatch.setenv("HUB_EXCHANGE_API_SECRET", "exchange-secret")
    with pytest.raises(RuntimeError, match="HUB_CONTROL_KEY.*HUB_EXCHANGE_API_KEY"):
        config.validate_credential_separation()


def test_production_paper_requires_control_and_webhook_but_not_exchange(monkeypatch):
    monkeypatch.setattr(config.settings, "admin_key", "control-key")
    monkeypatch.setattr(config.settings, "webhook_secret", "webhook-key")
    monkeypatch.setattr(config.settings, "external_live_enabled", False)
    monkeypatch.delenv("HUB_EXCHANGE_API_KEY", raising=False)
    monkeypatch.delenv("HUB_EXCHANGE_API_SECRET", raising=False)
    config.validate_credential_separation(production=True)
