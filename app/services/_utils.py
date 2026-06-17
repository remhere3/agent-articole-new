"""Utilitare partajate intre providerii de cautare.

Aceste functii erau duplicate (copy-paste) in search_anthropic / search_tavily /
search_searxng / search_author. Le-am consolidat aici ca un fix
de regex sau o noua lista de domenii sa se faca intr-un singur loc.
"""
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Watermark-uri (IEEE / biblioteci universitare)
# ---------------------------------------------------------------------------
# Ex: "Authorized licensed use limited to: ... Downloaded on ... UTC from ...
#      Restrictions apply."
_WATERMARK_RE = re.compile(
    r'Authorized licensed use limited to:[^.]+\.\s*Downloaded on[^.]+\.\s*'
    r'(?:UTC\s*)?(?:from[^.]+\.)?\s*Restrictions apply\.?',
    flags=re.IGNORECASE,
)


def strip_watermarks(text: str) -> str:
    """Elimina watermark-urile de tip 'Authorized licensed use limited to: ...'."""
    return _WATERMARK_RE.sub('', text or '').strip()


# ---------------------------------------------------------------------------
# Parsare data — superset al tuturor variantelor folosite anterior
# ---------------------------------------------------------------------------
# Perechile (lungime_reala_data, format) — len(fmt) != lungimea datei reale.
# Ordinea conteaza: de la cel mai specific (ISO cu ora) la cel mai general (an).
_DATE_CANDIDATES = [
    (20, "%Y-%m-%dT%H:%M:%SZ"),
    (19, "%Y-%m-%dT%H:%M:%S"),
    (10, "%Y-%m-%d"),
    (7,  "%Y-%m"),
    (4,  "%Y"),
]


def parse_date(s) -> Optional[datetime]:
    """Parseaza o data dintr-un string in formate uzuale; None daca nu se potriveste."""
    if not s:
        return None
    text = str(s).strip()
    for length, fmt in _DATE_CANDIDATES:
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Domenii academice
# ---------------------------------------------------------------------------
# Lista e mentinuta in afara codului, in config/academic_domains.txt, ca sa
# poata fi editata fara modificari de cod. Se incarca o singura data, la import.
ACADEMIC_DOMAINS_FILE = Path(__file__).resolve().parents[2] / "config" / "academic_domains.txt"


def _load_academic_domains(path: Path = ACADEMIC_DOMAINS_FILE) -> list:
    """Citeste domeniile din fisierul de config (un domeniu pe linie).

    Ignora liniile goale si comentariile ('#', inclusiv inline). Domeniile sunt
    normalizate la litere mici si deduplicate, pastrand ordinea. Daca fisierul
    lipseste sau e ilizibil, logheaza o eroare si intoarce o lista goala.
    """
    domains: list = []
    seen: set = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Nu pot citi fisierul cu domenii academice (%s): %s", path, e)
        return domains
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line and line not in seen:
            seen.add(line)
            domains.append(line)
    if not domains:
        logger.warning("Lista de domenii academice e goala (%s)", path)
    return domains


ACADEMIC_DOMAINS = _load_academic_domains()


def domain(url: str) -> str:
    """Extrage domeniul (fara 'www.') dintr-un URL; '' daca esueaza."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def is_academic(url: str) -> bool:
    """True daca domeniul URL-ului e in lista academica (sau subdomeniu al ei)."""
    d = domain(url)
    return any(d == a or d.endswith("." + a) for a in ACADEMIC_DOMAINS)


# ---------------------------------------------------------------------------
# Potrivire nume autor (cautari dupa persoana)
# ---------------------------------------------------------------------------
_NAME_PART_RE = re.compile(r'^[A-ZÁÉÍÓÚĂÂÎȘȚ][a-záéíóúăâîșț\-]+$')


def looks_like_person_name(text: str) -> bool:
    """Heuristica: 2-4 cuvinte, fiecare capitalizat, fara cifre (ex. 'Roxana Ionete')."""
    parts = (text or "").strip().split()
    if not (2 <= len(parts) <= 4):
        return False
    return all(_NAME_PART_RE.match(p) for p in parts)


def _as_text(value) -> str:
    """Converteste orice valoare in text pentru cautare.

    SearXNG poate intoarce campuri (ex. `authors`) ca lista, nu string —
    `" ".join([...])` ar crapa cu 'expected str instance, list found'.
    Aici aplatizam liste/tuple si convertim scalarele in str.
    """
    if not value:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value)
    return str(value)


def _word_present(word: str, haystack: str) -> bool:
    """True daca `word` apare ca termen intreg in `haystack` (ambele lowercase)."""
    if not word:
        return False
    return re.search(rf'(?<!\w){re.escape(word)}(?!\w)', haystack) is not None


def author_in_result(name: str, item: dict) -> bool:
    """True daca rezultatul se refera la autorul cautat.

    Cere ca ATAT prenumele cat si numele de familie sa apara ca termeni intregi
    in titlu / continut / summary (in orice ordine). Astfel se resping potrivirile
    pe un singur cuvant (ex. cautand 'Roxana Ionete', un articol cu doar 'Roxana'
    NU trece), pastrand variantele 'Ionete, Roxana' sau 'Roxana Elena Ionete'.
    """
    haystack = " ".join(
        _as_text(item.get(k)) for k in ("title", "content", "summary", "authors")
    ).lower()
    parts = [p for p in (name or "").strip().split() if p]
    if len(parts) < 2:
        return bool(parts) and _word_present(parts[0].lower(), haystack)
    first, last = parts[0].lower(), parts[-1].lower()
    return _word_present(first, haystack) and _word_present(last, haystack)


# ---------------------------------------------------------------------------
# Reincercari cu backoff exponential pentru apeluri externe
# ---------------------------------------------------------------------------
# Coduri HTTP tranzitorii care merita reincercate (rate limit + erori server).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def is_retryable_http(exc: Exception) -> bool:
    """True pentru erori httpx tranzitorii: timeout, conexiune sau status 429/5xx.

    Erorile 'definitive' (400/401/403/404 etc.) intorc False — nu are sens sa le
    reincercam (cheie gresita, URL inexistent...).
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def describe_exc(e: BaseException) -> str:
    """Descriere lizibila a unei exceptii, utila cand str(e) e gol.

    Multe exceptii httpx (timeout, conexiune) au mesaj gol — fara tip, logul
    ar arata doar '()'. Ex. ReadTimeout fara mesaj -> 'ReadTimeout'.
    """
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


async def retry_async(
    fn: Callable[[], Awaitable],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    retry_on: Optional[Callable[[Exception], bool]] = None,
    label: str = "",
):
    """Apeleaza fn() (coroutine fara argumente) cu reincercari si backoff exponential.

    - attempts: numarul total de incercari (inclusiv prima).
    - base_delay: intarzierea dupa prima esuare; se dubleaza la fiecare reincercare
      (2s, 4s, 8s...), plafonata la max_delay.
    - retry_on(exc) -> bool: daca e dat, doar erorile pentru care intoarce True sunt
      reincercate; restul sunt aruncate imediat. Implicit: orice exceptie.

    Arunca ultima exceptie daca toate incercarile esueaza.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001 — filtram prin retry_on
            last_exc = e
            if attempt >= attempts or (retry_on is not None and not retry_on(e)):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "[retry] %s: incercarea %d/%d a esuat (%s); reincerc peste %.1fs",
                label or getattr(fn, "__name__", "call"),
                attempt, attempts, describe_exc(e), delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]  # inaccesibil: ultima incercare arunca direct
