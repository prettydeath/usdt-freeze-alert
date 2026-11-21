<p align="center"><img src="assets/banner.svg" alt="USDT Freeze Alert - See USDT freezes before they happen" width="100%"></p>

<p align="center">
  <a href="https://github.com/OWNER/usdt-freeze-alert/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/OWNER/usdt-freeze-alert/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/OWNER/usdt-freeze-alert/actions/workflows/watch.yml"><img alt="watch" src="https://github.com/OWNER/usdt-freeze-alert/actions/workflows/watch.yml/badge.svg"></a>
  <img alt="last update" src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FOWNER%2Fusdt-freeze-alert%2Fdata%2Fsite%2Fbadges%2Fupdated.json">
  <img alt="chains" src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FOWNER%2Fusdt-freeze-alert%2Fdata%2Fsite%2Fbadges%2Fchains.json">
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

Tether freezes USDT through multisig wallets. The proposal is public before it is executed - this project watches
those queues across chains, stores everything in SQLite, measures how long proposals wait, and renders a static dashboard.

<p align="center">
  <img alt="pending now" src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FOWNER%2Fusdt-freeze-alert%2Fdata%2Fsite%2Fbadges%2Fpending.json&style=for-the-badge">
  <img alt="median delay" src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FOWNER%2Fusdt-freeze-alert%2Fdata%2Fsite%2Fbadges%2Fmedian.json&style=for-the-badge">
  <img alt="freezes 7d" src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FOWNER%2Fusdt-freeze-alert%2Fdata%2Fsite%2Fbadges%2Ffreezes7d.json&style=for-the-badge">
</p>

<p align="center">
  <a href="assets/dashboard-dark.png"><img src="assets/dashboard-dark.png" alt="Dashboard (dark)" width="49%"></a>
  <a href="assets/dashboard-light.png"><img src="assets/dashboard-light.png" alt="Dashboard (light)" width="49%"></a>
</p>

## How a freeze happens

```mermaid
flowchart LR
  A[Owner submits<br/>addBlackList] --> B[Other owners sign<br/>3 of 6 / 2 of 3]
  B --> C{Threshold<br/>reached?}
  C -- yes --> D[Executed: address frozen]
  C -- no --> B
  A -. visible here .-> B
```

The window between *submitted* and *executed* is the early warning. Median is hours on Ethereum and TRON; about 3% of
Ethereum executions land in the same block and cannot be seen coming.

## Coverage

| Mechanism | Chains |
|---|---|
| **On-chain pending** (multisig contract) | Ethereum, TRON, Avalanche, Celo*, Kaia*, Kava*, NEAR, Aptos, TON* |
| **Safe API pending** (off-chain, not guaranteed) | USDT0 on Arbitrum, Polygon, Plasma, Mantle, Optimism, Monad, Unichain, Stable, Morph, Ink, HyperEVM, Berachain |
| **Executed only** (no queue exists) | USDC on Ethereum, USDC and USDT on Solana |

\* delays are `n/a`: no reliable timestamps from public indexers.

## Quick start

```bash
pip install requests
export ETHERSCAN_API_KEY=...        # required for Ethereum history
python -m freezewatch run           # update + stats + site/index.html
```

`python -m freezewatch backfill --chain all` fetches full history (resumable). `run --loop --interval 60` is a local daemon.
`python -m pytest` runs the offline test-suite.

## Run it on GitHub Actions

Fork, add secrets, enable Actions. A cron job runs every 10 minutes and force-pushes **one orphan commit** with
`data/` and `site/` to the `data` branch, so `main` stays code-only and history never grows. The dashboard is
published to Pages from that branch only when something changed.

| Secret | Required | Purpose |
|---|---|---|
| `ETHERSCAN_API_KEY` | yes | Ethereum and USDC logs (Etherscan V2) |
| `TRONGRID_API_KEY` | no | faster TRON reads (public limit is 3 req/s) |
| `SAFE_API_KEY` | no | Safe Transaction Service |
| `TONAPI_KEY` | no | tonapi.io |
| `ETH_RPC_URL`, `SOLANA_RPC_URL` | no | your own RPCs (the public Solana RPC is very slow) |
| `TG_BOT_TOKEN`, `TG_CHAT_ID` | no | Telegram alerts: new proposal, quorum-1, executed, new freeze, new chain for an address |

Seed the `data` branch once from a local run (see the commands in the release notes), or let the first runs build it.

## Limits worth knowing

- TRON execution time is inferred from confirmations (the multisig emits no queryable events); verified against on-chain state in batches.
- Safe queues live off-chain: a signer can skip them, so "no proposal" does not mean "no freeze".
- Solana freezes a token account, not the wallet; Solana USDT history fills gradually on the public RPC.
- Not financial advice.

## Data and attribution

Everything is read from public chain data and APIs. Delays were cross-checked against
[BitOK's tether-blacklist-research](https://github.com/BitOKorg/tether-blacklist-research) (bitok.org, CC BY 4.0):
Ethereum matches on 2251 of 2251 shared proposals, TRON on 7204 of 7204. No BitOK data is included in this repository.

## License

MIT. See [LICENSE](LICENSE) and [CHANGELOG](CHANGELOG.md).
