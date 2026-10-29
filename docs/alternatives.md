# Workflow comparison

Reviewed on 2026-10-28 before implementation. This is a comparison of the documented workflows, not an exhaustive search of every reader or extension. Project pages do not all provide a publication date. Their current documentation was read on the review date; the social engagement figures in the project brief were supplied evidence, not independently recounted here.

| Workflow | Setup and output | Confirmation and freshness |
| --- | --- | --- |
| [Newsboat 2.41 manual](https://newsboat.org/releases/2.41/docs/newsboat.html) | Terminal RSS/Atom reader with feed tags, article lists, unread export, and command-line operations | Read/unread operations are documented. A named, immutable exported batch with separate first-observed timestamps and a later exact-batch acknowledgment is not documented in the reviewed flow. A custom script could add it. |
| [edsu/rss-digest](https://github.com/edsu/rss-digest) | Requires a GReader-compatible aggregator and a model; generates Markdown or HTML from a chosen hours window | Optional mark-read and history context exist. The reviewed CLI does not document replaying a frozen batch and acknowledging its ID afterward. |
| [Daily Signal Digest](https://github.com/DonghaoxiangOscar/Daily-Signal-Digest) | Fetches into local SQLite; builds a digest from messages fetched in the last 24 hours, using a local summarizer by default | Stores digest runs. The reviewed README does not document an immutable pending batch with explicit entry acknowledgment and publication/observation labels. |
| feedtick | One feed list and a local state file; original headlines, explicit sectors, text/Markdown/JSON | A check creates one batch; retries replay it; ack confirms only its entries. The next poll starts after ack. |

The cited tools cover adjacent jobs well. None of the reviewed documented flows supplied this complete case with the same setup, so construction continued. This does not establish unique invention or the absence of another implementation. Prefer Newsboat if you want an interactive reader, and an existing digest tool if you want generated summaries.

The motivating [GrainStats request](https://x.com/GrainStats/status/2115493425449677098) and [Foshograin request](https://x.com/Foshograin/status/2115580610525708445) do not validate willingness to pay or distinct audiences. The hypothesis still needs direct user testing. [Grain Central's official feed directory](https://www.graincentral.com/rss-feeds/) establishes feed availability as a feature, not continuous uptime or instant publication.
