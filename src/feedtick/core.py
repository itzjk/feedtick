import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .feeds import FeedError, fetch, iso, normalize_url, parse
from .store import Store


def config(path):
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("feeds")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise ValueError("config requires 1 to 50 [[feeds]] tables")
    sources = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each feed must be a [[feeds]] table")
        url = normalize_url(row.get("url"))
        sector = row.get("sector")
        name = row.get("name", url)
        if not isinstance(sector, str) or not sector.strip() or not isinstance(name, str) or not name.strip():
            raise ValueError("each feed needs a nonempty sector and optional nonempty name")
        sources.append({"url": url, "sector": sector, "name": name})
    if len({s["url"] for s in sources}) != len(sources):
        raise ValueError("each feed URL must appear only once")
    return sorted(sources, key=lambda s: s["url"])


def check(path, sources, *, now=None, timeout=10, retries=1, recent_hours=24):
    now = now or datetime.now(timezone.utc)
    stamp = iso(now)
    store = Store(path)
    with store.transaction():
        pending = store.pending()
        if pending is not None:
            return pending
        coverage = []
        for source in sources:
            try:
                data, headers, final_url = fetch(source["url"], store.validators(source["url"]), timeout, retries)
                if data is None and not any(store.validators(source["url"]).values()):
                    raise FeedError("unexpected HTTP 304 without a cached feed")
                if data is not None:
                    entries = parse(data, final_url, now, recent_hours)
                    for item in entries:
                        store.ingest(source, item, stamp)
                    store.cache(source["url"], headers)
                coverage.append({**source, "status": "not_modified" if data is None else "ok", "error": None})
            except (FeedError, ValueError) as exc:
                coverage.append({**source, "status": "error", "error": str(exc)})
        items = store.unconfirmed([s["url"] for s in sources])
        payload = {"batch_id": uuid.uuid4().hex if items else None, "checked_at": stamp,
                   "coverage_status": "partial" if any(c["status"] == "error" for c in coverage) else "complete",
                   "coverage": coverage, "recent_hours": recent_hours, "items": items}
        if items:
            store.create_batch(payload)
        return payload


def ack(path, batch_id, *, now=None):
    store = Store(path)
    with store.transaction():
        return store.acknowledge(batch_id, iso(now or datetime.now(timezone.utc)))
