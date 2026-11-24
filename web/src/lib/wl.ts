// Watchlist / address-page model: status banner priority rules, balances, timeline, markdown report, CSV rows.
import { cshort, dstr, dur, isoS, med, NOW, usd } from "./format"
import type { AddrRecord, Data } from "./types"
import type { LiveRun } from "./live"

export interface WlResult {
  input: string
  key: string | null
  /** undefined = index could not be loaded, null = not in the index */
  rec: AddrRecord | null | undefined
  live: LiveRun | null
  invalid?: boolean
}

export type StatusKey = "BLOCKED" | "PROPOSED" | "UNFROZEN" | "NOT_BLOCKED" | "UNKNOWN"
export interface StatusModel {
  status: StatusKey; icon: string; word: string; cls: "red" | "amb" | "gry" | "grn" | "unk"
  summary: string; amount: string | null
  blocked: string[]; proposed: string[]; unfrozen: string[]; clean: string[]; likely?: string[]; also: string
}

export const ACTION: Record<string, string> = { f: "frozen", u: "unfrozen", b: "burned (funds destroyed)" }
export const STATE_TXT = { blocked: "Frozen - cannot move", unfrozen: "Unfrozen - funds can move again", clear: "Not blocked" } as const
export type BalState = keyof typeof STATE_TXT

export const isSafeChain = (d: Data, c: string) => d.chains[c]?.mech === "safe_api"
export const cname = (d: Data, c: string) => d.chains[c]?.label || c
const propRef = (d: Data, c: string, id: number) => (isSafeChain(d, c) ? "Safe nonce " + Math.floor(id / 100) : "proposal #" + id)

export function chainState(r: WlResult, c: string): BalState {
  const rec = r.rec, lv = r.live?.results.find((x) => x.chain === c && x.blocked !== null), ix = rec?.s ? rec.s[c] : null
  if (lv) return lv.blocked ? "blocked" : ix === "unfrozen" || ix === "frozen" || ix === "burned" ? "unfrozen" : "clear"
  if (ix === "frozen" || ix === "burned") return "blocked"
  return ix === "unfrozen" ? "unfrozen" : "clear"
}

export interface BalRow { c: string; v: number; asof: string | null; src: "index" | "live"; state: BalState }
export function balanceRows(r: WlResult): BalRow[] {
  const out: Record<string, Omit<BalRow, "state">> = {}, rec = r.rec
  if (rec?.b) for (const [c, v] of Object.entries(rec.b)) if (v > 0) out[c] = { c, v, asof: rec.bc?.[c] || null, src: "index" }
  if (r.live) for (const x of r.live.results) if (x.balance && x.balance > 0) out[x.chain] = { c: x.chain, v: x.balance, asof: x.checked_at.slice(0, 16).replace("T", " ") + " UTC", src: "live" }
  return Object.values(out).map((b) => ({ ...b, state: chainState(r, b.c) })).sort((a, b) => b.v - a.v)
}

export function medianDelay30(d: Data, chain: string) {
  const x = d.ex.filter((e) => e[0] === chain && e[2] >= 0 && NOW() - e[1] <= 30 * 86400).map((e) => e[2])
  return x.length ? med(x) : null
}

export function statusOf(d: Data, r: WlResult): StatusModel {
  if (r.invalid) return { status: "UNKNOWN", icon: "❔", word: "UNKNOWN", cls: "unk", summary: "Not a recognised address (EVM, TRON, Solana, NEAR, Aptos or TON)", amount: null, blocked: [], proposed: [], unfrozen: [], clean: [], also: "" }
  const rec = r.rec, L = r.live, idx = rec?.s || {}, liveBy: Record<string, NonNullable<LiveRun>["results"][number]> = {}
  if (L) for (const x of L.results) if (x.blocked !== null) liveBy[x.chain] = x
  const blocked: string[] = [], unfrozen: string[] = [], clean: string[] = [], likely: string[] = []
  for (const c of new Set([...Object.keys(idx), ...Object.keys(liveBy)])) {
    const ix = idx[c], lv = liveBy[c], was = ix === "frozen" || ix === "burned"
    if (lv) { if (lv.blocked) blocked.push(c); else if (was) { unfrozen.push(c); likely.push(c) } else if (ix === "unfrozen") unfrozen.push(c); else clean.push(c) }
    else if (was) blocked.push(c)
    else if (ix === "unfrozen") unfrozen.push(c)
  }
  const pend = (rec?.p || []).filter((p) => p[6] === "pending").sort((a, b) => b[3] / (b[4] || 99) - a[3] / (a[4] || 99))
  const proposed = [...new Set(pend.map((p) => p[0] as string))]
  const evs = (c: string, k: string) => (rec?.e || []).filter((e) => e[0] === c && e[1] === k).sort((a, b) => a[2] - b[2])
  const bal = (c: string): number | null => (liveBy[c] && liveBy[c].balance != null ? liveBy[c].balance : rec?.b ? (rec.b[c] ?? null) : null)
  let st: Pick<StatusModel, "status" | "icon" | "word" | "cls" | "summary" | "amount">
  if (blocked.length) {
    const firsts = blocked.map((c) => evs(c, "f")[0]).filter(Boolean).sort((a, b) => a[2] - b[2])
    let sum = "on " + blocked.length + " chain" + (blocked.length > 1 ? "s" : "")
    if (firsts.length) {
      const e = firsts[0]
      sum += " · since " + dstr(e[2]) + " · " + (e[11] === 0 ? "no public proposal" : e[5] == null ? "delay n/a" : e[5] === 0 ? "no advance notice" : dur(e[5]) + " after proposal")
    } else sum += " · blocked after our last update"
    const known = blocked.map(bal).filter((v): v is number => v != null), total = known.reduce((s, v) => s + v, 0)
    st = { status: "BLOCKED", icon: "⛔", word: "BLOCKED", cls: "red", summary: sum, amount: known.length ? usd(total) + " frozen now" : "balance unknown" }
  } else if (pend.length) {
    const p = pend[0], m = medianDelay30(d, p[0]), b = bal(p[0]) ?? p[7]
    st = { status: "PROPOSED", icon: "⏳", word: "FREEZE PROPOSED", cls: "amb", summary: cshort(p[0]) + " · " + propRef(d, p[0], p[1]) + " · " + p[3] + "/" + (p[4] ?? "?") + " signed" + (m != null ? " · usually executes in ~" + dur(m) : ""), amount: b != null ? usd(b) + " at risk" : "balance unknown" }
  } else if (unfrozen.length) {
    const c = unfrozen[0], f = evs(c, "f")[0], u = evs(c, "u").slice(-1)[0]
    const movable = balanceRows(r).filter((b) => b.state === "unfrozen").reduce((s, b) => s + b.v, 0)
    st = { status: "UNFROZEN", icon: "🔓", word: "UNFROZEN", cls: "gry", summary: unfrozen.map(cshort).join(", ") + (likely.includes(c) || !u ? " · likely unfrozen recently (live check)" : " · was blocked " + (f ? dstr(f[2]) : "?") + " – " + dstr(u[2]) + " · not blocked now"), amount: movable > 0 ? usd(movable) + " now movable" : null }
  } else {
    st = { status: "NOT_BLOCKED", icon: "✅", word: "NOT BLOCKED", cls: "grn", summary: (rec ? "no active freeze" : "no freeze history in Tether / USDT0 / USDC data") + (L && !L.note ? " · live: clean on " + clean.length + " chain" + (clean.length === 1 ? "" : "s") : ""), amount: null }
  }
  const also: string[] = []
  if (st.status === "BLOCKED" && proposed.length) also.push("⏳ proposal pending on " + proposed.map(cshort).join(", "))
  if (st.status !== "UNFROZEN" && unfrozen.length) also.push("🔓 unfrozen on " + unfrozen.map(cshort).join(", "))
  return { ...st, blocked, proposed, unfrozen, clean, likely, also: also.join(" · ") }
}

export interface TlItem { ts: number; chain: string; label: string; tx?: string | null; kind?: string }
export function timelineOf(d: Data, r: WlResult): TlItem[] {
  const rec = r.rec, items: TlItem[] = []
  if (rec) {
    for (const e of rec.e || []) {
      if (e[10]) items.push({ ts: e[10], chain: e[0], label: "proposal submitted" + (e[7] != null && e[7] < 1e12 ? " (" + (isSafeChain(d, e[0]) ? "Safe nonce " + Math.floor(e[7] / 100) : "#" + e[7]) + ")" : "") })
      items.push({ kind: e[1], ts: e[2], chain: e[0], tx: e[3], label: e[1] === "f" ? "frozen" + (e[11] === 0 ? " (no public proposal)" : e[5] === 0 ? " (no advance notice)" : e[5] != null ? " (" + dur(e[5]) + " after proposal)" : "") : e[1] === "u" ? "unfrozen" : "funds burned" + (e[4] != null ? " " + usd(e[4]) : "") })
    }
    for (const p of rec.p || []) if (p[5]) items.push({ ts: p[5], chain: p[0], label: (p[6] === "stale" ? "stale" : "pending") + " proposal " + p[2] + " " + p[3] + "/" + (p[4] ?? "?") })
    for (const [c, v] of Object.entries(rec.i || {})) {
      if (v[3]) items.push({ ts: v[3], chain: c, label: "first inflow after freeze (" + v[0] + " transfers, " + usd(v[1]) + " in total)" })
      if (v[4] && v[4] !== v[3]) items.push({ ts: v[4], chain: c, label: "latest inflow after freeze" })
    }
  }
  if (r.live) for (const [c, f] of Object.entries(r.live.found || {})) if (f.ts) items.push({ ts: f.ts, chain: c, label: "blocked (found on chain)", tx: f.tx })
  return items.sort((a, b) => a.ts - b.ts)
}

export const mdEsc = (s: unknown) => String(s == null ? "" : s).replace(/\|/g, "\\|").replace(/\n/g, " ")
const delayTxt = (e: any[]) => (e[11] === 0 ? "no public proposal" : e[5] == null ? "n/a" : e[5] === 0 ? "no advance notice" : dur(e[5]))

export function buildReport(d: Data, r: WlResult, nowIso: string): string {
  const rec = r.rec, st = statusOf(d, r), L: string[] = []
  const CH = d.chains
  const explorer = (c: string, h?: string | null) => { const b = CH[c]?.tx; return b && h ? b + h : null }
  L.push("# Address report: " + r.input, "")
  L.push("- Address: `" + r.input + "`" + (r.key && r.key !== r.input ? " (index key `" + r.key + "`)" : ""))
  L.push("- Generated: " + nowIso + " (UTC)")
  L.push("- Data as of: " + d.generated_at + " (UTC)")
  L.push("- Source: USDT Freeze Alert, https://prettydeath.github.io/usdt-freeze-alert/#addr=" + encodeURIComponent(r.input), "")
  L.push("## Status", "")
  L.push("**" + st.word + "** - " + st.summary + (st.amount ? " (" + st.amount + ")" : "") + (st.also ? "; also: " + st.also : ""))
  if (r.live) L.push("- Live on-chain check at " + r.live.at + " (UTC): " + r.live.results.map((x) => CH[x.chain]?.label + ": " + (x.blocked === true ? "BLOCKED" : x.blocked === false ? "not blocked" : "unavailable") + (x.balance != null ? " (balance " + usd(x.balance) + ")" : "") + " via " + x.source).join("; "))
  const labels = rec?.l ? Object.entries(rec.l) : [], reasons = rec?.r ? Object.entries(rec.r) : []
  if (labels.length || reasons.length) {
    L.push("", "## Labels, reasons and sanctions", "")
    for (const [c, l] of labels) L.push("- Label from wallet-attribution (" + c + "): " + [l[0], l[1], l[2]].filter(Boolean).join(" / ") + " - sources: " + l[3] + (l[4] ? " - " + l[4] : ""))
    for (const [c, x] of reasons) L.push("- " + (x[4] ? "OFAC SDN listed" : "Reason hint: " + x[0] + " (" + x[1] + ")") + " [" + c + "; " + (x[3] === "curated" ? "curated, public source" : "label from wallet-attribution") + "]" + (x[2] ? " - " + x[2] : ""))
    L.push("- Labels are third-party hints (wallet-attribution, MIT, and its upstream sources), not findings of this tool.")
  }
  const tl = timelineOf(d, r)
  if (tl.length) {
    L.push("", "## Timeline (UTC)", "")
    for (const i of tl) { const u = explorer(i.chain, i.tx); L.push("- " + dstr(i.ts) + " - " + CH[i.chain]?.label + " - " + i.label + (i.tx ? " - tx `" + i.tx + "`" + (u ? " " + u : "") : "")) }
  }
  if (rec?.e?.length) {
    L.push("", "## Executed actions", "", "| Chain | Action | Executed (UTC) | Delay | Amount | Tx |", "|---|---|---|---|---|---|")
    for (const x of rec.e) L.push("| " + mdEsc(CH[x[0]]?.label) + " | " + ({ f: "frozen", u: "unfrozen", b: "burned" } as Record<string, string>)[x[1]] + " | " + dstr(x[2]) + " | " + delayTxt(x) + " | " + (x[4] != null ? usd(x[4]) : "-") + " | `" + (x[3] || "") + "` |")
  }
  const bal = balanceRows(r)
  if (bal.length) {
    L.push("", "## Balances", "", "| Chain | Balance | State | As of |", "|---|---|---|---|")
    for (const b of bal) L.push("| " + mdEsc(CH[b.c]?.label) + " | " + usd(b.v) + " | " + STATE_TXT[b.state] + " | " + (b.asof || "last scan") + (b.src === "live" ? " (live check)" : "") + " |")
  }
  if (rec?.o) {
    L.push("", "## Funds moved before the freeze (72 h window)", "")
    for (const [c, fl] of Object.entries(rec.o)) for (const f of fl) {
      L.push("- " + CH[c]?.label + ", frozen " + dstr(f[0]) + ": escaped " + usd(f[1]) + " (" + usd(f[2]) + " between proposal and execution)" + (f[4] > 0 ? "; " + usd(f[4]) + " moved between addresses that were also frozen (not counted)" : "") + (f[5] ? "; TRUNCATED (lower bound)" : "") + (f[6] ? "; labelled service wallet, not counted" : ""))
      for (const x of f[3]) L.push("  - to `" + x[0] + "` " + usd(x[1]) + (x[2] > 0 ? " (in-window " + usd(x[2]) + ")" : "") + (x[4] ? " [also frozen]" : "") + (x[3] ? " [label from wallet-attribution: " + [x[3][0], x[3][1], x[3][2]].filter(Boolean).join(" / ") + "]" : ""))
    }
  }
  const inf = rec ? Object.entries(rec.i || {}) : []
  if (inf.length) {
    L.push("", "## Funds sent after the freeze", "")
    for (const [c, v] of inf) L.push("- " + CH[c]?.label + ": " + v[0] + " transfer(s), " + usd(v[1]) + "; top senders: " + v[2].map((s) => "`" + s[0] + "` (" + s[1] + ", " + usd(s[2]) + ")").join(", "))
  }
  if (rec?.x?.length) { L.push("", "## Cross-chain signals", ""); for (const x of rec.x) L.push("- " + (x[1] === "proposed" ? "freeze proposed" : "frozen") + " on " + CH[x[0]]?.label + " " + dstr(x[2]) + ", still unblocked: " + x[3].map((e: any[]) => usd(e[3]) + " on " + CH[e[0]]?.label).join(", ")) }
  if (rec?.c?.length) L.push("", "## Clusters", "", "Cluster ids (leads, not proof of a common owner): " + rec.c.map((i) => "`" + i + "`").join(", "))
  if (rec?.p?.length) { L.push("", "## Pending / stale proposals", ""); for (const p of rec.p) L.push("- " + CH[p[0]]?.label + " " + (p[6] === "stale" ? "stale" : "pending") + " proposal " + p[1] + " (" + p[2] + "), " + p[3] + "/" + (p[4] ?? "?") + " signatures" + (p[5] ? ", submitted " + dstr(p[5]) : "")) }
  L.push("", "## Sources", "")
  L.push("- Freeze data: Tether multisig contracts and token events via Etherscan V2, public RPCs, TronGrid/Tronscan, Safe Transaction Service, nearblocks, Aptos Indexer, tonapi and the Solana RPC (see the project README).")
  L.push("- Labels: wallet-attribution (MIT) and its upstream sources.")
  L.push("- Times are UTC. Not financial or legal advice.")
  return L.join("\n") + "\n"
}

const tlJson = (d: Data, r: WlResult) => timelineOf(d, r).map((i) => ({ when_utc: new Date(i.ts * 1000).toISOString(), chain: i.chain, event: i.label, tx: i.tx || null }))
export function reportJson(d: Data, r: WlResult) {
  return JSON.stringify({ address: r.input, key: r.key, generated_utc: new Date().toISOString(), data_as_of_utc: d.generated_at, status: statusOf(d, r), index_record: r.rec, live_check: r.live ? r.live.results : null, timeline: tlJson(d, r), labels_source: "wallet-attribution (MIT) and its upstream sources" }, null, 1)
}
export function watchJson(d: Data, results: WlResult[]) {
  return JSON.stringify(results.map((r) => ({ address: r.input, key: r.key, status: statusOf(d, r).status, found: !!r.rec, invalid: !!r.invalid, data: r.rec, live: r.live ? r.live.results : null, live_found: r.live ? r.live.found : null, timeline: tlJson(d, r) })), null, 1)
}
export function watchRows(d: Data, results: WlResult[]): (string | number | boolean)[][] {
  const rows: (string | number | boolean)[][] = [["address", "key", "chain", "kind", "when_utc", "delay_seconds", "amount", "tx", "state", "balance", "token", "blocked", "checked_at", "source", "status"]]
  for (const r of results) {
    const S = statusOf(d, r).status, n0 = rows.length
    if (!r.rec) rows.push([r.input, r.key || "", "", r.invalid ? "invalid" : "not_found", "", "", "", "", "", "", "", "", "", ""])
    else {
      for (const e of r.rec.e || []) rows.push([r.input, r.key!, e[0], ACTION[e[1]] || e[1], isoS(e[2]), e[5] ?? "", e[4] ?? "", e[3] || "", r.rec.s?.[e[0]] || "", r.rec.b?.[e[0]] ?? "", "", "", "", ""])
      for (const p of r.rec.p || []) rows.push([r.input, r.key!, p[0], "proposal_" + p[6] + ":" + p[2], isoS(p[5]), "", "", "", p[3] + "/" + (p[4] ?? "?"), "", "", "", "", ""])
    }
    for (const b of balanceRows(r)) rows.push([r.input, r.key || "", b.c, "balance", "", "", "", "", STATE_TXT[b.state], b.v, cname(d, b.c), b.state === "blocked", b.asof || "", b.src])
    if (r.live) for (const x of r.live.results) rows.push([r.input, r.key || "", x.chain, "live_check", "", "", "", "", "", x.balance ?? "", x.label, x.blocked === null ? "unavailable" : x.blocked, x.checked_at, x.source])
    for (const i of timelineOf(d, r)) rows.push([r.input, r.key || "", i.chain, "timeline: " + i.label, isoS(i.ts), "", "", i.tx || "", "", "", "", "", "", ""])
    for (let q = n0; q < rows.length; q++) rows[q].push(S)
  }
  return rows
}
