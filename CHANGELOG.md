# Changelog

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
