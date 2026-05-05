from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def parse_tradingview_symbol_from_url(chart_url: str) -> str | None:
    """Extract broker:symbol from TradingView chart URL query (e.g. FX_IDC:GBPUSD → GBPUSD)."""
    if not chart_url or not chart_url.strip():
        return None
    try:
        parsed = urlparse(chart_url.strip())
        qs = parse_qs(parsed.query)
        sym = (qs.get("symbol") or [None])[0]
        if not sym:
            return None
        sym = unquote(sym)
        if ":" in sym:
            return sym.split(":", 1)[1].upper()
        return sym.upper()
    except Exception:
        return None


def fetch_tradingview_chart_context(chart_url: str, max_body_chars: int = 4000) -> dict[str, Any]:
    """
    Best-effort fetch of public chart page for title/description.
    TradingView may block datacenter IPs or return minimal HTML; URL + query symbol still useful.
    """
    out: dict[str, Any] = {
        "url": chart_url or "",
        "normalized_symbol": parse_tradingview_symbol_from_url(chart_url or ""),
        "fetch_ok": False,
        "http_status": None,
        "og_title": "",
        "og_description": "",
        "note": "",
    }
    if not chart_url or not chart_url.strip():
        out["note"] = "no_url"
        return out

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            r = client.get(chart_url.strip())
            out["http_status"] = r.status_code
            if r.status_code >= 400:
                out["note"] = f"http_{r.status_code}"
                return out
            text = r.text
            out["fetch_ok"] = True
            chunk = text[: max_body_chars * 2]
            try:
                soup = BeautifulSoup(chunk, "lxml")
            except Exception:
                soup = BeautifulSoup(chunk, "html.parser")
            og_title = soup.find("meta", property="og:title")
            og_desc = soup.find("meta", property="og:description")
            if og_title and og_title.get("content"):
                out["og_title"] = og_title["content"].strip()[:500]
            if og_desc and og_desc.get("content"):
                out["og_description"] = og_desc["content"].strip()[:1000]
            if not out["og_title"] and soup.title and soup.title.string:
                out["og_title"] = soup.title.string.strip()[:500]
    except Exception as e:
        logger.info("TradingView fetch skipped or failed: %s", e)
        out["note"] = "fetch_error"
    return out


def format_tradingview_context_for_prompt(ctx: dict[str, Any]) -> str:
    lines = ["--- TradingView chart context ---"]
    if ctx.get("url"):
        lines.append(f"URL: {ctx['url']}")
    if ctx.get("normalized_symbol"):
        lines.append(f"Symbol from URL: {ctx['normalized_symbol']}")
    if ctx.get("og_title"):
        lines.append(f"Page title: {ctx['og_title']}")
    if ctx.get("og_description"):
        lines.append(f"Page description: {ctx['og_description']}")
    if ctx.get("note"):
        lines.append(f"Fetch note: {ctx['note']}")
        if str(ctx.get("note", "")).startswith("http_403"):
            lines.append(
                "(Normal: TradingView often blocks automated page fetch; GBPUSD from the URL query is still used.)"
            )
    lines.append("--- End TradingView context ---")
    return "\n".join(lines)
