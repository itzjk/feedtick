import re
import socket
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import feedparser
from defusedxml import ElementTree

MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 30000
MAX_DEPTH = 64


class FeedError(ValueError):
    pass


def visible(value):
    return "".join(c if not unicodedata.category(c).startswith("C") else " " for c in str(value))


def normalize_url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise FeedError("URL must be an HTTP(S) URL without whitespace")
    try:
        p = urlsplit(value)
        if p.scheme.lower() not in ("http", "https") or not p.hostname or p.username or p.password:
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii").lower()
        host = f"[{host}]" if ":" in host else host
        port = p.port
        if port and (p.scheme.lower(), port) not in (("http", 80), ("https", 443)):
            host += f":{port}"
        def percent(match):
            char = chr(int(match.group()[1:], 16))
            return char if char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~" else match.group().upper()
        path = re.sub(r"%[0-9a-fA-F]{2}", percent, p.path or "/")
        query = re.sub(r"%[0-9a-fA-F]{2}", percent, p.query)
        return urlunsplit((p.scheme.lower(), host, quote(path, safe="/%:@!$&'()*+,;=-._~"), quote(query, safe="%/?@:!$&'()*+,;=-._~"), ""))
    except (ValueError, UnicodeError) as exc:
        raise FeedError("URL must be an HTTP(S) URL without credentials") from exc


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, normalize_url(newurl))


def fetch(url, validators, timeout=10.0, retries=1):
    headers = {"User-Agent": "feedtick/0.1 (+https://github.com/itzjk/feedtick)", "Accept-Encoding": "identity"}
    headers.update({k: v for k, v in validators.items() if v})
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            with build_opener(Redirects()).open(Request(url, headers=headers), timeout=timeout) as response:
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise FeedError("compressed response rejected; server must honor identity encoding")
                expected = int(response.headers["Content-Length"]) if "Content-Length" in response.headers else None
                if expected is not None and (expected < 0 or expected > MAX_BYTES):
                    raise FeedError("feed exceeds 2 MiB response limit")
                chunks = []
                size = 0
                while True:
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FeedError("feed exceeds 2 MiB response limit")
                    if time.monotonic() - started > timeout:
                        raise TimeoutError("feed download deadline exceeded")
                    if not chunk:
                        break
                    chunks.append(chunk)
                if expected is not None and size != expected:
                    raise OSError("incomplete HTTP response")
                return b"".join(chunks), dict(response.headers), response.geturl()
        except HTTPError as exc:
            if exc.code == 304:
                return None, dict(exc.headers), url
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                raise FeedError(f"HTTP {exc.code}") from exc
            exc.close()
        except (URLError, TimeoutError, socket.timeout, OSError, HTTPException) as exc:
            if attempt == retries:
                raise FeedError(f"request failed: {visible(exc)}") from exc
        if attempt < retries:
            time.sleep(0.1 * (attempt + 1))


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def publication(entry, now, recent_hours):
    raw = entry.get("published")
    if not raw:
        return None, None, "missing", "unknown_date"
    try:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        dt = dt.astimezone(timezone.utc)
        if dt > now:
            return raw, iso(dt), "future", "future_date"
        return raw, iso(dt), "valid", "recently_published" if dt >= now - timedelta(hours=recent_hours) else "older_discovered"
    except (ValueError, TypeError, OverflowError):
        return raw, None, "invalid", "unknown_date"


def parse(data, base_url, now, recent_hours):
    if len(data) > MAX_BYTES:
        raise FeedError("feed exceeds 2 MiB response limit")
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        stack = [(root, 1)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > MAX_NODES or depth > MAX_DEPTH:
                raise FeedError("XML node or nesting limit exceeded")
            stack.extend((child, depth + 1) for child in node)
    except Exception as exc:
        raise FeedError(f"unsafe or malformed XML: {visible(exc)}") from exc
    parsed = feedparser.parse(data, response_headers={"content-location": base_url, "content-type": "application/xml"})
    if not parsed.version or parsed.bozo:
        raise FeedError("malformed or unsupported RSS/Atom feed")
    if len(parsed.entries) > 5000:
        raise FeedError("feed exceeds 5000 entries")
    entries = []
    guids = defaultdict(set)
    for entry in parsed.entries:
        raw_link = next((v.href for v in entry.get("links", []) if v.get("rel", "alternate") == "alternate" and v.get("href")), None)
        link = normalize_url(urljoin(base_url, raw_link)) if raw_link else None
        guid = entry.get("id") or None
        if not guid and not link:
            raise FeedError("entry has neither a stable ID nor an HTTP(S) link")
        raw, published, status, freshness = publication(entry, now, recent_hours)
        item = {"guid": guid, "link": link, "title": entry.get("title", ""), "published_raw": raw,
                "published_at": published, "date_status": status, "freshness": freshness}
        entries.append(item)
        if guid:
            guids[guid].add(link)
    for item in entries:
        if item["guid"] and len(guids[item["guid"]]) > 1:
            if item["link"] is None:
                raise FeedError("reused ID without a link cannot be disambiguated")
            item["guid"] = None
    return entries
