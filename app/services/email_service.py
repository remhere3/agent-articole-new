"""
Serviciu de trimitere email-uri cu rapoarte de articole.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo

_BUCHAREST = ZoneInfo("Europe/Bucharest")

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def _build_html_report(
    topic_name: str,
    keywords: Optional[str],
    days_back: int,
    articles: list,
    run_id: int,
    user_question: Optional[str] = None,
    telemetry: Optional[Dict[str, Any]] = None,
) -> str:
    article_rows = ""
    if not articles:
        article_rows = "<p><em>Nu au fost gasite articole noi in aceasta perioada.</em></p>"
    else:
        for a in articles:
            pub = a.get("published_date") or "N/A"
            authors = a.get("authors") or "N/A"
            source = a.get("source") or "N/A"
            summary = a.get("summary") or ""
            article_rows += f"""
            <div style="border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin-bottom:16px;">
              <h3 style="margin:0 0 6px 0;font-size:16px;">
                <a href="{a['url']}" style="color:#1a73e8;text-decoration:none;">{a['title']}</a>
              </h3>
              <p style="margin:2px 0;color:#666;font-size:13px;">
                <strong>Sursa:</strong> {source} &nbsp;|&nbsp;
                <strong>Data:</strong> {pub} &nbsp;|&nbsp;
                <strong>Autori:</strong> {authors}
              </p>
              <p style="margin:8px 0 0 0;color:#333;font-size:14px;">{summary}</p>
            </div>"""

    # Sectiunea telemetrie
    t = telemetry or {}
    provider    = t.get("provider", "—")
    model       = t.get("model", "—")
    web_search  = t.get("web_search", "—")
    elapsed_s   = t.get("elapsed_s")
    elapsed_str = f"{elapsed_s:.1f}s" if elapsed_s is not None else "—"
    found_total = t.get("found_total", len(articles))
    excluded    = t.get("excluded", 0)

    provider_color = {"anthropic": "#0044aa", "tavily": "#c45c00", "ollama": "#1a6b4a"}.get(provider, "#555")

    cost_usd = t.get("estimated_cost_usd")
    cost_row = ""
    if cost_usd is not None:
        cost_row = f"""
        <tr>
          <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Cost estimat</td>
          <td style="padding:5px 10px;font-size:13px;font-family:monospace;">${cost_usd:.4f} USD</td>
        </tr>"""

    question_row = ""
    if user_question:
        question_row = f"""
        <tr>
          <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Intrebare agent</td>
          <td style="padding:5px 10px;font-size:13px;font-style:italic;">„{user_question}"</td>
        </tr>"""

    keywords_row = ""
    if keywords:
        keywords_row = f"""
        <tr>
          <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Cuvinte cheie</td>
          <td style="padding:5px 10px;font-size:13px;font-family:monospace;">{keywords}</td>
        </tr>"""

    telemetry_block = f"""
  <div style="background:#f0f4ff;border:1px solid #c8d4f0;border-top:none;padding:14px 16px;">
    <p style="margin:0 0 8px 0;font-size:12px;font-weight:bold;color:#555;letter-spacing:.5px;text-transform:uppercase;">Telemetrie cautare</p>
    <table style="border-collapse:collapse;width:100%;">
      <tr>
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Provider</td>
        <td style="padding:5px 10px;font-size:13px;">
          <span style="background:{provider_color}22;color:{provider_color};border-radius:3px;padding:2px 8px;font-weight:600;font-family:monospace;">{provider}</span>
        </td>
      </tr>
      <tr style="background:#fff;">
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Model</td>
        <td style="padding:5px 10px;font-size:13px;font-family:monospace;">{model}</td>
      </tr>
      <tr>
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Web search</td>
        <td style="padding:5px 10px;font-size:13px;font-family:monospace;">{web_search}</td>
      </tr>{question_row}{keywords_row}
      <tr style="background:#fff;">
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Perioada</td>
        <td style="padding:5px 10px;font-size:13px;">ultimele {days_back} zile</td>
      </tr>
      <tr>
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Rezultate brute</td>
        <td style="padding:5px 10px;font-size:13px;">{found_total} gasite &nbsp;·&nbsp; {excluded} excluse (data veche/lipsa) &nbsp;·&nbsp; <strong>{len(articles)} valide</strong></td>
      </tr>
      <tr style="background:#fff;">
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Durata cautare</td>
        <td style="padding:5px 10px;font-size:13px;">{elapsed_str}</td>
      </tr>
      {cost_row}
      <tr>
        <td style="padding:5px 10px;color:#555;font-size:13px;white-space:nowrap;">Run ID</td>
        <td style="padding:5px 10px;font-size:13px;font-family:monospace;">#{run_id} &nbsp;·&nbsp; {datetime.now(_BUCHAREST).strftime("%d.%m.%Y %H:%M")}</td>
      </tr>
    </table>
  </div>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:20px;color:#333;">
  <div style="background:#1a1a2e;color:white;padding:20px;border-radius:8px 8px 0 0;">
    <h1 style="margin:0;font-size:22px;">Agent Articole — Raport</h1>
    <p style="margin:6px 0 0 0;opacity:0.75;font-size:14px;">
      {topic_name}
    </p>
  </div>
  {telemetry_block}
  <div style="padding:16px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px;">
    <p style="margin:0 0 12px 0;font-size:14px;color:#555;">
      <strong>{len(articles)}</strong> articole gasite in ultimele <strong>{days_back}</strong> zile
    </p>
    {article_rows}
  </div>
  <p style="color:#aaa;font-size:11px;text-align:center;margin-top:16px;">
    Agent Articole · run #{run_id} · {datetime.now(_BUCHAREST).strftime("%d.%m.%Y %H:%M")}
  </p>
</body>
</html>"""


async def send_report(
    to_addresses: List[str],
    topic_name: str,
    keywords: Optional[str],
    days_back: int,
    articles: list,
    run_id: int,
    user_question: Optional[str] = None,
    telemetry: Optional[Dict[str, Any]] = None,
) -> bool:
    """Trimite raportul HTML catre lista de adrese email."""
    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP credentials not configured — skipping email send")
        return False

    if not to_addresses:
        return False

    html_content = _build_html_report(
        topic_name=topic_name,
        keywords=keywords,
        days_back=days_back,
        articles=articles,
        run_id=run_id,
        user_question=user_question,
        telemetry=telemetry,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Agent Articole] {topic_name} — {len(articles)} articole noi"
    msg["From"] = settings.email_from
    msg["To"] = ", ".join(to_addresses)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )
        logger.info(f"[Email] Trimis catre {to_addresses} | topic='{topic_name}' | {len(articles)} articole")
        return True
    except Exception as e:
        logger.error(f"Email send error: {e}")
        return False
