# USDT Freeze Alert: web UI

Vite + React 18 + TypeScript (strict) + Tailwind v4 + shadcn/ui + Recharts v3 (via the shadcn `chart` component) + anime.js v4.
It is the root page of the static site. The previous single-file dashboard (`freezewatch/template.html`) is still rendered by
`render.py`, now as `site/classic.html`.

## Data contract

The app is static and only reads JSON that the Python pipeline writes into `site/`:

| file | used for |
|---|---|
| `data.json` | the exact object that used to be embedded in the page (`render.py` `build_payload()`), loaded at start |
| `analytics.json` | Analytics tab (the copy embedded in `data.json` is used when present, otherwise this is fetched) |
| `addresses.json`, `addr/<shard>.json` | address index, fetched lazily, only for the Watchlist and the address page |
| `health.json` | Status tab |

Files are resolved relative to the page (`DATA_BASE` in `src/lib/data.ts`, override with `VITE_DATA_BASE`). Routing is hash based:
`#live`, `#analytics`, `#watchlist`, `#signers`, `#status`, `#addr=<address>`, `#watch=a,b,c`. These are the same links the classic page used, so old
deep links keep working at the site root, and the same `ufa.*` localStorage keys are used (saved watchlist, theme, tab, filters).

New fields are optional in `src/lib/types.ts` and their blocks render only when present (hop-ahead watch, exit / outcome classes, sweepers,
exposure, detection lag, `data.flights`).

## Develop

```bash
cd web
npm ci
python scripts/fixture.py     # downloads the published data files into web/public/ (gitignored) and builds ../site for the checks below
npm run dev                   # http://localhost:5173/  (serves the fixture from web/public/)
npm run typecheck && npm run lint
npm run build                 # -> ../site/index.html + ../site/assets  (old hashed assets are removed first)
npm run size                  # bundle report
```

With a local `data/freezewatch.sqlite`, `python -m freezewatch run` writes the real files into `site/`; then `npm run build` adds the app.
`python scripts/fixture.py --offline` only copies what is already in `web/public/`.

## Verify

```bash
pip install playwright pillow && playwright install chromium
python scripts/verify.py        # every tab at 1360 and 375 px, dark and light, 0 console errors, no horizontal scroll, watchlist, address page,
                                # exports, filters, theme, classic.html and old deep links; screenshots -> web/screenshots/
python scripts/verify_live.py   # needs network: real "Check live on-chain" for frozen and clean addresses, plus prefers-reduced-motion
```

README images (`../assets/*.png`, `demo.gif`) are produced from the built app by `python tools/make_assets.py`.

## Layout

```
src/lib        data loading, address normalisation, live on-chain check, watchlist/status model, report builders, motion hooks
src/components shadcn/ui primitives (ui/), DataTable (CSV/JSON export + mobile card mode), address card, header, footer, flights block
src/pages      Live (+ lazy LiveReport charts), Analytics, Watchlist, AddressPage, Signers, Status
```

Motion (`src/lib/motion.ts`): every anime.js animation runs inside `createScope` (reverted on unmount) and is skipped when
`prefers-reduced-motion: reduce` is set: KPI count-up, staggered table rows and bars, `createDrawable` line draw on the area/line charts,
banner entrance, pulse on the live/status dots, `createLayout` FLIP on KPI tiles and the chain cards when the chain filter changes.

## Deployment (site root)

`npm run build` writes `index.html` and `assets/` straight into `site/`, next to what Python writes there (`classic.html`, `data.json`,
`analytics.json`, `addresses.json`, `addr/`, `health.json`, `badges/`). `render.py` never overwrites a React `index.html`; without the Node build it writes the classic
page as `index.html` too, so a plain `python -m freezewatch run` still yields a working site. `classic.html` is linked from the footer.

## CI

Node 22, `npm ci`, `npm run build`. The exact (unapplied) workflow changes are in [`ci.patch`](ci.patch) (`git apply web/ci.patch`; checked to apply cleanly):

* `watch.yml`: `setup-node@v4` (22, npm cache) and a `working-directory: web` step with `npm ci && npm run build`, placed **before** the Python `Run` step so a Node problem fails
  fast and the Python run keeps its full 7-minute deadline. The build takes about a minute on a cold cache (a few seconds of Vite plus `npm ci`). The existing "Publish to
  the data branch" step copies `site/` including the app; hashed asset names are deterministic, so the "no changes" short-circuit still works.
* `ci.yml`: new `web` job (`npm ci`, `npm run lint`, `npm run build`) for pushes and pull requests.
* `pages.yml`: no change, it uploads the whole `site/` folder of the `data` branch.
