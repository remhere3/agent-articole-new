"""
Cautare articole stiintifice via Tavily Search API.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from tavily import TavilyClient

logger = logging.getLogger(__name__)


def _strip_watermarks(text: str) -> str:
    import re
    return re.sub(
        r'Authorized licensed use limited to:[^.]+\.\s*Downloaded on[^.]+\.\s*(?:UTC\s*)?(?:from[^.]+\.)?\s*Restrictions apply\.?',
        '', text, flags=re.IGNORECASE
    ).strip()


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
    candidates = [
        (20, "%Y-%m-%dT%H:%M:%SZ"),
        (19, "%Y-%m-%dT%H:%M:%S"),
        (10, "%Y-%m-%d"),
        (7,  "%Y-%m"),
    ]
    text = str(s).strip()
    for length, fmt in candidates:
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


async def search_articles(
    keywords: str,
    days_back: int,
    api_key: str,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Cauta cu Tavily folosind parametrul nativ `days` (filtru real de data din API).
    Face 2 treceri: domenii academice + general academic, deduplicare dupa URL.
    Exclude strict articolele mai vechi de cutoff.
    """
    import asyncio
    client = TavilyClient(api_key=api_key)
    _tavily_calls = 0
    cutoff = datetime.now() - timedelta(days=days_back)
    today = datetime.now().strftime("%Y-%m-%d")
    month_year = (datetime.now() - timedelta(days=days_back)).strftime("%B %Y")

    seen: set = set()
    collected: List[Dict] = []

    queries = [
        f"{keywords} {month_year}",
        f"research study {keywords} {today[:4]}",
    ]

    for qi, query in enumerate(queries, 1):
        logger.info(f"[Tavily] Query {qi}/{len(queries)}: '{query}' | days={days_back}")

        # Trecere 1: domenii academice
        try:
            t0 = time.perf_counter()
            logger.info(f"[Tavily] [{qi}] Cautare academica...")
            r1 = await asyncio.to_thread(
                client.search,
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
            r2 = await asyncio.to_thread(
                client.search,
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

        pub_dt = _parse_date(item.get("published_date"))

        # Daca avem data si e mai veche decat cutoff, excludem strict
        if pub_dt is not None and pub_dt < cutoff:
            logger.info(f"[Tavily] EXCLUS (vechi {pub_dt.date()}): {title[:60]}")
            excluded_old += 1
            continue

        # Daca nu avem data, acceptam DOAR surse academice recunoscute
        if pub_dt is None and not _is_academic(url):
            logger.info(f"[Tavily] EXCLUS (fara data + non-academic): {title[:60]}")
            excluded_nodate += 1
            continue

        summary = _strip_watermarks((item.get("content") or "")[:600]).strip() or None

        valid.append({
            "title":          title,
            "url":            url,
            "authors":        None,
            "source":         _domain(url),
            "published_date": pub_dt.strftime("%Y-%m-%d") if pub_dt else None,
            "summary":        summary,
            "_academic":      _is_academic(url),
            "_score":         item.get("score", 0),
            "_pub_ts":        pub_dt.timestamp() if pub_dt else 0,
        })

    logger.info(f"[Tavily] Excluse: {excluded_old} prea vechi, {excluded_nodate} fara data/non-academic | raman {len(valid)}")

    # Sorteaza: academic > recent > scor
    valid.sort(key=lambda x: (x.pop("_academic"), x.pop("_pub_ts"), x.pop("_score")), reverse=True)

    # Filtru final garantat: Tavily poate returna ocazional articole vechi cu date incorecte
    before = len(valid)
    valid = [a for a in valid if _parse_date(a.get("published_date")) is None
             or _parse_date(a.get("published_date")) >= cutoff]
    if before - len(valid):
        logger.info(f"[Tavily] Filtru final: eliminat {before - len(valid)} articole cu date incorecte")

    logger.info(f"[Tavily] VALID: {len(valid)}")
    for i, a in enumerate(valid, 1):
        pub = a.get('published_date') or 'no-date'
        src = a.get('source') or '?'
        logger.info(f"  [{i}] {pub:10} | {src:25} | {a['title'][:55]}")

    return valid
