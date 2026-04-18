import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

import requests

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

WEB_FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a URL and return its readable text content (HTML is converted to markdown). "
            "Use this to read a specific webpage, article, or documentation page. "
            "Does not execute JavaScript."
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

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_CACHE: dict = {}
_CACHE_TTL = 900  # 15 minutes

# Private/internal IP ranges to block
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
    # Block .local mDNS names and localhost
    if hostname.lower() in ("localhost",) or hostname.lower().endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_RANGES)
    except ValueError:
        pass
    # Resolve and check
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            addr_str = info[4][0]
            try:
                addr = ipaddress.ip_address(addr_str)
                if any(addr in net for net in _PRIVATE_RANGES):
                    return True
            except ValueError:
                pass
    except Exception:
        pass
    return False

def _check_url(url: str) -> str | None:
    """Return error string if URL should be blocked, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported scheme '{parsed.scheme}'. Only http/https allowed."
    host = parsed.hostname or ""
    if not host:
        return "Invalid URL: no hostname."
    if _is_private(host):
        return f"Fetching internal/private host '{host}' is not allowed."
    return None

def _html_to_markdown(html: str, base_url: str = "") -> str:
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
    # Fallback: strip tags
    return re.sub(r"<[^>]+>", " ", html)

def _extract_main_content(html: str) -> str:
    """Extract main article content using heuristics."""
    if not _BS4:
        return html
    soup = BeautifulSoup(html, "html.parser")
    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "aside",
                     "header", "form", "iframe", "noscript"]):
        tag.decompose()
    # Try common article containers
    for selector in ["article", "main", '[role="main"]',
                     ".article-body", ".post-content", ".entry-content",
                     "#content", "#main-content"]:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 200:
            return str(el)
    # Fall back to body
    body = soup.find("body")
    return str(body) if body else html

def web_fetch(url: str, max_chars: int = 8000) -> dict:
    # Check cache
    cache_key = url
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < _CACHE_TTL:
            content = entry["content"]
            return {"url": url, "content": content[:max_chars], "cached": True,
                    "chars": len(content)}

    # Validate URL
    err = _check_url(url)
    if err:
        return {"error": err, "url": url}

    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=12,
            allow_redirects=False,
        )
        # Follow redirects manually so we can check each hop
        hops = 0
        while resp.is_redirect and hops < 5:
            location = resp.headers.get("Location", "")
            if not location:
                break
            # Make absolute
            if location.startswith("/"):
                parsed = urlparse(url)
                location = f"{parsed.scheme}://{parsed.netloc}{location}"
            err = _check_url(location)
            if err:
                return {"error": f"Redirect blocked: {err}", "url": url}
            url = location
            resp = requests.get(url, headers=_HEADERS, timeout=12, allow_redirects=False)
            hops += 1

        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            main_html = _extract_main_content(resp.text)
            text = _html_to_markdown(main_html, url).strip()
        elif "json" in content_type:
            text = resp.text
        else:
            text = resp.text

        # Clean up excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        _CACHE[cache_key] = {"content": text, "ts": time.time()}
        return {"url": url, "content": text[:max_chars], "chars": len(text), "cached": False}

    except requests.exceptions.ConnectionError as e:
        return {"error": f"Connection failed: {e}", "url": url}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out after 12 seconds.", "url": url}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}: {e}", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}
