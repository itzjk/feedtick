import html
import json
import re
import shlex
from itertools import groupby

from .feeds import visible

LABELS = {"recently_published": "Recently published", "older_discovered": "Older articles discovered now",
          "unknown_date": "Publication date unknown", "future_date": "Future publication date"}


def markdown(value):
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(visible(value), quote=True))


def render(payload, mode="text", *, state=None):
    if mode == "json":
        return json.dumps(payload, ensure_ascii=True, indent=2)
    esc = markdown if mode == "markdown" else visible
    prefix = "# " if mode == "markdown" else ""
    lines = [f"{prefix}feedtick | batch {payload['batch_id'] or 'none'}",
             f"Checked: {payload['checked_at']} | coverage: {payload['coverage_status']}"]
    if payload["batch_id"]:
        lines.append("Saved batch; coverage and headlines are frozen until ack.")
    if payload["coverage_status"] == "partial":
        lines.append("PARTIAL COVERAGE: failed sources may have unseen headlines.")
        for source in payload["coverage"]:
            if source["status"] == "error":
                lines.append(f"- {esc(source['name'])} ({esc(source['url'])}): {esc(source['error'])}")
    if not payload["items"]:
        lines.append("No new headlines." if payload["coverage_status"] == "complete" else "No new headlines from available sources.")
    for sector, items in groupby(payload["items"], lambda i: i["sector"]):
        lines.extend(["", f"{'## ' if mode == 'markdown' else ''}{esc(sector)}"])
        for freshness, group in groupby(items, lambda i: i["freshness"]):
            lines.append(LABELS[freshness] + (f" (within {payload['recent_hours']:g}h of check)" if freshness == "recently_published" else "") + ":")
            for item in group:
                title = esc(item["title"]) or "Untitled"
                link = item["link"]
                if mode == "markdown" and link:
                    lines.append(f"- [{title}](<{html.escape(link, quote=True).replace('>', '%3E')}>)")
                else:
                    lines.append(f"- {title}")
                    lines.append(f"  {visible(link) if link else 'Link: missing'}")
                lines.append(f"  Source: {esc(item['source_name'])} | Feed: {esc(item['source'])}")
                date = item["published_at"] or item["date_status"]
                if item["date_status"] == "future":
                    date += " (future)"
                lines.append(f"  Published: {date} | First seen: {item['first_seen_at']}")
                if item["date_status"] == "invalid":
                    lines.append(f"  Invalid source date: {esc(item['published_raw'])}")
                if len(item["sources"]) > 1:
                    lines.append("  Also listed by: " + "; ".join(f"{esc(s['name'])} ({esc(s['source'])}, {esc(s['sector'])})" for s in item["sources"] if s["source"] != item["source"]))
    if payload["batch_id"]:
        args = ["feedtick", "ack", payload["batch_id"]]
        if state is not None:
            args.extend(["--state", str(state)])
        command = shlex.join(args)
        if mode == "markdown":
            fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", command)), default=0))
            lines.extend(["", "Confirm:", "", fence + "sh", command, fence])
        else:
            lines.extend(["", f"Confirm: {command}"])
    return "\n".join(lines)
