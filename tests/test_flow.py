import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from feedtick.core import ack, check, config
from feedtick.feeds import FeedError, MAX_BYTES, normalize_url, parse
from feedtick.output import render

from conftest import item, rss

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def sources(wire, *paths):
    return [{"url": wire[0] + path, "sector": "Grains" if path == "/rss" else "Trade", "name": path[1:]} for path in paths or ("/rss",)]


def poll(db, wire, *paths, **kwargs):
    return check(db, sources(wire, *paths), now=NOW, retries=0, **kwargs)


def test_incremental_ack_and_pending(tmp_path, wire):
    db = tmp_path / "state.db"
    first = poll(db, wire)
    wire[1]["/rss"]["body"] = rss([item(), item("b", "https://example.test/b", "New depot opens")])
    repeated = poll(db, wire)
    assert repeated == first
    assert wire[3]["/rss"] == 1
    assert ack(db, first["batch_id"], now=NOW)
    second = poll(db, wire)
    assert [i["guid"] for i in second["items"]] == ["b"]
    assert not ack(db, first["batch_id"], now=NOW)
    assert poll(db, wire) == second
    ack(db, second["batch_id"], now=NOW)
    empty = poll(db, wire)
    assert empty["items"] == [] and empty["batch_id"] is None
    assert empty["coverage_status"] == "complete"


def test_check_never_confirms(tmp_path, wire):
    db = tmp_path / "state.db"
    batch = poll(db, wire)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT confirmed FROM entries").fetchall() == [(0,)]
    assert poll(db, wire) == batch


def test_partial_coverage_is_visible(tmp_path, wire):
    wire[1]["/down"] = {"status": 503}
    result = poll(tmp_path / "s.db", wire, "/rss", "/down")
    assert len(result["items"]) == 1
    assert result["coverage_status"] == "partial"
    for mode in ("text", "markdown"):
        output = render(result, mode)
        assert "PARTIAL COVERAGE" in output and "HTTP 503" in output and "/down" in output
    assert json.loads(render(result, "json"))["coverage"][1]["status"] == "error"


def test_empty_partial_is_not_no_news(tmp_path, wire):
    wire[1]["/rss"] = {"status": 503}
    result = poll(tmp_path / "s.db", wire)
    assert result["batch_id"] is None
    assert "No new headlines from available sources." in render(result)
    wire[1]["/rss"] = {"body": rss([item()])}
    assert len(poll(tmp_path / "s.db", wire)["items"]) == 1


def test_atom_rss_and_order(tmp_path, wire):
    wire[1]["/atom"] = {"body": b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Trade wire</title><entry><id>tag:example.test,2026:b</id><title>Puerto abierto</title><link href="https://example.test/b"/><published>2026-10-25T12:00:00+02:00</published><updated>2026-10-28T12:00:00Z</updated></entry></feed>'''}
    result = poll(tmp_path / "s.db", wire, "/atom", "/rss")
    assert [i["sector"] for i in result["items"]] == ["Grains", "Trade"]
    assert result["items"][1]["published_at"] == "2026-10-25T10:00:00Z"
    assert result["items"][1]["freshness"] == "older_discovered"
    assert "Puerto abierto" in render(result)


@pytest.mark.parametrize("date,status,freshness", [
    (None, "missing", "unknown_date"), ("tomorrow", "invalid", "unknown_date"),
    ("2026-10-28T10:00:00", "invalid", "unknown_date"),
    ("2026-10-29T10:00:00Z", "future", "future_date"),
    ("Tue, 27 Oct 2026 13:00:00 GMT", "valid", "recently_published"),
    ("2026-10-20T10:00:00Z", "valid", "older_discovered")])
def test_dates(tmp_path, wire, date, status, freshness):
    wire[1]["/rss"]["body"] = rss([item(date=date)])
    result = poll(tmp_path / "s.db", wire)
    entry = result["items"][0]
    assert entry["date_status"] == status and entry["freshness"] == freshness
    assert entry["first_seen_at"] == "2026-10-28T12:00:00Z"
    assert entry["published_raw"] == date
    if status in ("missing", "invalid"):
        assert entry["published_at"] is None


def test_duplicates_and_distinct_titles(tmp_path, wire):
    wire[1]["/rss"]["body"] = rss([
        item("reused", "https://EXAMPLE.test:443/%61#one"),
        item("reused", "https://example.test/b", "Trial harvest opens"),
        item("other", "https://example.test/a#two"),
        item(None, "https://example.test/c", "Trial harvest opens")])
    db = tmp_path / "s.db"
    result = poll(db, wire)
    assert len(result["items"]) == 3
    ack(db, result["batch_id"], now=NOW)
    wire[1]["/rss"]["body"] = rss([item(None, "https://example.test/c", "Changed title")])
    assert poll(db, wire)["items"] == []


def test_reused_guid_changed_link_is_not_lost(tmp_path, wire):
    db = tmp_path / "s.db"
    first = poll(db, wire)
    ack(db, first["batch_id"], now=NOW)
    wire[1]["/rss"]["body"] = rss([item("a", "https://example.test/different")])
    assert len(poll(db, wire)["items"]) == 1


def test_guid_without_link_and_missing_identity(tmp_path, wire):
    db = tmp_path / "s.db"
    wire[1]["/rss"]["body"] = rss([item("opaque", None)])
    first = poll(db, wire)
    assert first["items"][0]["link"] is None
    ack(db, first["batch_id"], now=NOW)
    assert poll(db, wire)["items"] == []
    wire[1]["/rss"]["body"] = rss([item(None, None)])
    result = poll(db, wire)
    assert result["coverage_status"] == "partial" and result["items"] == []


def test_equivalent_links_across_sources_keep_provenance(tmp_path, wire):
    wire[1]["/other"] = {"body": rss([item("different-guid", "https://EXAMPLE.test:443/a#fragment")])}
    result = poll(tmp_path / "s.db", wire, "/rss", "/other")
    assert len(result["items"]) == 1
    assert len(result["items"][0]["sources"]) == 2


def test_304_and_last_modified(tmp_path, wire):
    wire[1]["/rss"].update(etag='"v1"', **{"last-modified": "Wed, 28 Oct 2026 10:00:00 GMT"})
    db = tmp_path / "s.db"
    first = poll(db, wire)
    ack(db, first["batch_id"], now=NOW)
    result = poll(db, wire)
    assert result["coverage"][0]["status"] == "not_modified" and result["items"] == []
    assert wire[2][-1][1]["If-None-Match"] == '"v1"'
    assert wire[2][-1][1]["If-Modified-Since"] == "Wed, 28 Oct 2026 10:00:00 GMT"


def test_timeout_and_retry(tmp_path, wire):
    wire[1]["/slow"] = {"body": rss([]), "delay": 0.3}
    result = poll(tmp_path / "s.db", wire, "/slow", timeout=0.05)
    assert result["coverage_status"] == "partial" and result["coverage"][0]["status"] == "error"
    wire[1]["/retry"] = {"body": rss([item()]), "fail_once": True}
    retry = check(tmp_path / "r.db", sources(wire, "/retry"), now=NOW, retries=1)
    assert retry["coverage_status"] == "complete" and wire[3]["/retry"] == 2


@pytest.mark.parametrize("body", [b"<rss><broken>", b"<!DOCTYPE rss [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><rss>&x;</rss>", b"<html><title>Wrong endpoint</title></html>", b"<rss>" + b"<x>" * 65 + b"</x>" * 65 + b"</rss>"])
def test_bad_xml_preserves_good_source(tmp_path, wire, body):
    wire[1]["/bad"] = {"body": body, "etag": '"bad"'}
    db = tmp_path / "s.db"
    result = poll(db, wire, "/rss", "/bad")
    assert result["coverage_status"] == "partial" and len(result["items"]) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT url FROM sources WHERE url LIKE '%/bad'").fetchall() == []


def test_response_limit(tmp_path, wire):
    wire[1]["/rss"].update(length=MAX_BYTES + 1)
    assert poll(tmp_path / "s.db", wire)["coverage_status"] == "partial"
    with pytest.raises(FeedError, match="2 MiB"):
        parse(b"x" * (MAX_BYTES + 1), wire[0], NOW, 24)


def test_truncated_response_and_unexpected_304(tmp_path, wire):
    wire[1]["/rss"]["length"] = len(wire[1]["/rss"]["body"]) + 10
    assert poll(tmp_path / "truncated.db", wire)["coverage_status"] == "partial"
    wire[1]["/rss"] = {"status": 304}
    result = poll(tmp_path / "uncached.db", wire)
    assert result["coverage_status"] == "partial"
    assert "without a cached feed" in result["coverage"][0]["error"]


def test_redirects_fetch_feeds_not_articles(tmp_path, wire):
    wire[1]["/redirect"] = {"status": 302, "location": wire[0] + "/rss"}
    result = poll(tmp_path / "s.db", wire, "/redirect")
    assert len(result["items"]) == 1
    assert [r[0] for r in wire[2]] == ["/redirect", "/rss"]


def test_atom_updated_is_not_publication_time(tmp_path, wire):
    wire[1]["/rss"] = {"body": b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Wire</title><entry><id>tag:example.test,a</id><title>A new trial</title><updated>2026-10-28T10:00:00Z</updated></entry></feed>'}
    entry = poll(tmp_path / "s.db", wire)["items"][0]
    assert entry["published_at"] is None and entry["date_status"] == "missing"


@pytest.mark.parametrize("body", [b'<!DOCTYPE rss><rss version="2.0"><channel/></rss>', b'<rss version="2.0"><channel>' + b'<x/>' * 30001 + b'</channel></rss>'])
def test_xml_declarations_and_node_limit(tmp_path, wire, body):
    wire[1]["/rss"] = {"body": body}
    assert poll(tmp_path / "s.db", wire)["coverage_status"] == "partial"


@pytest.mark.parametrize("text", ['feeds = ["bad"]', '[[feeds]]\nurl="file:///bad"\nsector="A"', '[[feeds]]\nurl="https://example.test/rss"', '[[feeds]]\nurl="https://example.test/rss"\nsector="A"\n[[feeds]]\nurl="https://EXAMPLE.test:443/rss#dup"\nsector="B"'])
def test_bad_config_fails_clearly(tmp_path, text):
    path = tmp_path / "feeds.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        config(path)


def test_outputs_escape_untrusted_data_and_keep_dates(tmp_path, wire):
    wire[1]["/rss"]["body"] = rss([item(title="[click](javascript:bad) &lt;img src=x&gt;")])
    result = poll(tmp_path / "s.db", wire)
    result["items"][0]["title"] += "\x1b[31m"
    text = render(result)
    md = render(result, "markdown")
    assert "\x1b" not in text and "\x1b" not in md
    assert "\\[click\\]" in md
    for mode in ("text", "markdown", "json"):
        output = render(result, mode)
        assert "https://example.test/a" in output
        assert "2026-10-28T10:00:00Z" in output and "2026-10-28T12:00:00Z" in output


def test_concurrent_checks_and_stale_ack(tmp_path, wire):
    db = tmp_path / "s.db"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: poll(db, wire), range(2)))
    assert results[0] == results[1]
    batch_id = results[0]["batch_id"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: ack(db, batch_id, now=NOW), range(2))) == [False, True]
    wire[1]["/rss"]["body"] = rss([item("b", "https://example.test/b")])
    next_batch = poll(db, wire)
    assert not ack(db, batch_id, now=NOW)
    assert poll(db, wire) == next_batch
    with pytest.raises(ValueError, match="unknown"):
        ack(db, "not-a-batch", now=NOW)


@pytest.mark.parametrize("phase", ["before", "after"])
def test_process_interruption_recovery(tmp_path, wire, phase):
    db = tmp_path / "s.db"
    script = '''import os,sys,json
from datetime import datetime,timezone
from feedtick.core import check
from feedtick.store import Store
phase,db,source=sys.argv[1:]
if phase == "before":
    original=Store.create_batch
    def interrupted(self,payload):
        original(self,payload)
        os._exit(91)
    Store.create_batch=interrupted
result=check(db,json.loads(source),now=datetime(2026,10,28,12,tzinfo=timezone.utc),retries=0)
os._exit(92)
'''
    result = subprocess.run([sys.executable, "-c", script, phase, str(db), json.dumps(sources(wire))], timeout=10)
    assert result.returncode == (91 if phase == "before" else 92)
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT count(*) FROM batches").fetchone()[0]
        assert count == (0 if phase == "before" else 1)
        assert conn.execute("SELECT count(*) FROM entries WHERE confirmed=1").fetchone()[0] == 0
    recovered = poll(db, wire)
    assert len(recovered["items"]) == 1
    assert poll(db, wire) == recovered
    ack(db, recovered["batch_id"], now=NOW)
    assert poll(db, wire)["items"] == []


def test_cli_and_configuration(tmp_path, wire):
    config_file = tmp_path / "feeds.toml"
    config_file.write_text(f'[[feeds]]\nurl = "{wire[0]}/rss"\nsector = "Granos"\nname = "Wire"\n')
    db = tmp_path / "s.db"
    command = [sys.executable, "-m", "feedtick.cli"]
    result = subprocess.run(command + ["check", str(config_file), "--state", str(db), "--json"], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["items"][0]["sector"] == "Granos"
    result = subprocess.run(command + ["ack", payload["batch_id"], "--state", str(db)], capture_output=True, timeout=10)
    assert result.returncode == 0
    wire[1]["/rss"] = {"status": 503}
    result = subprocess.run(command + ["check", str(config_file), "--state", str(db), "--retries", "0", "--markdown"], text=True, capture_output=True, timeout=10)
    assert result.returncode == 2 and "PARTIAL COVERAGE" in result.stdout
    result = subprocess.run(command + ["ack", "unknown", "--state", str(db)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 1 and "unknown batch" in result.stderr


@pytest.mark.parametrize("args", [["ack"], ["check", "feeds.toml", "--retries", "9"], ["check", "feeds.toml", "--timeout", "0"]])
def test_usage_errors_do_not_look_like_partial_coverage(args):
    result = subprocess.run([sys.executable, "-m", "feedtick.cli", *args], text=True, capture_output=True, timeout=10)
    assert result.returncode == 1 and "error:" in result.stderr


@pytest.mark.parametrize("url,expected", [("https://EXAMPLE.test:443/%7e/a#x", "https://example.test/~/a"), ("http://example.test:80", "http://example.test/"), ("https://example.test/a?x=1&amp;y=2", "https://example.test/a?x=1&amp;y=2")])
def test_url_normalization(url, expected):
    assert normalize_url(url) == expected


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "https://u:p@example.test/", "https://example.test/a\nb"])
def test_unsafe_urls(url):
    with pytest.raises(FeedError):
        normalize_url(url)
