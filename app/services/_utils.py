"""Utilitare partajate intre providerii de cautare.

Aceste functii erau duplicate (copy-paste) in search_anthropic / search_tavily /
search_searxng / search_ollama / search_author. Le-am consolidat aici ca un fix
de regex sau o noua lista de domenii sa se faca intr-un singur loc.
"""
import asyncio
import logging
import re
from datetime import datetime
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
# Lista consolidata (superset din search_tavily + search_searxng).
ACADEMIC_DOMAINS = [
    # Preprint / Open Access
    "arxiv.org", "biorxiv.org", "medrxiv.org", "plos.org", "frontiersin.org",
    "mdpi.com", "elifesciences.org", "zenodo.org", "scielo.org",
    # Baze de date rezumate
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nih.gov",
    "europepmc.org", "semanticscholar.org",
    # Edituri mari
    "nature.com", "science.org", "cell.com", "springer.com", "wiley.com",
    "tandfonline.com", "sciencedirect.com", "academic.oup.com",
    "jamanetwork.com", "nejm.org", "thelancet.com",
    # Tehnic / CS
    "ieee.org", "acm.org", "iopscience.iop.org",
    # Chimie / Energie
    "rsc.org", "pubs.acs.org", "pubs.rsc.org",
    # Retele academice
    "researchgate.net", "academia.edu",
    # Energie / Hidrogen / Electroliza
    "ecs.org", "ecst.ecsdl.org",                         # Electrochemical Society
    "nrel.gov",                                           # National Renewable Energy Lab
    "energy.gov", "hydrogen.energy.gov",                  # US Dept of Energy
    "irena.org",                                          # Int'l Renewable Energy Agency
    "chemrxiv.org",                                       # Preprint chimie
    "biomedcentral.com",                                  # BioMed Central open access
    "core.ac.uk",                                         # Agregator open access
    # Rezolvatori DOI / agregatori — frecvente in SearXNG science
    "doi.org", "dx.doi.org", "hdl.handle.net",
    "ieeexplore.ieee.org", "link.springer.com",
    "onlinelibrary.wiley.com", "dl.acm.org",
    "hal.science", "hal.archives-ouvertes.fr",
]


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
                label or getattr(fn, "__name__", "call"), attempt, attempts, e, delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]  # inaccesibil: ultima incercare arunca direct
