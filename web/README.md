# USDT Freeze Alert: React UI

Vite + React 18 + TypeScript (strict) + Tailwind v4 + shadcn/ui + Recharts v3 (via the shadcn `chart` component) + anime.js v4.
It is a drop-in replacement for the single-file dashboard rendered by `freezewatch/render.py` (`freezewatch/template.html`,
which is left untouched and keeps being produced as `site/index.html`).

## Data contract

The app is static and only reads JSON that the Python pipeline already writes into `site/`:

| file | used for |
|---|---|
| `data.json` | the exact object that is embedded in `index.html` (new export in `render.py`), loaded at start |
| `analytics.json` | Analytics tab (the app uses the copy embedded in `data.json` when present, otherwise fetches this) |
| `addresses.json`, `addr/<shard>.json` | address index, fetched lazily only for the Watchlist and the address page |
| `health.json` | Status tab |

Files are resolved relative to `DATA_BASE` (`src/lib/data.ts`): `../` in a production build (the app lives in `site/app/`,
data is one level up), `./` in `npm run dev` (fixture in `web/public/`). Override with `VITE_DATA_BASE`.

Routing is hash based (`#live`, `#analytics`, `#watchlist`, `#signers`, `#status`, `#addr=<address>`, `#watch=a,b,c`), so GitHub Pages
static hosting needs no rewrites. `vite.config.ts` uses `base: './'`.

## Develop

```bash
cd web
npm ci
# dev fixture (gitignored): needs data.json, analytics.json, addresses.json, addr/*.json, health.json in web/public/.
# From a local checkout with data/freezewatch.sqlite:  python -m freezewatch run   (writes site/), then copy site/*.json + site/addr to web/public/
# Without local data, fetch the published files (see "Dev fixture" below).
npm run dev          # http://localhost:5173/
npm run typecheck && npm run lint
npm run build        # -> ../site/app
npm run size         # bundle report
```

### Dev fixture without local data

`data.json` is the `<script id="data">` blob of the live page; the other files are published next to it:
`https://prettydeath.github.io/usdt-freeze-alert/{analytics,addresses,health}.json` and `addr/{0-9,a-f,_}.json`.
Save them into `web/public/` (they are gitignored).

## Verify

```bash
# after `npm run build`, with the fixture files also copied into ../site/ (next to app/):
pip install playwright && playwright install chromium
python scripts/verify.py        # screenshots -> web/screenshots/
```

`scripts/verify.py` serves `../site`, opens every tab at 1360 px and 375 px in dark and light, asserts 0 console errors and
no horizontal scroll, runs the watchlist with several addresses, the address page, the CSV/JSON/MD exports, the chain filter,
stale switch, theme toggle and the analytics controls.

## Layout

```
src/lib        data loading, address normalisation, live on-chain check, watchlist/status model, report builders, motion hooks
src/components shadcn/ui primitives (ui/), DataTable (CSV/JSON export + mobile card mode), address card, header, footer
src/pages      Live (+ lazy LiveReport charts), Analytics, Watchlist, AddressPage, Signers, Status
```

Motion (`src/lib/motion.ts`): every anime.js animation runs inside `createScope` (reverted on unmount) and is skipped when
`prefers-reduced-motion: reduce` is set: KPI count-up, staggered table rows and bars, `createDrawable` line draw on the area/line charts,
banner entrance, pulse on the live/status dots, `createLayout` FLIP on KPI tiles and the chain cards when the chain filter changes.

## Switch-over (not wired into CI yet)

The build goes to `site/app/`, so the old page keeps working at `/usdt-freeze-alert/` and the new one is served at
`/usdt-freeze-alert/app/` (A/B period, no URL breaks for `#addr=` / `#watch=` links of the old page).
When the new UI is ready to become the default, in this order:

1. build with `VITE_DATA_BASE=./` and `outDir` = `../site`, move the old output to `site/classic.html`
   (change `out = SITE_DIR / "index.html"` to `classic.html` in `render.py` and publish the app as `site/index.html`), or
2. keep the app in `site/app/` and replace `site/index.html` with a one-line redirect (`location.replace('app/' + location.hash)`),
   which preserves deep links.

Option 2 is the smaller change and needs no Python edit except the redirect page.

## CI

Node 22, `npm ci`, `npm run build`. The exact (unapplied) workflow changes are in [`ci.patch`](ci.patch):

* `watch.yml`: after the `Run` step (which fills `site/`) add `setup-node@v4` (22, npm cache) and a `working-directory: web` step with
  `npm ci && npm run build`. The build writes `site/app/`, which the existing "Publish to the data branch" step already copies
  together with the rest of `site/`. Hashed asset names are deterministic, so the "no changes" check still short-circuits when
  neither data nor `web/` changed.
* `ci.yml`: new `web` job (`npm ci`, `npm run lint`, `npm run build`) for pull requests.
* `pages.yml`: **no change needed**, it already uploads the whole `site/` folder of the `data` branch, which now contains `app/`.

Apply with `git apply web/ci.patch`.
