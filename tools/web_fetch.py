import asyncio
import ipaddress
import re
import socket
import threading
import time
from urllib.parse import urlparse

import requests

try:
    import cloudscraper as _cs_mod
    _CLOUDSCRAPER = True
except ImportError:
    _CLOUDSCRAPER = False

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

try:
    import html2text as _h2t
    _H2T = True
except ImportError:
    _H2T = False

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    _CRAWL4AI = True
except ImportError:
    _CRAWL4AI = False

WEB_FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a URL and return its readable text content (HTML is converted to markdown). "
            "Use this to read a specific webpage, article, or documentation page. "
            "Executes JavaScript and bypasses anti-bot protection."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to fetch (https:// or http://)"},
                "max_chars": {
                    "type": "integer",
                    "default": 8000,
                    "description": "Maximum characters of content to return"
                },
            },
            "required": ["url"],
        },
    },
}

# Full browser-like headers for fallback requests path
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_CACHE: dict = {}
_CACHE_TTL = 900  # 15 minutes

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

def _is_private(hostname: str) -> bool:
    if hostname.lower() in ("localhost",) or hostname.lower().endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_RANGES)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            try:
                addr = ipaddress.ip_address(info[4][0])
                if any(addr in net for net in _PRIVATE_RANGES):
                    return True
            except ValueError:
                pass
    except Exception:
        pass
    return False

def _check_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported scheme '{parsed.scheme}'. Only http/https allowed."
    host = parsed.hostname or ""
    if not host:
        return "Invalid URL: no hostname."
    if _is_private(host):
        return f"Fetching internal/private host '{host}' is not allowed."
    return None

# ── Persistent async event loop + crawl4ai browser ───────────────────────────

_async_loop: asyncio.AbstractEventLoop | None = None
_async_thread: threading.Thread | None = None
_crawler: "AsyncWebCrawler | None" = None
_crawler_lock = threading.Lock()

def _get_async_loop() -> asyncio.AbstractEventLoop:
    global _async_loop, _async_thread
    if _async_loop is None or not _async_loop.is_running():
        _async_loop = asyncio.new_event_loop()
        _async_thread = threading.Thread(target=_async_loop.run_forever, daemon=True)
        _async_thread.start()
    return _async_loop

def _run_async(coro, timeout: int = 30):
    loop = _get_async_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)

async def _ensure_crawler() -> "AsyncWebCrawler":
    global _crawler
    if _crawler is None:
        _crawler = AsyncWebCrawler(
            config=BrowserConfig(headless=True, verbose=False)
        )
        await _crawler.start()
    return _crawler

_RUN_CFG = None

def _get_run_cfg():
    global _RUN_CFG
    if _RUN_CFG is None and _CRAWL4AI:
        _RUN_CFG = CrawlerRunConfig(word_count_threshold=5)
    return _RUN_CFG

def _crawl4ai_fetch(url: str) -> str:
    """Fetch via crawl4ai's real browser. Returns markdown text."""
    with _crawler_lock:
        crawler = _run_async(_ensure_crawler())
    result = _run_async(crawler.arun(url, config=_get_run_cfg()), timeout=30)
    if not result.success:
        raise RuntimeError(result.error_message or "crawl4ai fetch failed")
    md = result.markdown
    # StringCompatibleMarkdown: prefer raw_markdown attr, fall back to str()
    text = getattr(md, "raw_markdown", None) or str(md)
    return text

# ── Fallback: requests + cloudscraper ────────────────────────────────────────

def _html_to_markdown(html: str) -> str:
    if _H2T:
        converter = _h2t.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.ignore_tables = False
        converter.body_width = 0
        return converter.handle(html)
    if _BS4:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    return re.sub(r"<[^>]+>", " ", html)

def _extract_main_content(html: str) -> str:
    if not _BS4:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside",
                     "header", "form", "iframe", "noscript"]):
        tag.decompose()
    for selector in ["article", "main", '[role="main"]',
                     ".article-body", ".post-content", ".entry-content",
                     "#content", "#main-content"]:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 200:
            return str(el)
    body = soup.find("body")
    return str(body) if body else html

def _follow_redirects(session, initial_url: str, timeout: int) -> tuple:
    url = initial_url
    for _ in range(8):
        resp = session.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=False)
        if not resp.is_redirect:
            return url, resp
        location = resp.headers.get("Location", "")
        if not location:
            return url, resp
        if location.startswith("/"):
            parsed = urlparse(url)
            location = f"{parsed.scheme}://{parsed.netloc}{location}"
        err = _check_url(location)
        if err:
            raise ValueError(f"Redirect blocked: {err}")
        url = location
    return url, resp

def _requests_fetch(url: str, use_cloudscraper: bool) -> str:
    if use_cloudscraper and _CLOUDSCRAPER:
        session = _cs_mod.create_scraper(
            browser={"browser": "chrome", "platform": "darwin", "mobile": False}
        )
    else:
        session = requests.Session()
    _, resp = _follow_redirects(session, url, timeout=15)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "html" in content_type:
        return _html_to_markdown(_extract_main_content(resp.text))
    return resp.text

# ── Public entry point ────────────────────────────────────────────────────────

def web_fetch(url: str, max_chars: int = 8000) -> dict:
    cache_key = url
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < _CACHE_TTL:
            content = entry["content"]
            return {"url": url, "content": content[:max_chars], "cached": True, "chars": len(content)}

    err = _check_url(url)
    if err:
        return {"error": err, "url": url}

    text = None
    last_error = None

    # Primary: crawl4ai real browser
    if _CRAWL4AI:
        try:
            text = _crawl4ai_fetch(url)
        except Exception as e:
            last_error = e

    # Fallback: cloudscraper → plain requests
    if text is None:
        for use_cs in ([True, False] if _CLOUDSCRAPER else [False]):
            try:
                text = _requests_fetch(url, use_cloudscraper=use_cs)
                break
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 429:
                    retry_after = min(int(e.response.headers.get("Retry-After", 2)), 10)
                    time.sleep(retry_after)
                    try:
                        text = _requests_fetch(url, use_cloudscraper=use_cs)
                        break
                    except Exception as e2:
                        last_error = e2
                elif status in (403, 406, 503) and use_cs:
                    last_error = e
                    continue
                else:
                    return {"error": f"HTTP {status}: {e}", "url": url}
            except ValueError as e:
                return {"error": str(e), "url": url}
            except requests.exceptions.ConnectionError as e:
                return {"error": f"Connection failed: {e}", "url": url}
            except requests.exceptions.Timeout:
                return {"error": "Request timed out.", "url": url}
            except Exception as e:
                last_error = e
                continue

    if text is None:
        return {"error": f"Failed to fetch: {last_error}", "url": url}

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    _CACHE[cache_key] = {"content": text, "ts": time.time()}
    return {"url": url, "content": text[:max_chars], "chars": len(text), "cached": False}
