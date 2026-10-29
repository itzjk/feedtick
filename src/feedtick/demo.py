import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import ack, check, config
from .feeds import iso
from .output import render


def run(pause=0):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = {"new": False, "down": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/trade" and state["down"]:
                self.send_error(503)
                return
            items = ""
            if self.path == "/grain":
                items = f"<item><guid isPermaLink='false'>harvest</guid><title>Trial harvest opens</title><link>https://example.test/harvest</link><pubDate>{iso(now - timedelta(hours=48))}</pubDate></item>"
                if state["new"]:
                    items += f"<item><guid isPermaLink='false'>depot</guid><title>East depot reopens</title><link>https://example.test/depot</link><pubDate>{iso(now)}</pubDate></item>"
            body = f"<rss version='2.0'><channel><title>Demo wire</title>{items}</channel></rss>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory(prefix="feedtick-demo-") as directory:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            root = Path(directory)
            url = f"http://127.0.0.1:{server.server_port}"
            feeds = root / "feeds.toml"
            feeds.write_text(f'[[feeds]]\nurl = "{url}/grain"\nsector = "Grains"\nname = "Demo grain wire"\n\n[[feeds]]\nurl = "{url}/trade"\nsector = "Trade"\nname = "Demo trade wire"\n')
            sources = config(feeds)
            db = root / "state.sqlite3"
            print("$ feedtick check feeds.toml", flush=True)
            first = check(db, sources, now=now, retries=0)
            print(render(first), flush=True)
            time.sleep(pause)
            print(f"\n$ feedtick ack {first['batch_id']}", flush=True)
            ack(db, first["batch_id"], now=now)
            print("Confirmed. Adding one headline to the local RSS server.", flush=True)
            state["new"] = True
            time.sleep(pause)
            print("\n$ feedtick check feeds.toml", flush=True)
            second = check(db, sources, now=now, retries=0)
            print(render(second), flush=True)
            assert len(second["items"]) == 1 and second["items"][0]["guid"] == "depot"
            time.sleep(pause)
            ack(db, second["batch_id"], now=now)
            print("\nConfirmed second batch. Taking the trade feed offline.", flush=True)
            state["down"] = True
            print("$ feedtick check feeds.toml", flush=True)
            partial = check(db, sources, now=now, retries=0)
            print(render(partial), flush=True)
            assert partial["coverage_status"] == "partial" and not partial["items"]
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
