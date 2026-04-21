"""
Notificari ntfy.sh dupa fiecare rulare.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def send_run_notification(
    ntfy_url: str,
    topic_name: str,
    run_id: int,
    status: str,
    results_count: int,
    elapsed_s: float,
    subscribers: list[str],
    provider: str,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    api_calls: Optional[int] = None,
) -> None:
    """Trimite o notificare ntfy dupa finalizarea unui run."""
    if not ntfy_url:
        return

    emoji = "✅" if status == "success" else "❌"
    title = f"{emoji} Agent Articole — {topic_name}"

    lines = [f"{results_count} articole găsite în {elapsed_s:.1f}s"]

    if subscribers:
        lines.append(f"Abonați: {', '.join(subscribers)}")

    tele_parts = []
    if api_calls is not None:
        tele_parts.append(f"req: {api_calls}")
    if tokens_input is not None:
        tele_parts.append(f"in: {tokens_input:,}")
    if tokens_output is not None:
        tele_parts.append(f"out: {tokens_output:,}")
    if tele_parts:
        lines.append("Telemetrie: " + " · ".join(tele_parts))

    lines.append(f"Provider: {provider} · Run #{run_id}")

    body = "\n".join(lines)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                ntfy_url,
                content=body.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8"),
                    "Priority": "default",
                    "Tags": "mag" if status == "success" else "warning",
                },
            )
            r.raise_for_status()
        logger.info(f"[ntfy] Notificare trimisa pentru run #{run_id}")
    except Exception as e:
        logger.warning(f"[ntfy] Eroare la trimitere notificare: {e}")
