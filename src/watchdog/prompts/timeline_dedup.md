These timeline events are all dated {{date}}. Each event below is numbered. Return `groups`: one cluster per surviving event. Each cluster is `{"keep": <index>, "duplicates": [<indices>]}` — `keep` is the single index to KEEP, and `duplicates` lists the indices of the events that are pure restatements of it and should be dropped. Every index must appear exactly once across all clusters (a unique event forms its own cluster with an empty `duplicates` list).

Only collapse **pure restatements** — events asserting the same facts in different words, where one adds nothing the other lacks. Within such a group, set `keep` to the single index with the most precise wording and list the rest in `duplicates`.

KEEP every event that contributes any material fact, detail, or distinct perspective the others do not — even when it concerns the same occurrence — as its own cluster. Two accounts of the same day from different sources (for example, opposing parties who frame or emphasize it differently) are NOT duplicates: give each its own cluster. Each kept event stays attributed to its own source, so divergent accounts sit side by side on the date.

When in doubt, keep both (separate clusters). Do not rewrite events; only choose which indices to keep and which fold into them.
