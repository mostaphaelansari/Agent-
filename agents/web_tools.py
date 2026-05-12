import logging
import os
import time

from bedrock_agentcore.tools.browser_client import BrowserClient
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_BROWSER_IDENTIFIER = "aws.browser.v1"
_SESSION_TIMEOUT_SECONDS = 600
_NAV_TIMEOUT_MS = 30000
_MAX_TEXT_BYTES = 12000


def fetch_url(url: str) -> dict:
    region = os.environ["AWS_REGION"]
    started = time.monotonic()
    client = BrowserClient(region=region)
    session_id = client.start(
        identifier=_BROWSER_IDENTIFIER,
        session_timeout_seconds=_SESSION_TIMEOUT_SECONDS,
    )
    try:
        cdp_url, cdp_headers = client.generate_ws_headers()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(endpoint_url=cdp_url, headers=cdp_headers)
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                text = (page.inner_text("body") or "").strip()
            finally:
                browser.close()
    finally:
        try:
            client.stop()
        except Exception:
            logger.warning("failed to stop browser session %s", session_id)

    truncated = len(text) > _MAX_TEXT_BYTES
    if truncated:
        text = text[:_MAX_TEXT_BYTES] + "\n...[truncated]"

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "tool=fetch_url url=%s session_id=%s duration_ms=%d text_bytes=%d truncated=%s",
        url, session_id, duration_ms, len(text), truncated,
    )
    return {"url": url, "text": text, "truncated": truncated, "duration_ms": duration_ms}
