import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                url TEXT PRIMARY KEY, etag TEXT, modified TEXT
            );
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY, link TEXT UNIQUE, source TEXT NOT NULL,
                sector TEXT NOT NULL, name TEXT NOT NULL, first_seen TEXT NOT NULL,
                data TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS identities (
                source TEXT NOT NULL, guid TEXT NOT NULL, link_key TEXT NOT NULL,
                entry_id TEXT NOT NULL REFERENCES entries(id),
                PRIMARY KEY(source, guid, link_key)
            );
            CREATE TABLE IF NOT EXISTS origins (
                entry_id TEXT NOT NULL REFERENCES entries(id), source TEXT NOT NULL,
                sector TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(entry_id,source)
            );
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, acknowledged_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending ON batches((1)) WHERE acknowledged_at IS NULL;
            CREATE TABLE IF NOT EXISTS batch_entries (
                batch_id TEXT NOT NULL REFERENCES batches(id), entry_id TEXT NOT NULL REFERENCES entries(id),
                PRIMARY KEY(batch_id, entry_id)
            );
        """)

    @contextmanager
    def transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        finally:
            self.db.close()

    def pending(self):
        row = self.db.execute("SELECT payload FROM batches WHERE acknowledged_at IS NULL").fetchone()
        return json.loads(row[0]) if row else None

    def validators(self, url):
        row = self.db.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
        return {"If-None-Match": row["etag"], "If-Modified-Since": row["modified"]} if row else {}

    def cache(self, url, headers):
        headers = {k.lower(): v for k, v in headers.items()}
        self.db.execute("INSERT INTO sources VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET etag=excluded.etag,modified=excluded.modified",
                        (url, headers.get("etag"), headers.get("last-modified")))

    def ingest(self, source, item, now):
        link, guid = item["link"], item["guid"]
        row = self.db.execute("SELECT id FROM entries WHERE link=?", (link,)).fetchone() if link else None
        if row is None and guid:
            row = self.db.execute("SELECT entry_id FROM identities WHERE source=? AND guid=? AND link_key=?",
                                  (source["url"], guid, link or "")).fetchone()
        entry_id = row[0] if row else uuid.uuid4().hex
        if row:
            self.db.execute("UPDATE entries SET data=? WHERE id=?", (json.dumps(item, ensure_ascii=False), entry_id))
        else:
            self.db.execute("INSERT INTO entries(id,link,source,sector,name,first_seen,data) VALUES(?,?,?,?,?,?,?)",
                            (entry_id, link, source["url"], source["sector"], source["name"], now, json.dumps(item, ensure_ascii=False)))
        if guid:
            self.db.execute("INSERT OR IGNORE INTO identities VALUES(?,?,?,?)", (source["url"], guid, link or "", entry_id))
        self.db.execute("INSERT INTO origins VALUES(?,?,?,?) ON CONFLICT(entry_id,source) DO UPDATE SET sector=excluded.sector,name=excluded.name",
                        (entry_id, source["url"], source["sector"], source["name"]))

    def unconfirmed(self, urls):
        placeholders = ",".join("?" for _ in urls)
        rows = self.db.execute(f"SELECT DISTINCT e.* FROM entries e JOIN origins o ON e.id=o.entry_id WHERE confirmed=0 AND o.source IN ({placeholders})", urls)
        result = []
        for row in rows:
            origins = [dict(r) for r in self.db.execute("SELECT source,sector,name FROM origins WHERE entry_id=? ORDER BY source", (row["id"],))]
            primary = next(o for o in origins if o["source"] in urls)
            result.append({**json.loads(row["data"]), "id": row["id"], "source": primary["source"],
                           "sector": primary["sector"], "source_name": primary["name"], "sources": origins, "first_seen_at": row["first_seen"]})
        return sorted(result, key=lambda i: (i["sector"], i["freshness"], i["published_at"] or "", i["first_seen_at"], i["source"], i["link"] or i["guid"] or "", i["id"]))

    def create_batch(self, payload):
        self.db.execute("INSERT INTO batches(id,payload) VALUES(?,?)", (payload["batch_id"], json.dumps(payload, ensure_ascii=False)))
        self.db.executemany("INSERT INTO batch_entries VALUES(?,?)", [(payload["batch_id"], i["id"]) for i in payload["items"]])

    def acknowledge(self, batch_id, now):
        row = self.db.execute("SELECT acknowledged_at FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("unknown batch ID in this state database")
        if row[0]:
            return False
        self.db.execute("UPDATE entries SET confirmed=1 WHERE id IN (SELECT entry_id FROM batch_entries WHERE batch_id=?)", (batch_id,))
        self.db.execute("UPDATE batches SET acknowledged_at=? WHERE id=?", (now, batch_id))
        return True
