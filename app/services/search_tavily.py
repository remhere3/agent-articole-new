"""
Cautare articole stiintifice via Tavily Search API.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from tavily import TavilyClient

logger = logging.getLogger(__name__)


async def _tavily_call_with_retry(client: "TavilyClient", max_retries: int = 3, **kwargs) -> dict:
    """Apeleaza client.search cu retry si backoff exponential (2s, 4s, 8s)."""
    waits = [2, 4, 8]
    for attempt in range(1, max_retries + 1):
        try:
            return await asyncio.to_thread(client.search, **kwargs)
        except Exception as e:
            if attempt == max_retries:
                raise
            wait = waits[attempt - 1]
            logger.warning(f"[Tavily] retry {attempt}/{max_retries} dupa eroare ({e}) — astept {wait}s...")
            await asyncio.sleep(wait)
    return {}

ACADEMIC_DOMAINS = [
    # Preprint / Open Access
    "arxiv.org", "biorxiv.org", "medrxiv.org", "plos.org", "frontiersin.org",
    "mdpi.com", "elifesciences.org", "zenodo.org", "scielo.org",
    "osf.io", "ssrn.com", "hal.science", "hal.archives-ouvertes.fr",
    "chemrxiv.org", "techrxiv.org", "essoar.org", "eartharxiv.org",
    "authorea.com", "preprints.org", "psyarxiv.com", "socarxiv.org",
    # Baze de date / Motoare de cautare academice
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nih.gov",
    "europepmc.org", "semanticscholar.org",
    "openalex.org", "lens.org", "dimensions.ai",
    "doaj.org", "base-search.net", "unpaywall.org",
    "scilit.net", "fatcat.wiki",
    # Edituri mari
    "nature.com", "science.org", "cell.com", "springer.com", "wiley.com",
    "tandfonline.com", "sciencedirect.com", "academic.oup.com",
    "jamanetwork.com", "nejm.org", "thelancet.com",
    "cambridge.org", "sagepub.com", "karger.com", "degruyter.com",
    "pnas.org", "royalsocietypublishing.org", "bmj.com", "peerj.com",
    "jmir.org", "hindawi.com", "scirp.org",
    # Tehnic / CS / Inginerie
    "ieee.org", "acm.org", "iopscience.iop.org",
    "asme.org", "asce.org", "aiaa.org",
    "dl.acm.org", "ieeexplore.ieee.org",
    # Chimie / Materiale / Energie
    "rsc.org", "pubs.acs.org", "pubs.rsc.org",
    "acs.org", "chemistryworld.com",
    # Energie / Hidrogen / Electroliza / Mediu
    "ecs.org", "ecst.ecsdl.org",
    "nrel.gov", "energy.gov", "hydrogen.energy.gov",
    "irena.org", "iea.org",
    "energies-mdpi.com", "sciencedirect.com",
    "cleantechnica.com",                                  # stiri tehnice energie curata
    # Biologie / Medicina / Sanatate
    "biomedcentral.com", "publichealthontario.ca",
    "who.int", "cdc.gov", "embase.com",
    "cochranelibrary.com", "healthaffairs.org",
    # Fizica / Matematica / Astronomie
    "aps.org", "aip.org", "spie.org",
    "eso.org", "nasa.gov", "aanda.org",
    # Stiinte sociale / Economie
    "nber.org", "repec.org", "ideas.repec.org",
    "brookings.edu", "rand.org",
    # Institutii si repositorii nationale
    "cern.ch", "jinr.ru", "anl.gov", "lbl.gov", "bnl.gov",
    "core.ac.uk", "openaire.eu", "dart-europe.org",
    # Retele academice (academia.edu exclus — permite orice upload, produce rezultate irelevante)
    "researchgate.net", "orcid.org","scholar.google.com",
]


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _is_academic(url: str) -> bool:
    d = _domain(url)
    return any(d == a or d.endswith("." + a) for a in ACADEMIC_DOMAINS)


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    for fmt, n in [
        ("%Y-%m-%dT%H:%M:%SZ", 20),
        ("%Y-%m-%dT%H:%M:%S",  19),
        ("%Y-%m-%d",           10),
        ("%Y-%m",               7),
    ]:
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


_MONTH_PAT = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_PAT})\s+(\d{{4}})\b"),   # 15 April 2025
    re.compile(rf"\b({_MONTH_PAT})\s+(\d{{1,2}}),?\s+(\d{{4}})\b"), # April 15, 2025
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),                          # 2025-04-15
]


def _extract_date_from_content(content: str) -> Optional[str]:
    if not content:
        return None
    m = _DATE_PATTERNS[0].search(content)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {m.group(3)}", "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = _DATE_PATTERNS[1].search(content)
    if m:
        try:
            return datetime.strptime(f"{m.group(2)} {m.group(1)[:3]} {m.group(3)}", "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = _DATE_PATTERNS[2].search(content)
    if m:
        return m.group(1)
    return None


_NAME = r"[A-Z][a-z]+"
_FULL_NAME = rf"{_NAME}(?:\s+(?:[A-Z]\.?\s+)?{_NAME})+"
_AUTHOR_SEP = rf"(?:\s*,\s*|\s+and\s+)"

_AUTHOR_PATTERNS = [
    # "Authors: Name1, Name2" sau "Author: Name"
    re.compile(rf"[Aa]uthors?\s*:\s*({_FULL_NAME}(?:{_AUTHOR_SEP}{_FULL_NAME}){{0,6}})"),
    # "by Name1, Name2 and Name3" — fara IGNORECASE ca sa respecte majusculele
    re.compile(rf"\b[Bb]y\s+({_FULL_NAME}(?:{_AUTHOR_SEP}{_FULL_NAME}){{0,5}})(?=\s*[,\.\(]|\s+(?:et\s+al|published|in\s+[A-Z]|from|at\b|is\b|are\b|\d{{4}}))"),
    # "Name1, Name2 et al."
    re.compile(rf"({_FULL_NAME}(?:\s*,\s*{_FULL_NAME}){{1,5}}\s+et\s+al)"),
]


def _extract_authors(content: str) -> Optional[str]:
    if not content:
        return None
    for pat in _AUTHOR_PATTERNS:
        m = pat.search(content)
        if m:
            candidate = m.group(1).strip().rstrip(".,;")
            if 5 < len(candidate) < 200:
                return candidate
    return None


async def search_articles(
    keywords: str,
    days_back: int,
    api_key: str,
    user_question: Optional[str] = None,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Cauta cu Tavily folosind parametrul nativ `days` (filtru real de data din API).
    Face 2 treceri: domenii academice + general academic, deduplicare dupa URL.
    Exclude strict articolele mai vechi de cutoff.
    """
    client = TavilyClient(api_key=api_key)
    _tavily_calls = 0
    cutoff = datetime.now() - timedelta(days=days_back)
    today = datetime.now().strftime("%Y-%m-%d")
    month_year = (datetime.now() - timedelta(days=days_back)).strftime("%B %Y")

    seen: set = set()
    collected: List[Dict] = []

    # Daca avem user_question, adaugam o trecere suplimentara cu intrebarea completa
    queries = [
        f"{keywords} {month_year}",
        f"research study {keywords} {today[:4]}",
    ]
    if user_question and user_question.strip() != keywords.strip():
        queries.append(user_question[:300])

    for qi, query in enumerate(queries, 1):
        logger.info(f"[Tavily] Query {qi}/{len(queries)}: '{query}' | days={days_back}")

        # Trecere 1: domenii academice
        try:
            t0 = time.perf_counter()
            logger.info(f"[Tavily] [{qi}] Cautare academica...")
            r1 = await _tavily_call_with_retry(
                client,
                query=query,
                search_depth="advanced",
                include_domains=ACADEMIC_DOMAINS,
                days=days_back,
                max_results=10,
                include_answer=False,
                include_raw_content=False,
            )
            _tavily_calls += 1
            n_new = sum(1 for item in r1.get("results", []) if item.get("url", "").strip() not in seen)
            logger.info(f"[Tavily] [{qi}] Academic: {len(r1.get('results', []))} rezultate ({time.perf_counter()-t0:.1f}s) | {n_new} noi")
            for item in r1.get("results", []):
                url = item.get("url", "").strip()
                if url and url not in seen:
                    seen.add(url)
                    collected.append(item)
        except Exception as e:
            logger.warning(f"[Tavily] [{qi}] Academic search error: {e}")

        # Trecere 2: general (fara filtrare domenii)
        try:
            t0 = time.perf_counter()
            logger.info(f"[Tavily] [{qi}] Cautare generala...")
            r2 = await _tavily_call_with_retry(
                client,
                query=f"scientific paper {query}",
                search_depth="advanced",
                days=days_back,
                max_results=7,
                include_answer=False,
                include_raw_content=False,
            )
            _tavily_calls += 1
            n_new = sum(1 for item in r2.get("results", []) if item.get("url", "").strip() not in seen)
            logger.info(f"[Tavily] [{qi}] General: {len(r2.get('results', []))} rezultate ({time.perf_counter()-t0:.1f}s) | {n_new} noi")
            for item in r2.get("results", []):
                url = item.get("url", "").strip()
                if url and url not in seen:
                    seen.add(url)
                    collected.append(item)
        except Exception as e:
            logger.warning(f"[Tavily] [{qi}] General search error: {e}")

    if telemetry is not None:
        telemetry["api_calls"] = _tavily_calls
    logger.info(f"[Tavily] Total brut: {len(collected)} | incep validarea...")

    valid = []
    excluded_old = 0
    excluded_nodate = 0

    for item in collected:
        title = (item.get("title") or "").strip()
        url   = (item.get("url")   or "").strip()
        if not title or not url:
            continue

        content = (item.get("content") or "").strip()
        pub_dt = _parse_date(item.get("published_date"))

        # Daca Tavily nu a returnat data, incearcam extragerea din snippet
        if pub_dt is None:
            extracted_date_str = _extract_date_from_content(content)
            if extracted_date_str:
                pub_dt = _parse_date(extracted_date_str)

        # Daca avem data si e mai veche decat cutoff, excludem strict
        if pub_dt is not None and pub_dt < cutoff:
            logger.debug(f"[Tavily] EXCLUS (vechi {pub_dt.date()}): {title[:50]}")
            excluded_old += 1
            continue

        # Daca nu avem data, acceptam DOAR surse academice recunoscute
        if pub_dt is None and not _is_academic(url):
            logger.debug(f"[Tavily] EXCLUS (fara data + non-academic): {title[:50]}")
            excluded_nodate += 1
            continue

        authors = _extract_authors(content) or None

        valid.append({
            "title":          title,
            "url":            url,
            "authors":        authors,
            "source":         _domain(url),
            "published_date": pub_dt.strftime("%Y-%m-%d") if pub_dt else None,
            "summary":        content[:600] or None,
            "relevance_score": min(10.0, max(1.0, item.get("score", 0.5) * 10)),
            "_academic":      _is_academic(url),
            "_score":         item.get("score", 0),
            "_pub_ts":        pub_dt.timestamp() if pub_dt else 0,
        })

    if excluded_old or excluded_nodate:
        logger.info(f"[Tavily] Excluse: {excluded_old} prea vechi, {excluded_nodate} fara data/non-academic")

    # Sorteaza: academic > recent > scor
    valid.sort(key=lambda x: (x.pop("_academic"), x.pop("_pub_ts"), x.pop("_score")), reverse=True)

    logger.info(f"[Tavily] VALID: {len(valid)}")
    for i, a in enumerate(valid, 1):
        pub = a.get('published_date') or 'no-date'
        src = a.get('source') or '?'
        logger.info(f"  [{i}] {pub:10} | {src:25} | {a['title'][:55]}")

    return valid
