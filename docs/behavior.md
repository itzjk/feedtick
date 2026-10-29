# Identity, persistence, and safe polling

## Identity

IDs belong to a feed URL, never to all feeds globally. An RSS GUID or Atom ID is treated as reliable only with a consistent normalized link (or consistently no link). Repeated GUIDs on different links in one response fall back to those links. A GUID reused with another link in a later response also produces a separate entry. This conservative rule avoids losing news when a publisher reuses IDs, but a publisher changing both an article URL and its ID association can cause a duplicate.

An entry without an ID uses its normalized link. Without either, the source fails visibly rather than guessing from a title. Repeated identical GUIDs with no link cannot reveal whether a publisher meant two stories; they are one identity. Titles never serve as identifiers. Title and date edits update stored metadata but do not unconfirm an entry or change an existing batch.

Link normalization lowercases scheme and host, removes default ports and fragments, maps an empty path to `/`, and normalizes percent escapes for unreserved characters. Query parameters, their order, trailing slashes, HTTP versus HTTPS, and path case remain significant. Tracking parameters are not stripped. Equivalent links across feeds are one entry, with every observed source and sector in JSON. The primary sector and source are the lexically first configured source URL among its origins; text and Markdown also list other origins. No title similarity or financial relevance is inferred.

## Batches and failures

The first check includes every identifiable entry currently in the feeds. `first_seen_at` is the local UTC check time, not a source timestamp. A published date needs a timezone; an Atom updated date alone is not a publication date. Future dates retain their claimed timestamp and a warning. Recent means published within `--recent-hours` of the check, not a promise of real-time delivery. Items sort by sector, freshness label, publication timestamp, first observation, source URL, link/ID, and stored entry ID.

Each state file has a single queue. A pending batch freezes headlines, source coverage, timestamps, and provenance. Check replays it without HTTP, even with another config, until ack. News arriving then is fetched on the next check after ack; feed rotation can remove it before then. Use different state files for independent configurations or reviewers. A complete empty result has no batch to confirm. An empty partial result also has no batch and always reports incomplete coverage.

SQLite `BEGIN IMMEDIATE` serializes checks and acknowledgments, including ingestion, HTTP validators, membership, and the saved payload. Successful check never confirms entries. An interrupted transaction rolls back entries and validators together; interruption after commit recovers the identical payload on retry. Acknowledgment uses stored membership, never a time cutoff or latest-batch shortcut. Repeated ack is harmless. Unknown IDs fail. A stale ack cannot confirm a later batch. Concurrent commands wait up to 30 seconds for the writer; a busy error leaves state unchanged. Keep the database on a local filesystem.

A failed feed does not remove other results or advance its validators. Exit `2` and the coverage list identify every failed source. A partial batch remains partial when replayed: its coverage describes its original poll. Retry the failed source on the check after acknowledgment. SQLite failure aborts the transaction and returns `1`.

## Resource and export boundaries

Only configured HTTP(S) feed endpoints and their HTTP(S) redirects are requested. Credentials embedded in URLs and non-HTTP schemes are rejected. Article links are stored but never fetched. Requests use identity encoding and at most three attempts. A socket inactivity timeout and elapsed budget checked between response chunks bound downloads; a blocked platform DNS resolver can outlast that budget. Response bytes, XML nodes, nesting depth, entry count, and source count are limited. XML is checked with defusedxml before feedparser, with DTDs and entities forbidden. Malformed feeds fail as a unit.

Feeds are untrusted data. No feed text is executed. Terminal control characters are replaced for display; Markdown metacharacters and HTML are escaped. JSON retains original field values with control characters escaped. Output links are limited to HTTP(S). Configuring a private endpoint permits network access to it, so use feed lists you trust. No article bodies or feed descriptions are persisted.

## Scheduling

Use an absolute executable, config, state, and output path in your own crontab. This example polls hourly and overwrites a local output file. The output repeats until you acknowledge the batch; it does not infer delivery.

```cron
0 * * * * /path/to/feedtick check /path/to/feeds.toml --state /path/to/feedtick.sqlite3 --markdown > /path/to/latest.md 2> /path/to/feedtick.err
```

Run `feedtick ack <batch-id> --state /path/to/feedtick.sqlite3` after review. Nothing installs cron or a background service automatically.

## Recording the demo

With the project installed, VHS, ttyd, ffmpeg, and a Chromium browser available, run `vhs docs/demo.tape` from the repository root. The tape invokes `feedtick demo --pause 2`, waits for the first batch before showing the terminal, and waits for the partial-coverage result before ending. A slow start fails the tape's bounded wait instead of silently producing a truncated demo. All headlines and the HTTP server are generated locally; the temporary directory is removed afterward. `feedtick demo` runs the same scenario without pauses.
