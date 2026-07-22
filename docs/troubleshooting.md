# Troubleshooting

When something goes wrong, find your symptom below and follow the fix. Every command here runs in the terminal. If your problem isn't listed, see [Getting help](#getting-help) at the bottom.

## watchdog: command not found

The install didn't add `watchdog` to your path (the list of places your terminal looks for programs). Fix it with:

```bash
pipx ensurepath
```

Then close and reopen your terminal.

## "Watchdog isn't set up yet"

Watchdog is installed but hasn't been through first-time setup. Run:

```bash
watchdog setup
```

## qpdf, Ghostscript, or Tesseract missing during setup

Setup refuses to continue until its required tools are installed. Install the missing tool for your platform — the exact commands are in [Step 4 of the install guide](install.md#step-4-install-the-prerequisites) — then run setup again:

```bash
watchdog setup
```

## A file landed in _INCOMING/_FAILED/

The file couldn't be processed during `watchdog chew`. It sits in `_INCOMING/_FAILED/` alongside an explanation of what went wrong. Common causes:

- **Password-protected PDF** — remove the password and try again.
- **Corrupted file** — try re-downloading or re-exporting it.
- **Unsupported format** — check the [supported file types](vault.md#supported-file-types).

To retry, fix the problem, move the file from `_INCOMING/_FAILED/` back into `_INCOMING/` (in your file manager, or with `mv`), then run:

```bash
watchdog chew
```

## A file landed in _INCOMING/_SKIPPED/

Two things send a file here, and neither is an error:

- **It's an exact duplicate.** Watchdog fingerprints every document by its content, so a file that is byte-identical to one already ingested — even under a different name — is set aside rather than processed twice. Nothing to do.
- **No text was found.** Chewing found nothing readable in the file, even after OCR. Open the original and check it's legible; a very poor scan can produce no text at all.

## A document failed during ingest

A document whose extraction fails is logged to `.watchdog/registry/ingest.log` and set aside in `.watchdog/queue/_failed/` — the rest of the batch still completes. To retry, run these from inside the vault directory:

```bash
watchdog requeue
watchdog dig
```

`watchdog requeue` moves everything in `queue/_failed/` back into the queue.

You don't have to remember to check: a bare `watchdog dig` with nothing new to read notices a document waiting in `queue/_failed/` and offers to requeue and retry it right there, instead of just reporting an empty queue. `watchdog dig --estimate` mentions it too, without moving anything (an estimate never changes what's on disk). The same quarantine notice appears if a document needs attention when `watchdog bark`'s wrap-up (described below) finishes or is interrupted.

## Hitting rate limits

A rate limit is a cap on how much work the AI provider lets you do in a window of time. Large batches can hit it. Two levers help:

Lower how many documents are extracted at once (the default is 5):

```bash
watchdog dig --concurrency 2
```

Or set it permanently:

```bash
watchdog configure extract_concurrency 2
```

For an unattended run — overnight, say — add `--wait`. Instead of stopping when it hits a limit, dig sleeps until the limit resets and resumes on its own, repeating until the whole queue is done:

```bash
watchdog dig --wait
```

Without `--wait`, dig stops cleanly on a rate limit. Nothing is lost: every document processed so far is saved to a durable working file, so re-running `watchdog dig` picks up only what's still queued.

## Ingest interrupted after extraction

Ingest has two stages: `watchdog dig` reads each document (the slow, paid part); `watchdog bark` then writes everything to your vault in one pass and produces the briefing. If `bark` never got a chance to run — no briefing appeared, entity summaries look unfinished, or you saw a message that nothing was written yet — the batch can be completed without re-reading anything:

```bash
watchdog bark
```

This runs just the wrap-up: it writes the documents to the vault, reconciles duplicate entities, and produces the briefing. It is safe to run more than once — if the wrap-up itself hits a rate limit partway through (for example while reconciling entities), nothing is written to your vault at all, and you simply run `watchdog bark` again once the limit resets. It picks up from the saved working files each time. Re-running `watchdog dig` (or the bare guided walk) also notices an unfinished batch and asks what to do with it — see the [command reference](commands.md).

## Ingest prevents the machine from sleeping during a run

`watchdog dig` (and the deprecated `watchdog ingest`) prevents the machine from sleeping for as long as it's running — a sleep partway through a document kills whatever call was in flight outright, unlike a network blip a retry can absorb. On macOS this uses the system's own `caffeinate` utility; on Linux, `systemd-inhibit` (present wherever systemd is, which is most mainstream distros). Neither needs setup, and both release the machine the moment the run ends or is interrupted. On a Linux system without systemd, or on Windows, there's no equivalent to fall back to, so a run there is not protected against the machine sleeping.

## A lock is stuck

If a chew or ingest was interrupted, a lock file can be left behind that blocks the next run. Remove it with:

```bash
watchdog unlock <name>
```

If the lock is recent (under 30 minutes old), Watchdog warns you that the operation may still be running. Once you're sure it isn't, force the removal:

```bash
watchdog unlock <name> --force
```

You can omit the name when running from inside the vault directory.

## Skills look outdated after an upgrade

When you upgrade Watchdog (`pipx upgrade watchdog-intel`), the record skills — the document-type knowledge — update automatically, because they are read straight from the package. But each vault's Claude Code command skills (the `/watchdog-*` commands) are copied into the vault and keep their old versions. Refresh them from inside the vault:

```bash
cd ~/Investigations/your-investigation
watchdog refresh-skills
```

## A vault moved or is missing

If you've reorganized your files and Watchdog can no longer find a vault, start with a health check of every registered investigation:

```bash
watchdog doctor
```

It lists any vault whose folder is missing or broken and suggests the fix. To point the registry at a vault's new location — or to have Watchdog move the folder for you if you haven't moved it yet:

```bash
watchdog move <name> /new/path/to/parent-folder
```

If a vault folder exists on disk but Watchdog doesn't know about it at all, register it:

```bash
watchdog register /path/to/the/vault-folder
```

## Obsidian says "Vault not found"

You ran `watchdog obsidian <name>` and Obsidian popped up a "Vault not found" error. This happens because Obsidian only reads its list of vaults when it starts up, so a vault created while Obsidian was already running is invisible to it until you restart.

Quit Obsidian completely (not just close the window — use **Quit** so no Obsidian process is left running), then run the command again:

```bash
watchdog obsidian <name>
```

Newer versions of Watchdog detect this situation and tell you to restart Obsidian instead of showing the confusing error.

## Getting help

If something isn't working, open an issue at [github.com/tomcardoso/watchdog/issues](https://github.com/tomcardoso/watchdog/issues). Include:

- What you typed or did
- What you expected to happen
- What actually happened — copy and paste any error messages
- Your operating system and version

## Where next

The [command reference](commands.md) documents every command and flag mentioned here. For settings such as concurrency and models, see [Configuration](configuration.md).
