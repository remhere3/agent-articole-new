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
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(str(s).strip()[:len(fmt)], fmt)
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
            logger.debug(f"[Tavily] EXCLUS (vechi {pub_dt.date()}): {title[:50]}")
            excluded_old += 1
            continue

        # Daca nu avem data, acceptam DOAR surse academice recunoscute
        if pub_dt is None and not _is_academic(url):
            logger.debug(f"[Tavily] EXCLUS (fara data + non-academic): {title[:50]}")
            excluded_nodate += 1
            continue

        valid.append({
            "title":          title,
            "url":            url,
            "authors":        None,
            "source":         _domain(url),
            "published_date": pub_dt.strftime("%Y-%m-%d") if pub_dt else None,
            "summary":        (item.get("content") or "")[:600].strip() or None,
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
