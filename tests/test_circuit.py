"""Teste pentru circuit breaker-ul per-provider (app/services/_circuit.py).

Ceasul e injectat (fara sleep real). Acoperim: deschidere dupa prag, fail-fast
cat e deschis, half-open dupa cooldown, inchidere la succes, redeschidere cu
cooldown exponential la proba esuata, reset de contor la succes, plus
clasificarea is_infra_failure si integrarea cu _dispatch_search.
"""
import httpx
import pytest

from app.services._circuit import (
    CircuitBreaker,
    CircuitOpenError,
    ProviderDownError,
    is_infra_failure,
    get_breaker,
    reset_all,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _breaker(fail_threshold=3, cooldown=100.0, max_cooldown=None):
    clock = FakeClock()
    b = CircuitBreaker(
        "test", fail_threshold=fail_threshold, cooldown=cooldown,
        max_cooldown=max_cooldown, now=clock,
    )
    return b, clock


# --------------------------------------------------------------------------- #
# Masina de stari
# --------------------------------------------------------------------------- #
def test_ramane_inchis_sub_prag():
    b, _ = _breaker(fail_threshold=3)
    b.record_failure()
    b.record_failure()
    b.before()  # 2 < 3 -> inca inchis, nu arunca


def test_se_deschide_la_prag_si_fail_fast():
    b, _ = _breaker(fail_threshold=3)
    for _ in range(3):
        b.record_failure()
    with pytest.raises(CircuitOpenError):
        b.before()


def test_half_open_dupa_cooldown_apoi_succes_inchide():
    b, clock = _breaker(fail_threshold=2, cooldown=100.0)
    b.record_failure()
    b.record_failure()  # deschis
    with pytest.raises(CircuitOpenError):
        b.before()
    clock.t = 100.0  # cooldown expirat
    b.before()  # half-open: lasa proba sa treaca
    assert b.half_open is True
    b.record_success()
    assert b.failures == 0
    b.before()  # inchis din nou


def test_proba_half_open_esuata_redeschide_cu_cooldown_dublat():
    b, clock = _breaker(fail_threshold=1, cooldown=100.0)
    b.record_failure()  # prag 1 -> deschis
    assert b.current_cooldown == 100.0
    clock.t = 100.0
    b.before()  # half-open
    b.record_failure()  # proba esuata -> reopen, cooldown 200
    assert b.current_cooldown == 200.0
    with pytest.raises(CircuitOpenError):
        b.before()  # inca in cooldown la t=100
    clock.t = 100.0 + 200.0
    b.before()  # half-open din nou dupa 200s


def test_cooldown_exponential_plafonat():
    b, clock = _breaker(fail_threshold=1, cooldown=100.0, max_cooldown=300.0)
    b.record_failure()  # deschis, cooldown 100
    for expected in (200.0, 300.0, 300.0):  # 100->200->300->plafon 300
        clock.t += b.current_cooldown
        b.before()
        b.record_failure()
        assert b.current_cooldown == expected


def test_succes_reseteaza_contorul_esecuri_neconsecutive():
    b, _ = _breaker(fail_threshold=3)
    b.record_failure()
    b.record_failure()
    b.record_success()  # reset
    b.record_failure()
    b.before()  # 1 esec dupa reset -> inca inchis


# --------------------------------------------------------------------------- #
# Clasificarea esecurilor
# --------------------------------------------------------------------------- #
def test_is_infra_failure_httpx_tranzitoriu():
    assert is_infra_failure(httpx.ConnectTimeout("x")) is True
    req = httpx.Request("GET", "http://x")
    assert is_infra_failure(
        httpx.HTTPStatusError("x", request=req, response=httpx.Response(503, request=req))
    ) is True


def test_is_infra_failure_httpx_definitiv_e_fals():
    req = httpx.Request("GET", "http://x")
    assert is_infra_failure(
        httpx.HTTPStatusError("x", request=req, response=httpx.Response(404, request=req))
    ) is False


def test_is_infra_failure_provider_down_si_altele():
    assert is_infra_failure(ProviderDownError("jos")) is True
    assert is_infra_failure(ValueError("config")) is False


# --------------------------------------------------------------------------- #
# Integrare cu _dispatch_search
# --------------------------------------------------------------------------- #
class _FakeTopic:
    provider = "tavily"
    user_question = None
    keywords = "x"


@pytest.fixture(autouse=True)
def _clean_breakers():
    reset_all()
    yield
    reset_all()


@pytest.mark.asyncio
async def test_dispatch_fail_fast_cand_e_deschis(monkeypatch):
    from app.routers import searches

    calls = {"n": 0}

    async def fake_call_provider(topic, telemetry):
        calls["n"] += 1
        raise ProviderDownError("tavily jos")

    monkeypatch.setattr(searches, "_call_provider", fake_call_provider)

    # tavily: prag 3. Primele 3 apeluri lovesc providerul si esueaza infra.
    for _ in range(3):
        with pytest.raises(ProviderDownError):
            await searches._dispatch_search(_FakeTopic(), {})
    assert calls["n"] == 3

    # Al 4-lea: circuit deschis -> fail-fast, providerul NU mai e apelat.
    with pytest.raises(CircuitOpenError):
        await searches._dispatch_search(_FakeTopic(), {})
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_dispatch_succes_nu_deschide(monkeypatch):
    from app.routers import searches

    async def fake_call_provider(topic, telemetry):
        return []  # raspuns valid, fara rezultate = succes

    monkeypatch.setattr(searches, "_call_provider", fake_call_provider)
    for _ in range(5):
        out = await searches._dispatch_search(_FakeTopic(), {})
        assert out == []


@pytest.mark.asyncio
async def test_dispatch_eroare_definitiva_nu_deschide(monkeypatch):
    from app.routers import searches

    calls = {"n": 0}

    async def fake_call_provider(topic, telemetry):
        calls["n"] += 1
        raise ValueError("API key not configured")  # non-infra

    monkeypatch.setattr(searches, "_call_provider", fake_call_provider)
    # Oricate erori definitive -> circuitul ramane inchis (providerul tot apelat).
    for _ in range(5):
        with pytest.raises(ValueError):
            await searches._dispatch_search(_FakeTopic(), {})
    assert calls["n"] == 5
