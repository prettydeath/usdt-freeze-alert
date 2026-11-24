# Changelog

## Unreleased

- Analytics period selector: **Today (Kyiv)** (since 00:00 Europe/Kyiv, DST-aware) and **7d** (last 7 Kyiv days) before 30d / 90d / 12m / All; KPIs, lifecycle, chart (hourly / daily bars), by-chain table, top lists, supply, outflows, reasons / OFAC, cross-chain lag and the pulse heatmap follow it. `analytics.json` carries per-day (14 days) and per-hour (48 h) cells; the selection is remembered in the browser.
- Daily Telegram report at 09:00 Europe/Kyiv about the previous Kyiv day (once per day, catches up after a missed run, never for older days); `python -m freezewatch daily-report --date YYYY-MM-DD [--send]`; Status shows when it was last sent.

- New dashboard UI (`web/`): React 18 + TypeScript + Tailwind + shadcn/ui + Recharts + anime.js, same features and hash links (`#live`, `#analytics`, `#watchlist`, `#signers`, `#status`, `#addr=`, `#watch=`), code-split, reduced-motion aware. It is the site root; the previous page is `classic.html`. `site/data.json` (the former embedded blob) is exported for it.

- TRON: every TronGrid call goes through one client (key rotation, per-key throttle ~14 req/s, 429 cooldown, bounded, per-key usage counts in the log and on Status); Tronscan token-transfer API as a keyless fallback; recent-first inflow / outflow scans run in parallel.
- Cross-chain signals: a failed chain check is retried on the next run instead of skipped; FREEZE INCOMING alerts go out right after the core sync, TRON first; Status shows how long after submission new TRON proposals are detected.
- Hop-ahead watch: recipients of funds that left a freeze target (hop 1 and 2) are polled; a move before the address's own proposal raises a PRE-PROPOSAL FLIGHT alert.
- Third-party pulls (caller is not the frozen address) and sweeper candidates; allowance / Permit2 / EIP-7702 exposure of pending targets (address page and alert); exit classes (exchange, bridge, DEX, mixer, verified evasion address, unlabelled); clean / racing / partial / weak outcome per freeze with lead times and priority-fee anomalies; service-fee collectors and dust filtered out of repeat-recipient statistics.
- `config/evasion_watchlist.yaml`: verified addresses with an evidence field.

## v2.0.1

- Run deadline: one monotonic deadline per `run` (`FREEZEWATCH_RUN_DEADLINE_SEC`, default 420 s). Core proposal / freeze sync of every chain runs first; balances, inflows, outflows, signals, labels and history back-fills get what is left (a 60 s reserve is kept for render and publish) and stop inside their loops.
- Hard HTTP timeouts (5 s connect, 20 s read), bounded retries and a wall-clock cap per call; no request starts once an optional step is over its budget.
- Outflows: at most 60 s per address per run, with a saved cursor (block ranges / TronGrid fingerprint) so huge wallets finish over several runs.
- NEAR: bounded listing and per-run cap on detail fetches (a rate-limited nearblocks held a run for tens of minutes); Ethereum re-reads stale proposals only every 6 hours.
- Labels: streamed, time-bounded download (skipped for the run when slow); the cache lives outside `data/`.
- Status tab shows per-step timings of the last run; `watch.yml` job timeout is 12 minutes (45 for manual back-fills).

## v2.0.0

**Coverage**
- Track Tether freeze proposals on 30+ chains: multisig queues (ETH, TRON, AVAX, Celo, Kaia, Kava, NEAR, Aptos, TON), Safe API queues (12 USDT0 chains) and executed-only networks.
- USDT0 token events (`BlockPlaced`, `BlockReleased`, `DestroyedBlockedFunds`) are the source of truth for executed actions; events without a public Safe proposal are marked "no public proposal".
- Arbitrum era split: freezes before the 2025-01-28 hand-over belong to Tether's Gnosis MSW ("Arbitrum USDT"), later ones to the USDT0 Safe.
- Optimism and X Layer events via a resumable `eth_getLogs` scan; new live checks for NEAR, Aptos and TON.
- TRON ingestion is on-chain first (every id up to `transactionCount`, open ids refreshed every run); NEAR queue history works again after nearblocks dropped call arguments.

**Analytics**
- Frozen / burned / still frozen / unfrozen amounts, "sent after freeze" with the part later burned, period and token filters, sinks (zero address, token contracts) excluded.
- Freezes by reason and OFAC SDN count (freezes and unique addresses), supply operations kept apart from freezes.
- Pulse: execution heatmap, signer speed and share of last signatures, monthly median delay, queue length.
- Funds moved before the freeze (72 h): escaped vs internal, front-run list, truncation flags, labelled service wallets reported apart.
- Cross-chain signals with lag and "moved before" statistics; batch, cross-chain and shared-counterparty clusters.

**Dashboard**
- Watchlist and header search for EVM, TRON, Solana, NEAR, Aptos and TON; status banners (⛔ ⏳ 🔓 ✅); opt-in live on-chain check from the browser; CSV / JSON export of every table.
- Shareable address page (`#addr=`) with Markdown / JSON report and print layout; reason and OFAC chips; wallet-attribution labels.
- Tooltips on every action, glossary, stale-proposal and treasury / supply switches, light and dark themes.
- Status page with per-source health and coverage (`site/health.json`), header health pill.
- Mobile layout: compact header and menu, card lists instead of tables, scrollable chain pills and tabs.

**Alerts and ops**
- Telegram "Radar" alerts (freeze incoming, frozen, burned, cross-chain signal, sent to a frozen address), deduplicated, flood-guarded, fresh events only.
- API key rotation with cooldowns, per-source health records, GitHub Actions cron with an external-dispatch option; generated data lives on an orphan `data` branch.

**Docs**
- New README with demo GIF, screenshots and a mermaid overview; `tools/make_assets.py` regenerates every image.

## v1.0.0

- Track Tether USDT freeze proposals (multisig, pending) and executed blacklistings on Ethereum and TRON.
- Same Gnosis MultiSigWallet mechanism on Avalanche, Celo, Kaia and Kava.
- USDT0 freeze proposals from Safe Transaction Service on 12 chains.
- NEAR, Aptos (exact sequence matching) and TON multisig queues.
- Executed-only freezes for USDC (Ethereum) and USDC/USDT (Solana).
- Delay statistics, cross-chain address view and optional Telegram alerts.
- Static English dashboard with chain filters, stale-proposal switch and dark/light themes.
- GitHub Actions cron; generated data lives on an orphan `data` branch, `main` stays code-only.
