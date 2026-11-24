export const NOW = () => Date.now() / 1000

export const ORDER = ["eth", "tron", "avax", "arb_usdt", "celo", "kaia", "kava", "near", "aptos", "ton", "arb1", "pol", "plasma", "mantle", "oeth", "monad", "unichain", "stable", "morph", "ink", "hyper", "berachain", "xlayer", "flare", "conflux", "sei", "megaeth", "rootstock", "usdc_eth", "sol_usdc", "sol_usdt"]
export const NO_DELAY = ["usdc_eth", "sol_usdc", "sol_usdt"]
export const orderIdx = (c: string) => { const i = ORDER.indexOf(c); return i < 0 ? 99 : i }

const SHORT: Record<string, string> = { eth: "ETH", tron: "TRON", avax: "AVAX", celo: "CELO", kaia: "KAIA", kava: "KAVA", near: "NEAR", aptos: "APT", ton: "TON", usdc_eth: "USDC-ETH", sol_usdc: "USDC-SOL", sol_usdt: "USDT-SOL", arb_usdt: "ARB-USDT" }
export const cshort = (c: string) => SHORT[c] || c.toUpperCase()

export const FREEZE = ["addBlackList", "addToBlockedList", "freeze_accounts", "freeze_wallet", "freeze", "freezeAccount"]
export const DESTROY = ["destroyBlackFunds", "destroyBlockedFunds", "burn_from"]
export const REMOVE = ["removeBlackList", "removeFromBlockedList", "unfreeze_accounts", "unfreeze_wallet", "unfreeze", "thawAccount"]
export const SUPPLY = ["issue", "mint", "redeem", "burn"]
export type Category = "freeze" | "unfreeze" | "destroy" | "supply" | "admin"
export const catOf = (f: string): Category => FREEZE.includes(f) ? "freeze" : REMOVE.includes(f) ? "unfreeze" : DESTROY.includes(f) ? "destroy" : SUPPLY.includes(f) ? "supply" : "admin"

export function dur(sec: number | null | undefined): string {
  if (sec == null || sec < 0) return "n/a"
  sec = Math.round(sec)
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  if (d) return d + "d " + h + "h"
  if (h) return h + "h " + m + "m"
  if (m) return m + "m " + s + "s"
  return s + "s"
}
export const usd = (x: number | null | undefined) => x == null ? "-" : "$" + Math.round(x).toLocaleString("en-US")
export const usdS = (x: number) => x >= 1e9 ? "$" + (x / 1e9).toFixed(2) + "B" : x >= 1e6 ? "$" + (x / 1e6).toFixed(1) + "M" : usd(x)
export const num = (x: number) => x.toLocaleString("en-US")
export const dt = (s: string | null | undefined) => s ? s.replace("T", " ").replace("Z", " UTC") : "-"
export const dstr = (ts: number | null | undefined) => ts ? new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "-"
export const dday = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 10)
export const age = (s: string | null | undefined) => s ? dur(NOW() - Date.parse(s) / 1000) : "unknown"
export const isoS = (ts: number | null | undefined) => ts ? new Date(ts * 1000).toISOString().replace(".000Z", "Z") : ""
export const med = (a: number[]): number | null => {
  if (!a.length) return null
  const b = [...a].sort((x, y) => x - y), k = (b.length - 1) / 2
  return (b[Math.floor(k)] + b[Math.ceil(k)]) / 2
}
export const mondayOf = (t: number) => { const d = new Date(t * 1000), k = (d.getUTCDay() + 6) % 7; return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - k) / 1000 }
