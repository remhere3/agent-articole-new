"""
Cautare articole stiintifice via SearXNG (self-hosted) + Ollama pentru rezumare.
Fluxul: SearXNG executa cautarea web -> Ollama rezuma rezultatele.

Configurare: SEARXNG_BASE_URL=http://<ip-masina>:<port>
"""
import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import httpx

from app.services._utils import (
    _as_text,
    author_in_result as _author_in_result,
    describe_exc,
    domain as _domain,
    is_academic as _is_academic,
    is_retryable_http,
    looks_like_person_name as _looks_like_person_name,
    parse_date as _parse_date,
    retry_async,
    strip_watermarks as _strip_watermarks,
)

logger = logging.getLogger(__name__)


def _year_from_url(url: str) -> Optional[int]:
    """Incearca sa extraga anul publicarii din URL (ex: /2024/, /2023-, doi cu an)."""
    import re
    matches = re.findall(r'(?<!\d)(20[0-2]\d)(?!\d)', url)
    if matches:
        return max(int(y) for y in matches)
    return None


async def _lookup_date_crossref(doi: str) -> Optional[datetime]:
    """Interogheaza Crossref API pentru data publicarii unui DOI."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"https://api.crossref.org/works/{doi}",
                headers={"User-Agent": "AgentArticole/1.0 (mailto:agent@icsi.ro)"},
            )
            if r.status_code != 200:
                return None
            msg = r.json().get("message", {})
        pub = msg.get("published") or msg.get("published-print") or msg.get("published-online")
        if not pub:
            return None
        parts = pub.get("date-parts", [[]])[0]
        if len(parts) >= 3:
            return datetime(parts[0], parts[1], parts[2])
        if len(parts) == 2:
            return datetime(parts[0], parts[1], 1)
        if len(parts) == 1:
            return datetime(parts[0], 1, 1)
    except Exception:
        pass
    return None


async def _lookup_date_pubmed(pmid: str) -> Optional[datetime]:
    """Interogheaza NCBI E-utilities pentru data publicarii unui articol PubMed."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "pubmed", "id": pmid, "retmode": "json"},
            )
            if r.status_code != 200:
                return None
            data = r.json()
        result = data.get("result", {}).get(pmid, {})
        pub_date = result.get("epubdate") or result.get("pubdate") or ""
        if not pub_date:
            return None
        # Format "2025 Feb 12" sau "2025 Feb" sau "2025"
        months = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                  "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        parts = pub_date.split()
        if len(parts) >= 3:
            return datetime(int(parts[0]), months.get(parts[1], 1), int(parts[2]))
        if len(parts) == 2:
            return datetime(int(parts[0]), months.get(parts[1], 1), 1)
        if len(parts) == 1 and parts[0].isdigit():
            return datetime(int(parts[0]), 1, 1)
    except Exception:
        pass
    return None


def _arxiv_date_from_url(url: str) -> Optional[datetime]:
    """Extrage data din URL arXiv fara API.
    Format nou: arxiv.org/abs/2507.20702  -> 2025-07
    Format vechi: arxiv.org/abs/cond-mat/0309395 -> 2003-09
    """
    import re
    # Format nou: YYMM.NNNNN
    m = re.search(r'arxiv\.org/abs/(\d{4})\.\d+', url)
    if m:
        yymm = m.group(1)
        yy, mm = int(yymm[:2]), int(yymm[2:])
        year = 2000 + yy if yy <= 30 else 1900 + yy
        if 1 <= mm <= 12:
            return datetime(year, mm, 1)
    # Format vechi: category/YYMMNNN
    m = re.search(r'arxiv\.org/abs/[a-z-]+/(\d{2})(\d{2})\d+', url)
    if m:
        yy, mm = int(m.group(1)), int(m.group(2))
        year = 2000 + yy if yy <= 30 else 1900 + yy
        if 1 <= mm <= 12:
            return datetime(year, mm, 1)
    return None


async def _resolve_date(url: str) -> Optional[datetime]:
    """
    Incearca sa obtina data publicarii pentru un URL fara publishedDate in SearXNG.
    Suporta: doi.org, ncbi.nlm.nih.gov/pubmed, arxiv.org (din URL, fara API).
    """
    import re
    # arXiv — data direct din URL, fara apel API
    dt = _arxiv_date_from_url(url)
    if dt:
        return dt
    # DOI direct
    m = re.match(r'https?://(?:dx\.)?doi\.org/(10\.\S+)', url)
    if m:
        return await _lookup_date_crossref(m.group(1))
    # PubMed
    m = re.search(r'ncbi\.nlm\.nih\.gov/pubmed/(\d+)', url)
    if m:
        return await _lookup_date_pubmed(m.group(1))
    return None


async def _searxng_search(
    base_url: str,
    query: str,
    categories: str = "science",
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    """Apeleaza SearXNG JSON API si returneaza lista de rezultate brute."""
    url = f"{base_url.rstrip('/')}/search"
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": "en",
    }
    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()

    try:
        data = await retry_async(
            _do, retry_on=is_retryable_http, label=f"SearXNG '{query[:40]}'"
        )
        results = data.get("results", [])
        logger.info(f"[SearXNG] '{query[:60]}' -> {len(results)} rezultate brute")
        return results[:max_results]
    except Exception as e:
        logger.warning(f"[SearXNG] Eroare cautare: {describe_exc(e)}")
        return []


async def search_articles(
    keywords: str,
    days_back: int,
    searxng_base_url: str,
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.2",
    ollama_api_key: Optional[str] = None,
    user_question: Optional[str] = None,
    max_articles: int = 25,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    SearXNG cauta articolele, Ollama local rezuma rezultatele.

    max_articles: cate articole se trimit la Ollama pentru rezumare (cap per
    rulare). Limitat ca sa nu se faca prompt-uri prea lungi -> timeout Ollama.
    """
    query = user_question or keywords
    cutoff = datetime.now() - timedelta(days=days_back)
    logger.info(f"[SearXNG+Ollama] query='{query[:80]}' | days_back={days_back}")

    seen: set = set()
    collected: List[Dict] = []
    searxng_calls = 0
    current_year = str(datetime.now().year)

    # Imparte keywords dupa virgula si construieste query-uri scurte cu anul curent
    kw_parts = [k.strip() for k in keywords.split(",") if k.strip()]
    # Grupeaza cate 2 termeni per query, max 3 query-uri
    groups = []
    for i in range(0, min(len(kw_parts), 6), 2):
        groups.append(" ".join(kw_parts[i:i+2]))
    if not groups:
        groups = [keywords]
    queries = [f"{g} {current_year}" for g in groups[:3]]

    for qi, q in enumerate(queries, 1):
        logger.info(f"[SearXNG] Query {qi}/{len(queries)}: '{q}'")
        t0 = time.perf_counter()

        results = await _searxng_search(
            base_url=searxng_base_url,
            query=q,
            categories="science",
            max_results=20,
        )
        searxng_calls += 1
        elapsed = time.perf_counter() - t0

        n_new = 0
        for item in results:
            url = (item.get("url") or "").strip()
            if url and url not in seen:
                seen.add(url)
                collected.append(item)
                n_new += 1
        logger.info(f"[SearXNG] Query {qi}: {len(results)} rezultate in {elapsed:.1f}s | {n_new} noi")

    if telemetry is not None:
        telemetry["api_calls"] = searxng_calls

    # Cautare dupa autor: pastreaza doar rezultatele care contin numele complet
    # (prenume + nume). Altfel motorul intoarce si articole care prind doar un
    # cuvant din nume (ex. cautand "Roxana Ionete" -> articole cu doar "Roxana").
    if _looks_like_person_name(keywords):
        before = len(collected)
        collected = [it for it in collected if _author_in_result(keywords, it)]
        logger.info(
            f"[SearXNG] Cautare dupa autor '{keywords}': {len(collected)}/{before} "
            f"rezultate contin numele complet"
        )

    if not collected:
        logger.info("[SearXNG] Nu s-au gasit rezultate brute")
        return []

    # Filtrare initiala: titlu + url prezente, excludere rezultate vechi sau fara data/non-academic
    has_date: List[Dict] = []
    needs_fetch: List[Dict] = []
    excluded_old = 0
    excluded_nodate = 0
    for item in collected:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            continue
        pub_dt = _parse_date(item.get("publishedDate") or item.get("published_date"))

        # Data lipsa: incearca sa o deduca din URL
        if pub_dt is None:
            url_year = _year_from_url(url)
            if url_year:
                pub_dt = datetime(url_year, 1, 1)

        if pub_dt is not None and pub_dt < cutoff:
            excluded_old += 1
            logger.info(f"[SearXNG] EXCLUS (vechi {pub_dt.date()}): {title[:60]}")
            continue
        if pub_dt is None and not _is_academic(url):
            excluded_nodate += 1
            logger.info(f"[SearXNG] EXCLUS (fara data + non-academic): {title[:60]}")
            continue

        entry = {
            "title": title,
            "url": url,
            "source": _domain(url),
            "published_date": pub_dt.strftime("%Y-%m-%d") if pub_dt else None,
            "summary": _strip_watermarks(_as_text(item.get("content"))[:300])[:280].strip() or None,
        }
        if pub_dt is None:
            needs_fetch.append(entry)
        else:
            has_date.append(entry)

    logger.info(f"[SearXNG] Excluse: {excluded_old} prea vechi, {excluded_nodate} fara data/non-academic | {len(has_date)} cu data, {len(needs_fetch)} fara data")

    # Fetch paralel data din pagina pentru articolele fara data
    import asyncio
    if needs_fetch:
        logger.info(f"[SearXNG] Fetch data din pagina pentru {len(needs_fetch)} articole fara data...")
        tasks = [_resolve_date(a["url"]) for a in needs_fetch]
        fetched_dates = await asyncio.gather(*tasks, return_exceptions=True)
        excluded_fetch = 0
        for article, fetched_dt in zip(needs_fetch, fetched_dates):
            if isinstance(fetched_dt, Exception):
                fetched_dt = None
            if fetched_dt:
                article["published_date"] = fetched_dt.strftime("%Y-%m-%d")
                if fetched_dt < cutoff:
                    excluded_fetch += 1
                    logger.info(f"[SearXNG] EXCLUS dupa fetch (vechi {fetched_dt.date()}): {article['title'][:60]}")
                    continue
                logger.info(f"[SearXNG] Data gasita in pagina ({fetched_dt.date()}): {article['title'][:60]}")
            has_date.append(article)
        if excluded_fetch:
            logger.info(f"[SearXNG] Excluse dupa fetch pagina: {excluded_fetch}")

    pre_filtered = has_date

    if not pre_filtered:
        return []

    logger.info(f"[SearXNG] Dupa filtrare completa: {len(pre_filtered)} articole pentru Ollama")

    # Trimite maxim `max_articles` la Ollama pentru a evita timeout (configurabil)
    to_summarize = pre_filtered[:max_articles]
    logger.info(f"[SearXNG] Pasul 2 — {ollama_model} rezuma {len(to_summarize)}/{len(pre_filtered)} rezultate")
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    results_text = json.dumps(to_summarize, ensure_ascii=False, indent=2)

    prompt = f"""You are a scientific article analyst. Below are search results for: "{keywords}"
Only keep articles published after {cutoff_str}.

For each valid article, return a JSON array with fields:
title, url, authors (null if unknown), source, published_date, summary (2-3 sentences).

Search results:
{results_text}

Return ONLY valid JSON array, no other text."""

    t1 = time.perf_counter()
    enriched = await _ollama_generate(
        base_url=ollama_base_url,
        model=ollama_model,
        api_key=ollama_api_key,
        prompt=prompt,
    )
    elapsed = time.perf_counter() - t1

    if telemetry is not None:
        telemetry["api_calls"] = telemetry.get("api_calls", 0) + 1

    final = enriched if enriched else pre_filtered
    if not enriched:
        logger.warning(f"[SearXNG+Ollama] Ollama nu a putut parsa ({elapsed:.1f}s) — folosesc SearXNG raw")

    # Filtru final garantat: eliminam articole cu data cunoscuta < cutoff
    # (Ollama poate ignora instructiunea de filtrare sau poate inventa date)
    final_clean = []
    for a in final:
        pub_dt = _parse_date(a.get("published_date"))
        if pub_dt is not None and pub_dt < cutoff:
            logger.info(f"[SearXNG] POST-OLLAMA EXCLUS (vechi {pub_dt.date()}): {a.get('title','')[:60]}")
            continue
        final_clean.append(a)
    if len(final) - len(final_clean):
        logger.info(f"[SearXNG] Filtru post-Ollama: eliminat {len(final) - len(final_clean)} articole vechi")
    final = final_clean

    logger.info(f"[SearXNG+Ollama] FINAL: {len(final)} articole in {elapsed:.1f}s")
    return final


async def _ollama_generate(
    base_url: str,
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Endpoint local Ollama: POST /api/generate"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}

    async def _do() -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json().get("response", "")

    try:
        text = await retry_async(
            _do, retry_on=is_retryable_http, label="SearXNG+Ollama generate"
        )
        return _parse_json_array(text)
    except Exception as e:
        logger.warning(f"[SearXNG+Ollama] generate error: {e}")
    return []


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    import re
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass
    return []


async def check_searxng_available(base_url: str) -> bool:
    """Verifica daca SearXNG raspunde."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{base_url.rstrip('/')}/search",
                params={"q": "test", "format": "json"},
            )
            return r.status_code == 200
    except Exception:
        return False
