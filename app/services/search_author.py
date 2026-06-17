"""
Cautare articole stiintifice dupa autor via OpenAlex + CrossRef.
Ambele sunt complet gratuite, fara API key, fara probleme de rate limit.
OpenAlex: https://openalex.org (100k req/zi, open data)
CrossRef: https://crossref.org (open, politicos)
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import httpx

from app.services._utils import domain as _domain, parse_date as _parse_date

logger = logging.getLogger(__name__)

OA_BASE = "https://api.openalex.org"
CR_BASE = "https://api.crossref.org"
# OpenAlex recomanda email in User-Agent pentru "polite pool" (rate limit mai relaxat)
USER_AGENT = "AgentArticole/1.0 (mailto:agent@icsi.ro)"


def _name_matches(search_name: str, candidate_name: str) -> bool:
    parts = search_name.lower().split()
    candidate = candidate_name.lower()
    return all(p in candidate for p in parts)


def _best_url(work: dict) -> Optional[str]:
    """Alege cel mai bun URL dintr-un work OpenAlex."""
    if work.get("doi"):
        doi = work["doi"].replace("https://doi.org/", "")
        return f"https://doi.org/{doi}"
    oa = work.get("open_access") or {}
    if oa.get("oa_url"):
        return oa["oa_url"]
    ids = work.get("ids") or {}
    if ids.get("pmid"):
        pmid = str(ids["pmid"]).replace("https://pubmed.ncbi.nlm.nih.gov/", "").strip("/")
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    loc = work.get("primary_location") or {}
    return loc.get("landing_page_url") or None


async def _search_openalex(
    author_name: str,
    cutoff: datetime,
    client: httpx.AsyncClient,
    max_works: int = 200,
    max_profiles: int = 3,
) -> List[Dict[str, Any]]:
    """
    1. Cauta autorul in OpenAlex dupa nume → obtine author ID
    2. Obtine lucrarile autorului filtrate dupa data
    """
    # Pas 1: cauta autorul
    try:
        r = await client.get(
            f"{OA_BASE}/authors",
            params={"search": author_name, "per-page": 5},
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code != 200:
            logger.warning(f"[Author/OA] Author search HTTP {r.status_code}")
            return []
        candidates = r.json().get("results", [])
        logger.info(f"[Author/OA] {len(candidates)} candidati pentru '{author_name}'")
    except Exception as e:
        logger.warning(f"[Author/OA] Author search error: {e}")
        return []

    # Filtreaza dupa potrivire nume
    matched = [
        a for a in candidates
        if _name_matches(author_name, a.get("display_name", ""))
    ]
    if not matched:
        matched = candidates[:1]
    if not matched:
        logger.info("[Author/OA] Niciun autor gasit")
        return []

    logger.info(f"[Author/OA] Autori selectati: {[a.get('display_name') for a in matched]}")

    results = []
    seen: set = set()
    from_date = cutoff.strftime("%Y-%m-%d")

    # Pas 2: lucrarile fiecarui autor
    for author in matched[:max_profiles]:
        author_id = author.get("id", "").split("/")[-1]  # ex: A123456789
        if not author_id:
            continue
        # Paginare cu cursor: OpenAlex livreaza max 200/pagina, deci pentru
        # max_works > 200 parcurgem mai multe pagini pana epuizam rezultatele
        # sau atingem max_works. Cursor-ul incepe la "*"; next_cursor=null -> gata.
        per_page = min(max_works, 200)
        cursor: Optional[str] = "*"
        works: List[Dict[str, Any]] = []
        pages = 0
        while cursor and len(works) < max_works:
            try:
                r = await client.get(
                    f"{OA_BASE}/works",
                    params={
                        "filter": f"authorships.author.id:{author_id},from_publication_date:{from_date}",
                        "per-page": per_page,
                        "cursor": cursor,
                        "sort": "publication_date:desc",
                        "select": "id,title,authorships,publication_date,doi,open_access,ids,primary_location,abstract_inverted_index",
                    },
                    headers={"User-Agent": USER_AGENT},
                )
                if r.status_code != 200:
                    logger.warning(f"[Author/OA] Works HTTP {r.status_code}: {r.text[:200]}")
                    break
                data = r.json()
                page_results = data.get("results", [])
                works.extend(page_results)
                cursor = (data.get("meta") or {}).get("next_cursor")
                pages += 1
                if not page_results:
                    break
            except Exception as e:
                logger.warning(f"[Author/OA] Works error: {e}")
                break

        works = works[:max_works]
        logger.info(f"[Author/OA] '{author.get('display_name')}': {len(works)} lucrari recente ({pages} pagini)")

        for work in works:
            title = (work.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue

            pub_dt = _parse_date(work.get("publication_date"))
            if pub_dt is None or pub_dt < cutoff:
                continue

            url = _best_url(work)
            if not url:
                continue

            seen.add(title.lower())

            # Autori din authorships
            authorships = work.get("authorships") or []
            authors_str = ", ".join(
                a.get("author", {}).get("display_name", "")
                for a in authorships
                if a.get("author", {}).get("display_name")
            )

            # Sursa (journal/venue)
            loc = work.get("primary_location") or {}
            source = (loc.get("source") or {}).get("display_name") or _domain(url)

            # Abstract (OpenAlex il stocheaza ca inverted index)
            abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

            results.append({
                "title": title,
                "url": url,
                "authors": authors_str or None,
                "source": source,
                "published_date": pub_dt.strftime("%Y-%m-%d"),
                "summary": abstract,
                "_api": "oa",
            })

    logger.info(f"[Author/OA] {len(results)} articole recente")
    return results


def _reconstruct_abstract(inverted_index: Optional[dict]) -> Optional[str]:
    """OpenAlex stocheaza abstractul ca {cuvant: [pozitii]}. Il reconstruim."""
    if not inverted_index:
        return None
    try:
        words = [""] * (max(pos for positions in inverted_index.values() for pos in positions) + 1)
        for word, positions in inverted_index.items():
            for pos in positions:
                words[pos] = word
        return " ".join(words)[:600] or None
    except Exception:
        return None


async def _search_crossref(
    author_name: str,
    cutoff: datetime,
    client: httpx.AsyncClient,
    max_works: int = 200,
) -> List[Dict[str, Any]]:
    try:
        r = await client.get(
            f"{CR_BASE}/works",
            params={
                "query.author": author_name,
                "rows": min(max_works, 1000),  # CrossRef permite max 1000/pagina
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
    semantic_scholar_api_key: Optional[str] = None,  # pastrat pentru compatibilitate, neutilizat
    max_works: int = 200,
    max_profiles: int = 3,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Cauta articolele unui autor via OpenAlex + CrossRef in paralel.
    Fara API key, fara rate limit problematic.
    """
    cutoff = datetime.now() - timedelta(days=days_back)
    logger.info(f"[Author] '{author_name}' | cutoff={cutoff.date()} | OpenAlex + CrossRef")

    async with httpx.AsyncClient(timeout=30.0) as client:
        oa_res, cr_res = await asyncio.gather(
            _search_openalex(author_name, cutoff, client, max_works, max_profiles),
            _search_crossref(author_name, cutoff, client, max_works),
            return_exceptions=True,
        )

    if isinstance(oa_res, Exception):
        logger.warning(f"[Author] OpenAlex error: {oa_res}")
        oa_res = []
    if isinstance(cr_res, Exception):
        logger.warning(f"[Author] CrossRef error: {cr_res}")
        cr_res = []

    if telemetry is not None:
        telemetry["api_calls"] = 2

    # Deduplicare dupa titlu (OA are prioritate — abstracte complete)
    seen: set = set()
    merged = []
    for article in list(oa_res) + list(cr_res):
        norm = article["title"].lower().strip()
        if norm not in seen:
            seen.add(norm)
            article.pop("_api", None)
            merged.append(article)

    merged.sort(key=lambda x: x.get("published_date") or "", reverse=True)

    logger.info(f"[Author] TOTAL: {len(oa_res)} OA + {len(cr_res)} CR = {len(merged)} unice")
    for i, a in enumerate(merged, 1):
        logger.info(f"  [{i}] {a.get('published_date','?')} | {a.get('source','?')[:30]} | {a['title'][:55]}")

    return merged
