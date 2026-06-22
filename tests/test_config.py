"""Teste pentru politica optionala APP_SECRET_KEY (app/config.py).

`enforce_secret_key` e off implicit (cheia nu e obligatorie). Cand e on si cheia
e inca placeholder: in productie refuza pornirea, in debug doar avertizeaza.
"""
import logging

import pytest

from app.config import Settings


def _settings(**over):
    base = dict(
        enforce_secret_key=True,
        debug=False,
        app_secret_key="dev-secret-change-in-production",
    )
    base.update(over)
    return Settings(**base)


def test_off_nu_arunca_chiar_cu_cheie_default():
    # Toggle oprit -> cheia nu e obligatorie, indiferent de valoare/mediu.
    _settings(enforce_secret_key=False).verify_secret_key()


def test_on_cu_cheie_schimbata_nu_arunca():
    _settings(app_secret_key="o-cheie-reala-si-lunga").verify_secret_key()


def test_on_cheie_default_productie_refuza_pornirea():
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        _settings(debug=False).verify_secret_key()


def test_on_cheie_default_debug_doar_avertizeaza(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        _settings(debug=True).verify_secret_key()  # nu arunca
    assert any("APP_SECRET_KEY" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "key",
    ["dev-secret-change-in-production", "change_this_secret_key", "", "  "],
)
def test_secret_key_is_weak_detecteaza_placeholderele(key):
    assert _settings(app_secret_key=key).secret_key_is_weak is True


def test_secret_key_is_weak_false_pentru_cheie_proprie():
    assert _settings(app_secret_key="x" * 40).secret_key_is_weak is False
