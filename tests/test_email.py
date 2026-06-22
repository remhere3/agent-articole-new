"""Teste pentru serviciul de email (app/services/email_service.py).

Acopera in special timeout-ul SMTP explicit: un server blocat nu trebuie sa
tina jobul ostatic, iar `send_report` trebuie sa intoarca False (nu sa arunce).
`aiosmtplib.send` e mock-uit — fara SMTP real, fara retea.
"""
import aiosmtplib
import pytest

from app.services import email_service


def _args(**over):
    base = dict(
        to_addresses=["a@b.ro"],
        topic_name="Test",
        keywords="x",
        days_back=7,
        articles=[],
        run_id=1,
    )
    base.update(over)
    return base


@pytest.fixture
def smtp_configured(monkeypatch):
    monkeypatch.setattr(email_service.settings, "smtp_user", "user@b.ro")
    monkeypatch.setattr(email_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(email_service.settings, "smtp_timeout", 12.5)


@pytest.mark.asyncio
async def test_pasaza_timeout_explicit_la_send(smtp_configured, monkeypatch):
    captured = {}

    async def fake_send(msg, **kwargs):
        captured.update(kwargs)
        return {}, "OK"

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    ok = await email_service.send_report(**_args())
    assert ok is True
    assert captured["timeout"] == 12.5  # timeout-ul din settings e propagat


@pytest.mark.asyncio
async def test_timeout_smtp_intoarce_false_nu_arunca(smtp_configured, monkeypatch):
    async def fake_send(msg, **kwargs):
        raise aiosmtplib.SMTPTimeoutError("blocat")

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    ok = await email_service.send_report(**_args())
    assert ok is False


@pytest.mark.asyncio
async def test_eroare_generica_intoarce_false(smtp_configured, monkeypatch):
    async def fake_send(msg, **kwargs):
        raise aiosmtplib.SMTPException("eroare server")

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    ok = await email_service.send_report(**_args())
    assert ok is False


@pytest.mark.asyncio
async def test_fara_credentiale_nu_trimite(monkeypatch):
    monkeypatch.setattr(email_service.settings, "smtp_user", None)
    monkeypatch.setattr(email_service.settings, "smtp_password", None)

    called = {"n": 0}

    async def fake_send(msg, **kwargs):
        called["n"] += 1
        return {}, "OK"

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    ok = await email_service.send_report(**_args())
    assert ok is False
    assert called["n"] == 0  # nu s-a incercat trimiterea
