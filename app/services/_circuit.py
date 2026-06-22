"""
Circuit breaker per-provider pentru apelurile catre servicii externe.

Scop: cand un serviciu extern (Anthropic, Tavily, SearXNG, OpenAlex/CrossRef) e
jos, NU il mai lovim prin tot ciclul de retry la fiecare topic/rulare. Dupa N
esecuri consecutive circuitul se DESCHIDE: apelurile urmatoare esueaza instant
(fail-fast) pe o fereastra de cooldown. Dupa cooldown trece in HALF_OPEN si lasa
un singur apel de proba: succes -> CLOSED, esec -> OPEN cu cooldown dublat.

Starea e in memorie (per proces). Sigur fiindca scheduler-ul ruleaza intr-un
singur proces (vezi flock-ul de singleton din app/scheduler.py). Se reseteaza la
restart — acceptabil pentru un breaker.
"""
import logging
import time as _time
from typing import Callable, Dict, Optional

from app.services._utils import is_retryable_http

try:
    import anthropic
except ImportError:  # anthropic e dependinta, dar fii defensiv
    anthropic = None

logger = logging.getLogger(__name__)


class CircuitOpenError(Exception):
    """Circuitul e deschis: apelul a fost sarit (fail-fast) cat dureaza cooldown-ul."""


class ProviderDownError(Exception):
    """Ridicata de un provider cand serviciul extern e clar indisponibil
    (toate apelurile au esuat din motive de infrastructura, zero raspunsuri
    valide). Distinge 'serviciul e jos' de 'a raspuns, dar fara rezultate'.
    Mereu numarata ca esec de infrastructura de catre breaker."""


def is_infra_failure(exc: BaseException) -> bool:
    """True pentru esecuri de infrastructura (serviciul e jos/instabil), care
    trebuie sa contribuie la deschiderea circuitului.

    False pentru erori 'definitive' (auth 401, request invalid 400, config) —
    alea nu inseamna ca serviciul e jos, deci nu declanseaza breaker-ul.
    """
    if isinstance(exc, ProviderDownError):
        return True
    if is_retryable_http(exc):  # httpx: timeout, conexiune, 429/5xx
        return True
    if anthropic is not None:
        if isinstance(exc, anthropic.APIConnectionError):  # include APITimeoutError
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code == 429 or 500 <= exc.status_code < 600
    return False


class CircuitBreaker:
    """Breaker cu 3 stari (CLOSED / OPEN / HALF_OPEN) si cooldown exponential.

    `now` e injectabil (default time.monotonic) pentru a putea testa fara sleep.
    """

    def __init__(
        self,
        name: str,
        *,
        fail_threshold: int,
        cooldown: float,
        max_cooldown: Optional[float] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self.name = name
        self.fail_threshold = fail_threshold
        self.base_cooldown = cooldown
        self.max_cooldown = max_cooldown if max_cooldown is not None else cooldown * 6
        self._now = now or _time.monotonic
        self.failures = 0
        self.opened_at: Optional[float] = None   # timpul (monotonic) la deschidere
        self.current_cooldown = cooldown
        self.half_open = False

    def before(self) -> None:
        """De apelat INAINTE de apelul extern. Ridica CircuitOpenError daca
        circuitul e deschis si cooldown-ul nu a expirat. Daca a expirat, trece
        in half-open si lasa apelul (de proba) sa treaca."""
        if self.opened_at is None:
            return  # CLOSED — totul normal
        elapsed = self._now() - self.opened_at
        if elapsed < self.current_cooldown:
            remaining = self.current_cooldown - elapsed
            raise CircuitOpenError(
                f"Circuit '{self.name}' deschis — apel sarit (inca {remaining:.0f}s cooldown)"
            )
        # Cooldown expirat -> half-open: un singur apel de proba e permis
        self.half_open = True

    def record_success(self) -> None:
        """De apelat dupa un apel reusit (chiar daca a intors 0 rezultate)."""
        if self.opened_at is not None or self.failures:
            logger.info("[circuit] '%s' inchis dupa apel reusit", self.name)
        self.failures = 0
        self.opened_at = None
        self.half_open = False
        self.current_cooldown = self.base_cooldown

    def record_failure(self) -> None:
        """De apelat dupa un esec de infrastructura (is_infra_failure==True)."""
        self.failures += 1
        if self.half_open:
            # Proba din half-open a esuat -> redeschide cu cooldown dublat (plafonat)
            self.current_cooldown = min(self.current_cooldown * 2, self.max_cooldown)
            self.opened_at = self._now()
            self.half_open = False
            logger.warning(
                "[circuit] '%s' redeschis (proba half-open esuata), cooldown=%.0fs",
                self.name, self.current_cooldown,
            )
            return
        if self.failures >= self.fail_threshold and self.opened_at is None:
            self.opened_at = self._now()
            logger.warning(
                "[circuit] '%s' DESCHIS dupa %d esecuri consecutive, cooldown=%.0fs",
                self.name, self.failures, self.current_cooldown,
            )


# Praguri si cooldown-uri per provider. Rationament:
#  - anthropic: fiecare esec = pana la ~180s (retry lung); prag mic (2).
#  - tavily: platit, apel rapid; prag 3.
#  - searxng: local; prag 3, cooldown mic (proba ieftina).
#  - author (OpenAlex+CrossRef): gratuit, mai instabil; mai tolerant (prag 4).
_CONFIGS: Dict[str, dict] = {
    "anthropic": dict(fail_threshold=2, cooldown=300.0),
    "tavily":    dict(fail_threshold=3, cooldown=180.0),
    "searxng":   dict(fail_threshold=3, cooldown=120.0),
    "author":    dict(fail_threshold=4, cooldown=300.0),
}
_DEFAULT_CONFIG = dict(fail_threshold=3, cooldown=300.0)

_breakers: Dict[str, CircuitBreaker] = {}


def get_breaker(provider: str) -> CircuitBreaker:
    """Returneaza breaker-ul (singleton per proces) pentru un provider."""
    b = _breakers.get(provider)
    if b is None:
        cfg = _CONFIGS.get(provider, _DEFAULT_CONFIG)
        b = CircuitBreaker(provider, **cfg)
        _breakers[provider] = b
    return b


def reset_all() -> None:
    """Goleste registrul de breakere (folosit in teste pentru izolare)."""
    _breakers.clear()
