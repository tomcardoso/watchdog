# Watchdog desktop app

An Electron app over the same Python program as the `watchdog` command. Everything the CLI does,
the app does too — by calling the same code — and it adds what a terminal can't: a document
library with page thumbnails, a PDF viewer beside the extracted facts, an entity browser, a
network graph, a visual timeline, a review queue, and a chat with Claude inside the app.

User documentation is in [docs/app.md](../docs/app.md). This page is for developers.

## How it fits together

```
┌──────────── Electron ─────────────┐      stdio, one JSON message per line      ┌──── Python ────┐
│ renderer (React)  ⇄  preload  ⇄  main │  ─────────────────────────────────────▶ │ watchdog.gui    │
│  views, design system   bridge   │ ◀──────────── responses + events ────────── │  .server        │
│                                   │                                            │   api/*.py      │
│  wdfile:// (vault files only)     │                                            │   jobs → `python -m watchdog …`
└───────────────────────────────────┘                                            └────────────────┘
```

- **Reads** (lists, notes, search, review items) run in-process in the Python server and come
  back as JSON — see [API.md](API.md), mirrored by `src/shared/api.ts`.
- **Anything that changes an investigation** runs the real CLI command as a subprocess
  (`jobs.start` for long work like `add`, `action.run` for quick ones like `projects rename`), so
  the app can never behave differently from the terminal. Progress comes back as structured
  events (`WATCHDOG_PROGRESS=1`, see `src/watchdog/progress.py`).
- **Claude sessions** (`ask`, `ask --context`, `research`) run through the Claude Agent SDK in the
  vault folder, so the vault's own `.claude/` settings and `/watchdog-*` commands apply.
- The main process finds Python in this order. A packaged app: `WATCHDOG_PYTHON`, the interpreter
  chosen in the app, **the managed engine**, then pipx's `watchdog-intel` venv, the `watchdog`
  launcher's shebang and `python3` (an external install is used only if it is at least the version
  the app bundles). Development (unpackaged, or `WATCHDOG_SRC` set): the same, with the managed
  engine last, and the repo's `src/` laid ahead of the Python on `PYTHONPATH`. The managed engine
  needs no overlay: its wheel is built from the same source.

## The managed engine

A journalist never opens a terminal. On first run the app installs Watchdog itself
(`src/main/engine.ts`, shown by `src/renderer/src/views/onboarding/`):

1. `uv python install 3.12`, `uv venv --seed` into `<userData>/engine/` (`python/`, `cache/`, `venv/`;
   `UV_PYTHON_INSTALL_DIR`, `UV_CACHE_DIR` and `UV_PYTHON_BIN_DIR` all point inside it, so nothing
   lands in the user's home).
2. Phase 1 (D272), what setup waits for: `uv pip compile` resolves every library once into
   `engine/requirements.lock`; the wheel goes in with `--no-deps`; then the light libraries from
   `python -m watchdog.gui.engine_setup core-requirements`, pinned to the lock. The backend starts
   on this with `WATCHDOG_ENGINE_PENDING=1`, which makes `jobs.start` refuse document-adding
   commands.
3. Phase 2, in the background: `uv pip install -r requirements.lock`, then
   `python -m watchdog.gui.engine_setup models` (Docling, GLiNER, the embedding model, the reranker
   and an OCR check; failures are warnings, each model is fetched again on first use). When it
   finishes the main process calls `engine.setReady` on the running backend. An unfinished phase 2
   resumes at the next launch.

`--torch-backend cpu` (Linux and Windows) takes PyTorch's CPU build: on Linux the PyPI build pulls
the CUDA stack, a 6.2 GB environment against 1.8 GB. `engine.json` has a `schema` and the `phases`
finished for its wheel; a different bundled wheel triggers an automatic update, and a record in
any other schema rebuilds the environment.

`uv` ships in the app (`scripts/fetch-uv.mjs`: pinned version, sha256 verified, into
`resources/bin/<os>-<arch>/`) and the wheel is built at packaging time (`scripts/build-wheel.mjs`,
into `resources/python-wheel/`); `npm run dist*` runs both (`predist*`). Measured on a clean Linux
x64 install (October 2026): phase 1 downloads 147 MB of wheels and a 34 MB Python (a 398 MB
environment); phase 2 0.41 GB of libraries (1.9 GB environment in all) and 4.0 GB of models.

Developer switches: `WATCHDOG_FORCE_ONBOARDING=1` (or a step id: `welcome`, `engine`, `folder`,
`provider`, `approve`, `done`) shows setup even when it is complete;
`WATCHDOG_ENGINE_SIMULATE=1` (`slow`, `fail:<step>`, `slow-phase2` for a phase 2 that takes a few
minutes, `resume` to open with phase 1 already in place; combine with commas) plays a fake install
and sign-in without downloading anything, and gates the backend as a real phase 2 does.
`node scripts/engine-cli.mjs --user-data /tmp/x --verbose` runs the real installer headless
(`--core-only` stops after phase 1, `--cancel-after <s>` interrupts it, `--home` puts the models
somewhere disposable); `npm run engine:check` checks the engine logic without the network.

## Developing

```bash
cd gui
npm install
WATCHDOG_PYTHON=~/.local/pipx/venvs/watchdog-intel/bin/python npm run dev
```

`npm run dev` hot-reloads the renderer. `npm run typecheck` checks both sides; `npm run build`
builds into `out/`.

A demo investigation (fictional, generated by the real pipeline with canned model responses) is
the fastest way to see every screen populated:

```bash
python -m watchdog.gui.demo /tmp/wd-demo --home /tmp/wd-demo-home
HOME=/tmp/wd-demo-home npm run dev
```

### Screenshots for visual checks

```bash
npm run build
xvfb-run -a node scripts/shoot.mjs --home /tmp/wd-demo-home --project port-calder-waterfront \
  --out /tmp/shots --shot home='{"view":"home"}' --shot docs='{"view":"documents"}'
```

(`xvfb-run` only on a headless Linux box.) Add `--theme dark` for the dark theme.

## Frontend conventions

- **Layout.** `src/renderer/src/views/<area>/<Name>View.tsx`, default export, one route each
  (`lib/store.ts` → `Route`). A view's own styles go in a sibling `<area>.css` imported by the
  view; class names are prefixed with the area (`.docs-grid`, `.entity-hero`).
- **Design system first.** Use `components/ui` (Button, Card, Tabs, Modal, Badge, Field, Switch,
  Segmented, Dropdown, Empty, Callout, Progress, Skeleton, Stat…) and the tokens in
  `styles/tokens.css`. Never hard-code a colour, radius or shadow: every colour the app paints is a
  variable in `tokens.css` (add one there if none fits), which is what makes dark mode work and lets
  the palette change in one place. Canvas code reads tokens with `cssVar()` from `lib/pdf.ts`.
  `npm run typecheck` runs `scripts/check-colours.mjs`, which fails on a colour literal anywhere
  else. Entity types use `lib/entityTypes` (`typeMeta(type).color`, `.icon`)
  and `components/EntityChip`.
- **Data.** `useRpc(method, params)` for reads (react-query; pass `null` params to skip),
  `call(method, params)` for one-off calls, `invalidate('vault.')` after a change. Jobs:
  `lib/jobs.ts` (`startJob`, `runAction`, `flagsFor`).
- **Navigation.** `navigate({view: 'entity', id})`. Wikilinks inside markdown are handled by
  `components/Markdown`, which resolves them with `vault.resolveLink`.
- **Every state is designed.** Loading → `Skeleton`s shaped like the content; empty → `Empty` with
  a sentence on what fills it and an action; error → `ErrorNote` with retry.
- **Writing.** The audience is working journalists. Labels and messages are serious, precise and
  plain: no exclamation marks, no hype, Canadian English ("analyze", "colour", "centre"). Say what
  happened and what to do next.
- **Safety rails stay.** The public-records acknowledgement before anything is sent to a model,
  confirmations before destructive or irreversible actions (merge, delete, purge, force), and the
  `(inferred)` / figure-check markers on facts are part of the product, not friction to remove.

## Packaging

`npm run dist` builds installers with electron-builder (`electron-builder.config.cjs`): a `.dmg` on
macOS, an NSIS installer on Windows, an AppImage and `.deb` on Linux. The `watchdog` Python package
is built into a wheel and bundled with `uv`; the app installs them into its own engine on first run (see
"The managed engine"). `npm run engine:prepare` produces both for this computer.
