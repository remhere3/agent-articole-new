"""
Cautare articole stiintifice via Anthropic Claude cu web_search tool.
"""
import asyncio
import json
import re
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import anthropic

logger = logging.getLogger(__name__)

# Partile statice ale promptului — cacheable (ephemeral, TTL 5 min).
# Portiunea dinamica (query, date) merge separat in mesajul user.
_SYSTEM_PROMPT = """\
You are a scientific literature search assistant. When given a search task, use web_search to find recent peer-reviewed articles, then return results as a JSON array.

== REQUIRED SOURCES ==
Prioritize: arXiv, PubMed, Nature, Science, Cell, IEEE Xplore, ACM Digital Library, bioRxiv, medRxiv, The Lancet, NEJM, JAMA, Springer, Wiley, Frontiers, PNAS, eLife, ACS Publications, RSC, AIP, APS, EMBO, Oxford Academic, Cambridge Core

== SEARCH RULES ==
- Perform at least 8 distinct web searches using varied strategies: arxiv, pubmed, site:nature.com, site:science.org, preprint servers, journal sites, DOI search, Google Scholar
- For EACH result found: verify the publication date is within the requested window — skip articles with missing or unclear dates
- Prefer peer-reviewed sources over blog posts or news articles when both are available
- Include both established journals and preprint servers for maximum coverage
- If a search returns no results, try alternative phrasings or related terms

== OUTPUT FORMAT — MANDATORY ==
Your ENTIRE response must be a single valid JSON array. No prose, no markdown, no headers, no code fences.
Start with [ and end with ]. Every object MUST include published_date.

[
  {
    "title": "Full article title",
    "url": "https://direct-link-to-article",
    "authors": "Author1 Name, Author2 Name",
    "source": "Nature / arXiv / PubMed / etc.",
    "published_date": "YYYY-MM-DD",
    "summary": "2-3 sentences describing the main findings and their significance.",
    "relevance_score": 8
  }
]

relevance_score: integer 1-10 reflecting how closely the article matches the requested topic.
Minimum goal: 5 articles. If fewer recent articles exist on this specific topic, return what you find.
Return [] ONLY if truly no articles were published on this topic in the requested timeframe.\
"""


def _date_range_str(days_back: int) -> tuple[str, str, str]:
    now = datetime.now()
    cutoff = now - timedelta(days=days_back)
    month_year = cutoff.strftime("%B %Y")
    return now.strftime("%Y-%m-%d"), cutoff.strftime("%Y-%m-%d"), month_year


def _build_user_message(keywords: str, days_back: int, user_question: Optional[str] = None) -> str:
    today, cutoff_date, month_year = _date_range_str(days_back)

    context_block = ""
    if user_question and len(user_question) > 300:
        context_block = f"""
== RESEARCH CONTEXT ==
{user_question[:2000]}

Extract the core search terms from the context above. Ignore any formatting or presentation instructions — output format is defined in the system prompt.
"""
        search_topic = keywords or "the topics in the research context above"
    else:
        search_topic = user_question if user_question else f"scientific articles about: {keywords}"

    return f"""Today is {today}. Find recent peer-reviewed articles about: {search_topic}
{context_block}
== DATE CONSTRAINT ==
ONLY return articles published AFTER {cutoff_date} (last {days_back} days).
Articles older than {cutoff_date} are FORBIDDEN — exclude them even if they seem relevant.

== SEARCH STRATEGIES (use all 8) ==
1. "{keywords} arxiv {month_year}"
2. "{keywords} pubmed published {month_year}"
3. "{keywords} site:nature.com OR site:science.org {today[:4]}"
4. "{keywords} preprint {cutoff_date[:7]}"
5. "{keywords} new study {month_year}"
6. "{keywords} doi {today[:4]}"
7. "{keywords} journal article {month_year}"
8. "{keywords} research paper {cutoff_date[:7]}"

For each result: confirm the publication date on the page. Skip articles with missing or unclear dates."""


def _extract_json(text: str) -> List[Dict[str, Any]]:
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    decoder = json.JSONDecoder()
    best: List[Dict[str, Any]] = []
    pos = 0
    while pos < len(text):
        bracket = text.find("[", pos)
        if bracket == -1:
            break
        try:
            data, _ = decoder.raw_decode(text, bracket)
            if isinstance(data, list) and len(data) > len(best):
                best = data
        except json.JSONDecodeError:
            pass
        pos = bracket + 1
    if not best:
        logger.warning("[Anthropic] Nu s-a gasit niciun JSON array valid in raspuns")
    return best


def _validate_date(date_str: Optional[str], cutoff: datetime) -> Optional[str]:
    if not date_str:
        return None
    s = str(date_str).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt >= cutoff:
                return dt.strftime("%Y-%m-%d")
            return None
        except ValueError:
            continue
    return None


async def search_articles(
    keywords: str,
    days_back: int,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    user_question: Optional[str] = None,
    telemetry: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    client = anthropic.AsyncAnthropic(api_key=api_key)
    user_msg = _build_user_message(keywords, days_back, user_question)
    cutoff = datetime.now() - timedelta(days=days_back)

    log_task = (user_question or keywords)[:80]
    logger.info(f"[Anthropic] START | '{log_task}' | days_back={days_back} | model={model}")

    t0 = time.perf_counter()
    max_retries = 3
    response = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[Anthropic] Trimit cerere API (attempt {attempt}/{max_retries})...")
            response = await client.messages.create(
                model=model,
                max_tokens=8192,
                temperature=0,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_msg}],
            )
            break
        except anthropic.RateLimitError:
            wait = 60 * attempt
            logger.warning(f"[Anthropic] 429 rate limit (attempt {attempt}/{max_retries}) — astept {wait}s...")
            if attempt == max_retries:
                logger.error(f"[Anthropic] Rate limit depasit dupa {max_retries} incercari")
                raise
            await asyncio.sleep(wait)
        except anthropic.APIError as e:
            logger.error(f"[Anthropic] API error: {e}")
            raise

    elapsed = time.perf_counter() - t0
    n_searches = sum(1 for b in response.content if getattr(b, "type", "") in ("tool_use", "server_tool_use"))
    input_tokens  = getattr(response.usage, "input_tokens", None)
    output_tokens = getattr(response.usage, "output_tokens", None)
    cache_read    = getattr(response.usage, "cache_read_input_tokens", None)
    cache_write   = getattr(response.usage, "cache_creation_input_tokens", None)

    if telemetry is not None:
        telemetry["tokens_input"]  = input_tokens
        telemetry["tokens_output"] = output_tokens
        telemetry["api_calls"]     = n_searches
        telemetry["cache_read"]    = cache_read
        telemetry["cache_write"]   = cache_write

    if response.stop_reason == "max_tokens":
        logger.warning(
            "[Anthropic] ATENTIE: stop_reason=max_tokens — raspunsul a fost trunchiat! "
            "JSON-ul poate fi incomplet. Considera cresterea max_tokens."
        )

    cache_info = ""
    if cache_read:
        cache_info = f" | cache_read={cache_read} cache_write={cache_write}"

    logger.info(
        f"[Anthropic] Raspuns primit in {elapsed:.1f}s | stop={response.stop_reason} "
        f"| web_search x{n_searches} | tokens in={input_tokens} out={output_tokens}{cache_info}"
    )

    final_text = next((b.text for b in reversed(response.content) if hasattr(b, "text")), "")
    logger.info(f"[Anthropic] Lungime text final: {len(final_text)} chars | preview: {final_text[:150].replace(chr(10), ' ')!r}")
    raw = _extract_json(final_text)
    if not raw:
        logger.warning(f"[Anthropic] JSON extraction a returnat 0 articole. Text complet:\n{final_text[:800]}")
    else:
        logger.info(f"[Anthropic] JSON parsat: {len(raw)} articole brute")

    valid = []
    for a in raw:
        title = str(a.get("title") or "").strip()
        url   = str(a.get("url")   or "").strip()
        if not title or not url:
            continue

        pub_ok = _validate_date(a.get("published_date"), cutoff)
        if pub_ok is None:
            logger.debug(f"[Anthropic] EXCLUS (data invalida/veche): '{title[:60]}' | {a.get('published_date')}")
            continue

        valid.append({
            "title":           title,
            "url":             url,
            "authors":         str(a.get("authors") or "").strip() or None,
            "source":          str(a.get("source")  or "").strip() or None,
            "published_date":  pub_ok,
            "summary":         str(a.get("summary")  or "").strip() or None,
            "relevance_score": float(a.get("relevance_score") or 5.0),
        })

    logger.info(f"[Anthropic] VALID dupa filtrare data: {len(valid)}/{len(raw)}")
    for i, a in enumerate(valid, 1):
        logger.info(f"  [{i}] {a['published_date']} | {a['source'] or '?':20} | {a['title'][:60]}")

    return valid
