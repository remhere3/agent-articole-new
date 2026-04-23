"""
Cautare articole stiintifice via Ollama (local sau cloud) cu Tavily ca tool extern.
Fluxul: Tavily executa cautarea -> Ollama rezuma rezultatele.

Ollama Cloud: seteaza OLLAMA_BASE_URL=https://api.ollama.com si OLLAMA_API_KEY=<cheie>.
Local:        OLLAMA_BASE_URL=http://localhost:11434, OLLAMA_API_KEY gol.
"""
import asyncio
import json
import logging
import time as _time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import httpx

logger = logging.getLogger(__name__)


def _trim_for_ollama(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trunchiaza summary/content la 150 chars pentru a reduce input-ul trimis la Ollama."""
    trimmed = []
    for a in articles:
        t = dict(a)
        for field in ("summary", "content"):
            if t.get(field):
                t[field] = t[field][:150]
        trimmed.append(t)
    return trimmed


def _headers(api_key: Optional[str]) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _is_cloud(base_url: str) -> bool:
    return base_url.startswith("https://")


async def search_articles(
    keywords: str,
    days_back: int,
    tavily_api_key: str,
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.2",
    ollama_api_key: Optional[str] = None,
    user_question: Optional[str] = None,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Tavily cauta articolele, Ollama (local sau cloud) rezuma rezultatele.
    Modelele Ollama nu au web search nativ — Tavily este sursa de search.
    """
    query = user_question or keywords
    mode = "cloud" if _is_cloud(ollama_base_url) else "local"
    logger.info(f"[Ollama/{mode}] model={ollama_model} | query='{query[:80]}'")

    from app.services.search_tavily import search_articles as tavily_search

    logger.info("[Ollama] Pasul 1 — Tavily executa cautarea web")
    raw_results = await tavily_search(
        keywords=keywords,
        days_back=days_back,
        api_key=tavily_api_key,
        user_question=user_question or None,
        telemetry=telemetry,
    )

    if not raw_results:
        logger.info("[Ollama] Tavily nu a returnat rezultate — opresc")
        return []

    logger.info(f"[Ollama] Pasul 2 — {ollama_model} rezuma {len(raw_results)} rezultate")
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    results_text = json.dumps(_trim_for_ollama(raw_results), ensure_ascii=False, indent=2)

    n = len(raw_results)
    prompt = f"""You are a scientific article processor. You will receive {n} articles and must return ALL {n} of them.

Task: for each article, add/improve two fields:
- "summary": 2-3 sentences about the main findings (rewrite if too short, keep if already good)
- "relevance_score": integer 1-10 based on relevance to "{keywords}"

Rules:
- Return ALL {n} articles. Do NOT filter, drop, or merge any article.
- Keep all other fields (title, url, authors, source, published_date) EXACTLY as in the input.
- If authors is null or missing: set it to null. NEVER invent placeholder text like "Not listed" or "See article".
- If published_date is missing, try to infer it from the summary text; if unavailable keep null.
- Output MUST be a JSON array with exactly {n} objects.

Articles to process:
{results_text}

Return ONLY the JSON array starting with [ and ending with ]. No prose, no markdown."""

    t1 = _time.perf_counter()
    ollama_calls = 1
    if _is_cloud(ollama_base_url):
        logger.info(f"[Ollama] Trimit prompt catre Ollama Cloud ({ollama_model})...")
        enriched = await _ollama_chat(
            base_url=ollama_base_url,
            model=ollama_model,
            api_key=ollama_api_key,
            prompt=prompt,
        )
    else:
        logger.info(f"[Ollama] Trimit prompt catre Ollama local ({ollama_model})...")
        enriched = await _ollama_generate(
            base_url=ollama_base_url,
            model=ollama_model,
            api_key=ollama_api_key,
            prompt=prompt,
        )
    elapsed = _time.perf_counter() - t1
    if telemetry is not None:
        telemetry["api_calls"] = telemetry.get("api_calls", 0) + ollama_calls

    if enriched:
        logger.info(f"[Ollama] {len(enriched)}/{len(raw_results)} articole procesate in {elapsed:.1f}s")
        # Daca Ollama a returnat mai putine decat a primit, completam cu cele lipsa din Tavily
        if len(enriched) < len(raw_results):
            enriched_urls = {a.get("url", "") for a in enriched}
            missing = [a for a in raw_results if a.get("url", "") not in enriched_urls]
            if missing:
                logger.info(f"[Ollama] Completez cu {len(missing)} articole Tavily omise de model")
                enriched.extend(missing)
        return enriched

    logger.warning(f"[Ollama] Nu a putut parsa rezultate ({elapsed:.1f}s) — folosesc Tavily raw ({len(raw_results)} articole)")
    return raw_results


async def _ollama_generate(
    base_url: str,
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Endpoint local Ollama: POST /api/generate — cu retry si backoff exponential."""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    max_retries = 3
    waits = [2, 4]
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(url, json=payload, headers=_headers(api_key))
                r.raise_for_status()
                text = r.json().get("response", "")
            results = [
                a for a in _parse_json_array(text)
                if a.get("title") and a.get("url")
            ]
            for item in results:
                item.setdefault("relevance_score", 5.0)
                _sanitize_article(item)
            return results
        except httpx.HTTPError as e:
            logger.warning(f"[Ollama] generate httpx error (attempt {attempt}/{max_retries}): {e}")
        except Exception as e:
            logger.warning(f"[Ollama] generate error (attempt {attempt}/{max_retries}): {e}")
        if attempt < max_retries:
            wait = waits[attempt - 1]
            logger.info(f"[Ollama] retry {attempt}/{max_retries} — astept {wait}s...")
            await asyncio.sleep(wait)
    return []


async def _ollama_chat(
    base_url: str,
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Endpoint cloud Ollama (OpenAI-compatible): POST /v1/chat/completions — cu retry si backoff."""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    max_retries = 3
    waits = [2, 4]
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(url, json=payload, headers=_headers(api_key))
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
            results = [
                a for a in _parse_json_array(text)
                if a.get("title") and a.get("url")
            ]
            for item in results:
                item.setdefault("relevance_score", 5.0)
                _sanitize_article(item)
            return results
        except httpx.HTTPError as e:
            logger.warning(f"[Ollama Cloud] chat httpx error (attempt {attempt}/{max_retries}): {e}")
        except Exception as e:
            logger.warning(f"[Ollama Cloud] chat error (attempt {attempt}/{max_retries}): {e}")
        if attempt < max_retries:
            wait = waits[attempt - 1]
            logger.info(f"[Ollama Cloud] retry {attempt}/{max_retries} — astept {wait}s...")
            await asyncio.sleep(wait)
    return []


_AUTHOR_PLACEHOLDERS = {
    "not individually listed in search snippet",
    "not listed", "not available", "not provided",
    "see article", "see source", "unknown", "n/a", "na",
    "various authors", "multiple authors",
}


def _sanitize_article(a: dict) -> dict:
    """Curata campurile generate de model care contin text placeholder."""
    authors = a.get("authors")
    if isinstance(authors, str) and authors.strip().lower() in _AUTHOR_PLACEHOLDERS:
        a["authors"] = None
    return a


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    import re
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")
    # incearca direct ca array
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    # incearca ca obiect cu cheie array
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass
    return []


async def check_ollama_available(base_url: str, model: str, api_key: Optional[str] = None) -> bool:
    """Verifica daca Ollama (local sau cloud) ruleaza si modelul exista."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if _is_cloud(base_url):
                r = await client.get(
                    f"{base_url.rstrip('/')}/v1/models",
                    headers=_headers(api_key),
                )
                r.raise_for_status()
                models = [m["id"] for m in r.json().get("data", [])]
            else:
                r = await client.get(f"{base_url.rstrip('/')}/api/tags")
                r.raise_for_status()
                models = [m["name"] for m in r.json().get("models", [])]
            return any(model in m or m.startswith(model.split(":")[0]) for m in models)
    except Exception:
        return False
