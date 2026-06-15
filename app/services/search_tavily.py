"""
Cautare articole stiintifice via Tavily Search API.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from tavily import TavilyClient

from app.services._utils import (
    ACADEMIC_DOMAINS,
    domain as _domain,
    is_academic as _is_academic,
    parse_date as _parse_date,
    strip_watermarks as _strip_watermarks,
)

logger = logging.getLogger(__name__)


def _arxiv_date_from_url(url: str) -> Optional[str]:
    """Extrage data din URL arXiv fara API call (e.g. arxiv.org/abs/2407.12345 → 2024-07-01)."""
    import re
    m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4})\.', url)
    if m:
        yymm = m.group(1)
        year, month = int("20" + yymm[:2]), int(yymm[2:4])
        if 1 <= month <= 12:
            return f"{year}-{month:02d}-01"
    m2 = re.search(r'arxiv\.org/(?:abs|pdf)/\w+/(\d{2})(\d{2})', url)
    if m2:
        yy, mm = int(m2.group(1)), int(m2.group(2))
        year = 2000 + yy if yy <= 30 else 1900 + yy
        if 1 <= mm <= 12:
            return f"{year}-{mm:02d}-01"
    return None


def _year_from_url(url: str) -> Optional[int]:
    """Extrage un an (20xx) din URL — heuristica rapida pentru orice sursa."""
    import re
    years = re.findall(r'(?<!\d)(20[0-2]\d)(?!\d)', url)
    if years:
        return int(years[0])
    return None


def _looks_like_person_name(text: str) -> bool:
    """Heuristica simpla: 2-4 cuvinte, fiecare capitalizat, fara cifre."""
    import re
    parts = text.strip().split()
    if not (2 <= len(parts) <= 4):
        return False
    return all(re.match(r'^[A-ZÁÉÍÓÚĂÂÎȘȚ][a-záéíóúăâîșț\-]+$', p) for p in parts)


def _author_in_result(name: str, item: Dict) -> bool:
    """Verifica daca numele complet al autorului apare ca fraza in titlu sau continut."""
    haystack = " ".join([
        item.get("title") or "",
        item.get("content") or "",
    ]).lower()
    full = name.lower()
    if full in haystack:
        return True
    # Accepta si varianta "Botoran, Oana" sau "Botoran O."
    parts = name.split()
    if len(parts) >= 2:
        last, first = parts[-1].lower(), parts[0].lower()
        if f"{last}, {first}" in haystack:
            return True
        if f"{last} {first[0]}." in haystack:
            return True
    return False


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

    is_author_search = _looks_like_person_name(keywords)
    if is_author_search:
        logger.info(f"[Tavily] Detectat cautare dupa autor: '{keywords}'")

    seen: set = set()
    collected: List[Dict] = []

    if is_author_search:
        queries = [
            f'"{keywords}" author publications',
            f'"{keywords}" research paper scientist',
        ]
    else:
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
    # Filtrare autor: elimina rezultatele care nu mentioneaza numele
    if is_author_search:
        before_author = len(collected)
        collected = [item for item in collected if _author_in_result(keywords, item)]
        excluded_author = before_author - len(collected)
        if excluded_author:
            logger.info(f"[Tavily] Filtru autor: eliminat {excluded_author} rezultate fara '{keywords}'")

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

        # Incearca sa extraga data din URL daca lipseste din metadata Tavily
        if pub_dt is None:
            # arXiv: precizie luna (cel mai bun)
            url_date = _arxiv_date_from_url(url)
            if url_date:
                pub_dt = _parse_date(url_date)
                if pub_dt:
                    logger.info(f"[Tavily] Data din URL arXiv ({pub_dt.date()}): {title[:55]}")
            # General: an din URL (heuristica, precizie an)
            if pub_dt is None:
                year = _year_from_url(url)
                if year:
                    # Folosim 1 iulie ca medie a anului — mai precis decat 1 ian
                    pub_dt = datetime(year, 7, 1)
                    logger.info(f"[Tavily] An din URL ({year}): {title[:55]}")

        # Daca avem data si e mai veche decat cutoff, excludem strict
        if pub_dt is not None and pub_dt < cutoff:
            logger.info(f"[Tavily] EXCLUS (vechi {pub_dt.date()}): {title[:60]}")
            excluded_old += 1
            continue

        # Fara data: acceptam DOAR surse academice recunoscute (Tavily days=N filtreaza la sursa)
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
