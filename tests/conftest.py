import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def rss(items):
    return ("<rss version='2.0'><channel><title>Test wire</title>" + "".join(items) + "</channel></rss>").encode()


def item(guid="a", link="https://example.test/a", title="Trial harvest opens", date="2026-10-28T10:00:00Z"):
    return "<item>" + (f"<guid isPermaLink='false'>{guid}</guid>" if guid else "") + f"<title>{title}</title>" + (f"<link>{link}</link>" if link else "") + (f"<pubDate>{date}</pubDate>" if date else "") + "</item>"


@pytest.fixture
def wire():
    routes = {"/rss": {"body": rss([item()])}}
    requests = []
    counts = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            counts[self.path] += 1
            requests.append((self.path, dict(self.headers)))
            route = routes.get(self.path, {"status": 404})
            if route.get("delay"):
                time.sleep(route["delay"])
            status = route.get("status", 200)
            if route.get("fail_once") and counts[self.path] == 1:
                status = 503
            if route.get("etag") and self.headers.get("If-None-Match") == route["etag"]:
                status = 304
            body = route.get("body", b"")
            self.send_response(status)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(route.get("length", len(body))))
            for key in ("ETag", "Last-Modified", "Location", "Content-Encoding"):
                if key.lower() in route:
                    self.send_header(key, route[key.lower()])
            self.end_headers()
            try:
                if status != 304:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield base, routes, requests, counts
    server.shutdown()
    server.server_close()
    thread.join()
