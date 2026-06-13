"""
Cautare articole stiintifice dupa autor via Semantic Scholar + CrossRef.
Ambele API-uri sunt gratuite si nu necesita chei API.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

SS_BASE = "https://api.semanticscholar.org/graph/v1"
CR_BASE = "https://api.crossref.org"
USER_AGENT = "AgentArticole/1.0 (mailto:agent@icsi.ro)"


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _parse_date(s) -> Optional[datetime]:
    if not s:
        return None
    text = str(s).strip()
    for length, fmt in [(10, "%Y-%m-%d"), (7, "%Y-%m"), (4, "%Y")]:
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _url_from_paper(paper: dict) -> Optional[str]:
    ext = paper.get("externalIds") or {}
    if ext.get("DOI"):
        return f"https://doi.org/{ext['DOI']}"
    if ext.get("ArXiv"):
        return f"https://arxiv.org/abs/{ext['ArXiv']}"
    if ext.get("PubMed"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{ext['PubMed']}/"
    return paper.get("url") or None


def _name_matches(search_name: str, candidate_name: str) -> bool:
    """Verifica daca toate partile numelui cautat apar in numele candidatului."""
    parts = search_name.lower().split()
    candidate = candidate_name.lower()
    return all(p in candidate for p in parts)


async def _ss_get(client: httpx.AsyncClient, url: str, params: dict) -> Optional[dict]:
    """GET catre Semantic Scholar cu retry pe 429 (max 2 incercari, backoff 5s)."""
    for attempt in range(2):
        try:
            r = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(f"[Author/SS] 429 Rate limit — astept {wait}s (incercarea {attempt+1}/2)")
                await asyncio.sleep(wait)
                continue
            logger.warning(f"[Author/SS] HTTP {r.status_code} pentru {url}")
            return None
        except Exception as e:
            logger.warning(f"[Author/SS] Request error: {e}")
            return None
    logger.warning("[Author/SS] Rate limit persistent dupa retry — renunt la Semantic Scholar")
    return None


async def _search_semantic_scholar(
    author_name: str,
    cutoff: datetime,
    client: httpx.AsyncClient,
) -> List[Dict[str, Any]]:
    # Pas 1: cauta autorul dupa nume
    data = await _ss_get(
        client,
        f"{SS_BASE}/author/search",
        {"query": author_name, "fields": "name,paperCount", "limit": 5},
    )
    if data is None:
        return []
    candidates = data.get("data", [])

    # Filtreaza candidatii care se potrivesc cu numele
    matched = [a for a in candidates if _name_matches(author_name, a.get("name", ""))]
    if not matched:
        matched = candidates[:1]  # fallback: primul rezultat
    logger.info(f"[Author/SS] Autori selectati: {[a.get('name') for a in matched]}")

    results = []
    seen: set = set()

    # Pas 2: obtine articolele fiecarui autor
    for author in matched[:3]:
        author_id = author.get("authorId")
        if not author_id:
            continue
        papers_data = await _ss_get(
            client,
            f"{SS_BASE}/author/{author_id}/papers",
            {"fields": "title,authors,year,publicationDate,externalIds,url,abstract,venue,openAccessPdf", "limit": 100},
        )
        if papers_data is None:
            continue
        papers = papers_data.get("data", [])
        logger.info(f"[Author/SS] '{author.get('name')}': {len(papers)} articole total")

        for paper in papers:
            title = (paper.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue

            pub_dt = _parse_date(paper.get("publicationDate"))
            if pub_dt is None and paper.get("year"):
                pub_dt = datetime(paper["year"], 7, 1)
            if pub_dt is None or pub_dt < cutoff:
                continue

            url = _url_from_paper(paper)
            if not url:
                continue

            seen.add(title.lower())
            authors_str = ", ".join(a.get("name", "") for a in (paper.get("authors") or []))
            abstract = (paper.get("abstract") or "")[:600]

            results.append({
                "title": title,
                "url": url,
                "authors": authors_str or None,
                "source": _domain(url) or "semanticscholar.org",
                "published_date": pub_dt.strftime("%Y-%m-%d"),
                "summary": abstract.strip() or None,
                "_api": "ss",
            })

    logger.info(f"[Author/SS] {len(results)} articole recente")
    return results


async def _search_crossref(
    author_name: str,
    cutoff: datetime,
    client: httpx.AsyncClient,
) -> List[Dict[str, Any]]:
    try:
        r = await client.get(
            f"{CR_BASE}/works",
            params={
                "query.author": author_name,
                "rows": 50,
                "sort": "published",
                "order": "desc",
                "filter": f"from-pub-date:{cutoff.strftime('%Y-%m-%d')}",
                "select": "DOI,title,author,published,published-print,published-online,abstract,container-title,URL",
            },
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code != 200:
            logger.warning(f"[Author/CR] HTTP {r.status_code}")
            return []
        items = r.json().get("message", {}).get("items", [])
        logger.info(f"[Author/CR] {len(items)} rezultate brute")
    except Exception as e:
        logger.warning(f"[Author/CR] Search error: {e}")
        return []

    results = []
    for item in items:
        titles = item.get("title") or []
        title = titles[0].strip() if titles else ""
        if not title:
            continue

        # Verifica ca autorul cautat e in lista de autori CrossRef
        cr_authors = item.get("author") or []
        if not any(
            _name_matches(author_name, f"{a.get('given', '')} {a.get('family', '')}")
            for a in cr_authors
        ):
            continue

        pub_raw = item.get("published") or item.get("published-print") or item.get("published-online")
        pub_dt = None
        if pub_raw:
            parts = pub_raw.get("date-parts", [[]])[0]
            if len(parts) >= 2:
                pub_dt = datetime(parts[0], parts[1], parts[2] if len(parts) >= 3 else 1)
            elif len(parts) == 1:
                pub_dt = datetime(parts[0], 7, 1)

        if pub_dt is None or pub_dt < cutoff:
            continue

        doi = item.get("DOI", "")
        url = f"https://doi.org/{doi}" if doi else (item.get("URL") or "")
        if not url:
            continue

        authors_str = ", ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip() for a in cr_authors
        )
        journal = (item.get("container-title") or [""])[0]
        abstract = re.sub(r"<[^>]+>", "", item.get("abstract") or "")[:600]

        results.append({
            "title": title,
            "url": url,
            "authors": authors_str or None,
            "source": journal or _domain(url),
            "published_date": pub_dt.strftime("%Y-%m-%d"),
            "summary": abstract.strip() or None,
            "_api": "cr",
        })

    logger.info(f"[Author/CR] {len(results)} articole cu autorul '{author_name}'")
    return results


async def search_articles(
    author_name: str,
    days_back: int,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Cauta articolele unui autor in Semantic Scholar si CrossRef in paralel.
    Nu necesita chei API — ambele sunt servicii publice gratuite.
    """
    cutoff = datetime.now() - timedelta(days=days_back)
    logger.info(f"[Author] Cauta articole de: '{author_name}' | cutoff={cutoff.date()}")

    async with httpx.AsyncClient(timeout=20.0) as client:
        ss_res, cr_res = await asyncio.gather(
            _search_semantic_scholar(author_name, cutoff, client),
            _search_crossref(author_name, cutoff, client),
            return_exceptions=True,
        )

    if isinstance(ss_res, Exception):
        logger.warning(f"[Author] Semantic Scholar error: {ss_res}")
        ss_res = []
    if isinstance(cr_res, Exception):
        logger.warning(f"[Author] CrossRef error: {cr_res}")
        cr_res = []

    if telemetry is not None:
        telemetry["api_calls"] = 2

    # Deduplicare dupa titlu normalizat (SS are prioritate — abstracte mai bune)
    seen: set = set()
    merged = []
    for article in list(ss_res) + list(cr_res):
        norm = article["title"].lower().strip()
        if norm not in seen:
            seen.add(norm)
            article.pop("_api", None)
            merged.append(article)

    merged.sort(key=lambda x: x.get("published_date") or "", reverse=True)

    logger.info(f"[Author] TOTAL: {len(ss_res)} SS + {len(cr_res)} CR = {len(merged)} unice")
    for i, a in enumerate(merged, 1):
        logger.info(f"  [{i}] {a.get('published_date','?')} | {a.get('source','?')[:30]} | {a['title'][:55]}")

    return merged
