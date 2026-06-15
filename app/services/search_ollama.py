"""
Cautare articole stiintifice via Ollama (local sau cloud) cu Tavily ca tool extern.
Fluxul: Tavily executa cautarea -> Ollama rezuma rezultatele.

Ollama Cloud: seteaza OLLAMA_BASE_URL=https://api.ollama.com si OLLAMA_API_KEY=<cheie>.
Local:        OLLAMA_BASE_URL=http://localhost:11434, OLLAMA_API_KEY gol.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import httpx

from app.services._utils import (
    author_in_result as _author_in_result,
    is_retryable_http,
    looks_like_person_name as _looks_like_person_name,
    parse_date as _parse_date,
    retry_async,
)

logger = logging.getLogger(__name__)


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
        keywords=query,
        days_back=days_back,
        api_key=tavily_api_key,
        telemetry=telemetry,
    )

    if not raw_results:
        logger.info("[Ollama] Tavily nu a returnat rezultate — opresc")
        return []

    # Cautare dupa autor: pastreaza doar rezultatele care contin numele complet.
    # Detectia se face pe `keywords` (campul cu numele), nu pe `query` — query
    # poate fi user_question (o fraza intreaga) care nu arata a nume de persoana.
    if _looks_like_person_name(keywords):
        before = len(raw_results)
        raw_results = [r for r in raw_results if _author_in_result(keywords, r)]
        logger.info(
            f"[Ollama] Cautare dupa autor '{keywords}': {len(raw_results)}/{before} "
            f"rezultate contin numele complet"
        )
        if not raw_results:
            logger.info("[Ollama] Niciun rezultat cu numele complet al autorului — opresc")
            return []

    cutoff_dt = datetime.now() - timedelta(days=days_back)
    cutoff = cutoff_dt.strftime("%Y-%m-%d")

    # Limiteaza la max 12 articole pentru a evita timeout Ollama
    batch = raw_results[:12]
    if len(raw_results) > 12:
        logger.info(f"[Ollama] Limitat la 12/{len(raw_results)} articole (evita timeout)")
    logger.info(f"[Ollama] Pasul 2 — {ollama_model} rezuma {len(batch)} rezultate")

    results_text = json.dumps(batch, ensure_ascii=False, indent=2)

    prompt = f"""You are a scientific article analyst. Below are search results for: "{keywords}"
Only keep articles published after {cutoff}.

For each valid article, return a JSON array with fields:
title, url, authors, source, published_date, summary (2-3 sentences).

Search results:
{results_text}

Return ONLY valid JSON array, no other text."""

    import time as _time
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
        # Filtru final: elimina articole mai vechi decat cutoff (Ollama poate ignora instructiunea)
        final = []
        for a in enriched:
            pub_dt = _parse_date(a.get("published_date"))
            if pub_dt is not None and pub_dt < cutoff_dt:
                logger.info(f"[Ollama] EXCLUS (vechi {pub_dt.date()}): {a.get('title', '')[:60]}")
            else:
                final.append(a)
        if len(enriched) - len(final):
            logger.info(f"[Ollama] Filtru final: eliminat {len(enriched) - len(final)} articole prea vechi")
        logger.info(f"[Ollama] {len(final)} articole rezumate in {elapsed:.1f}s")
        return final

    logger.warning(f"[Ollama] Nu a putut parsa rezultate ({elapsed:.1f}s) — folosesc Tavily raw ({len(raw_results)} articole)")
    return raw_results


async def _ollama_generate(
    base_url: str,
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Endpoint local Ollama: POST /api/generate"""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}

    async def _do() -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers=_headers(api_key))
            r.raise_for_status()
            return r.json().get("response", "")

    try:
        text = await retry_async(_do, retry_on=is_retryable_http, label="Ollama generate")
        return _parse_json_array(text)
    except Exception as e:
        logger.warning(f"[Ollama] generate error: {e}")
    return []


async def _ollama_chat(
    base_url: str,
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Endpoint cloud Ollama (OpenAI-compatible): POST /v1/chat/completions"""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    async def _do() -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers=_headers(api_key))
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    try:
        text = await retry_async(_do, retry_on=is_retryable_http, label="Ollama Cloud chat")
        return _parse_json_array(text)
    except Exception as e:
        logger.warning(f"[Ollama Cloud] chat error: {e}")
    return []


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
