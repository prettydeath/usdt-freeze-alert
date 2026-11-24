// Opt-in live on-chain check; runs in the visitor's browser and sends the address to public RPC providers.
import { isEvmKey, tronHex } from "./address"
import type { Data, LiveCfg, LiveEvm } from "./types"

export interface LiveResult {
  id: string; chain: string; label: string; blocked: boolean | null; balance: number | null
  checked_at: string; source: string; url?: string; error?: string; accounts?: number
}
export interface FoundFreeze { ts?: number; tx?: string; skipped?: boolean; failed?: boolean; none?: boolean; partial?: boolean }
export interface LiveRun { results: LiveResult[]; found: Record<string, FoundFreeze>; at: string; other: boolean; note: string }

const evmData = (sel: string, hex20: string) => "0x" + sel + "0".repeat(24) + hex20.toLowerCase().replace(/^0x/, "")
const decBool = (r: unknown) => (typeof r === "string" && r.length === 66 ? BigInt(r) !== 0n : null)
const decUnits = (r: unknown, d = 6) => (typeof r === "string" && /^0x[0-9a-fA-F]+$/.test(r) ? Number(BigInt(r)) / 10 ** d : null)
const padTopic = (hex20: string) => "0x" + "0".repeat(24) + hex20.toLowerCase().replace(/^0x/, "")
const TOPIC_PLACED = "0x406bbf2d8d145125adf1198d2cf8a67c66cc4bb0ab01c37dccd4f7c0aae1e7c7"
const TOPIC_USDC_BL = "0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855"
const hostOf = (u: string) => { try { return new URL(u).host } catch { return u } }
const nowIso = () => new Date().toISOString()
const iChain = (id: string) => (id === "eth_usdt" ? "eth" : id === "eth_usdc" ? "usdc_eth" : id)

async function postJson(url: string, body: unknown, ms: number): Promise<any> {
  const c = new AbortController(), tm = setTimeout(() => c.abort(), ms)
  try {
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: c.signal })
    return await r.json()
  } finally { clearTimeout(tm) }
}
async function getJson(url: string, ms: number): Promise<any> {
  const c = new AbortController(), tm = setTimeout(() => c.abort(), ms)
  try { const r = await fetch(url, { signal: c.signal }); if (!r.ok) throw new Error("HTTP " + r.status); return await r.json() } finally { clearTimeout(tm) }
}
async function rpcM(url: string, method: string, params: unknown[], ms: number) {
  const j = await postJson(url, { jsonrpc: "2.0", id: 1, method, params }, ms)
  if (j.error) throw new Error(j.error.message || "rpc error")
  return j.result ?? j
}
const ethCall = (url: string, to: string, data: string, ms: number) => rpcM(url, "eth_call", [{ to, data }, "latest"], ms)
const unav = (id: string, label: string, src: string): LiveResult => ({ id, chain: id, label, blocked: null, balance: null, checked_at: nowIso(), source: src, error: "unavailable" })

async function liveEvm(t: LiveEvm, h20: string, ms: number): Promise<LiveResult> {
  for (const url of t.rpcs) {
    try {
      const [b, bal] = await Promise.all([ethCall(url, t.contract, evmData(t.sel, h20), ms), ethCall(url, t.contract, evmData("70a08231", h20), ms)])
      const blocked = decBool(b)
      if (blocked === null) throw new Error("bad result")
      return { id: t.id, chain: iChain(t.id), label: t.label, blocked, balance: decUnits(bal), checked_at: nowIso(), source: hostOf(url), url }
    } catch { /* try the next RPC */ }
  }
  return { ...unav(t.id, t.label, t.rpcs.map(hostOf).join(", ")), chain: iChain(t.id) }
}
async function liveTron(h20: string, L: LiveCfg): Promise<LiveResult | null> {
  const T = L.tron
  if (!T) return null
  const owner = "41" + "0".repeat(40), contract = tronHex(T.contract)
  const call = (sel: string) => postJson(T.endpoint + "/wallet/triggerconstantcontract", { owner_address: owner, contract_address: contract, function_selector: sel, parameter: "0".repeat(24) + h20.toLowerCase().replace(/^0x/, ""), visible: false }, L.timeout)
  try {
    const [b, bal] = await Promise.all([call(T.sel), call(T.balance)])
    const rb = (b.constant_result || [])[0], rbal = (bal.constant_result || [])[0]
    if (!rb) throw new Error("empty")
    return { id: "tron", chain: "tron", label: T.label, blocked: BigInt("0x" + rb) !== 0n, balance: rbal ? Number(BigInt("0x" + rbal)) / 1e6 : null, checked_at: nowIso(), source: hostOf(T.endpoint) }
  } catch { return unav("tron", T.label, hostOf(T.endpoint)) }
}
async function liveSolana(owner: string, L: LiveCfg): Promise<LiveResult[]> {
  const S = L.solana
  if (!S) return []
  const out: LiveResult[] = []
  for (const [id, mint] of Object.entries(S.mints)) {
    const label = id === "sol_usdc" ? "Solana USDC" : "Solana USDT"
    try {
      const res = await rpcM(S.endpoint, "getTokenAccountsByOwner", [owner, { mint }, { encoding: "jsonParsed" }], L.timeout)
      const accs = (res.value || []).map((v: any) => v.account.data.parsed.info)
      out.push({ id, chain: id, label, blocked: accs.some((a: any) => a.state === "frozen"), balance: accs.reduce((s: number, a: any) => s + (Number(a.tokenAmount.uiAmount) || 0), 0), accounts: accs.length, checked_at: nowIso(), source: hostOf(S.endpoint) })
    } catch { out.push(unav(id, label, hostOf(S.endpoint))) }
  }
  return out
}
async function liveNear(acct: string, L: LiveCfg): Promise<LiveResult | null> {
  const N = L.near
  if (!N) return null
  try {
    const j = await postJson(N.endpoint, { jsonrpc: "2.0", id: 1, method: "query", params: { request_type: "call_function", finality: "final", account_id: N.contract, method_name: N.method, args_base64: btoa(JSON.stringify({ account_id: acct })) } }, L.timeout)
    const raw = j.result && j.result.result
    if (!raw) throw new Error("empty")
    const s = JSON.parse(String.fromCharCode(...raw))
    const blocked = s === "Banned" ? true : s === "Allowable" ? false : null
    if (blocked === null) throw new Error("unknown status")
    return { id: "near", chain: "near", label: N.label, blocked, balance: null, checked_at: nowIso(), source: hostOf(N.endpoint) }
  } catch { return unav("near", N.label, hostOf(N.endpoint)) }
}
async function liveAptos(addr: string, L: LiveCfg): Promise<LiveResult | null> {
  const A = L.aptos
  if (!A) return null
  try {
    const j = await postJson(A.endpoint, { function: A.function, type_arguments: [], arguments: [addr] }, L.timeout)
    if (!Array.isArray(j) || !j.length || !j.every((x) => typeof x === "boolean")) throw new Error("bad result")
    return { id: "aptos", chain: "aptos", label: A.label, blocked: j.some(Boolean), balance: null, checked_at: nowIso(), source: hostOf(A.endpoint) }
  } catch { return unav("aptos", A.label, hostOf(A.endpoint)) }
}
async function liveTon(owner: string, L: LiveCfg): Promise<LiveResult | null> {
  const T = L.ton
  if (!T) return null
  try {
    const jw = await getJson(T.endpoint + "/accounts/" + encodeURIComponent(owner) + "/jettons/" + T.master, L.timeout + 3000)
    const w = jw.wallet_address && jw.wallet_address.address
    if (!w) throw new Error("no jetton wallet")
    const st = await getJson(T.endpoint + "/blockchain/accounts/" + encodeURIComponent(w) + "/methods/get_status", L.timeout + 3000)
    const s = st.decoded && st.decoded.status
    if (s !== 0 && s !== 1) throw new Error("unknown status")
    return { id: "ton", chain: "ton", label: T.label, blocked: s === 1, balance: jw.balance != null ? Number(jw.balance) / 1e6 : null, checked_at: nowIso(), source: hostOf(T.endpoint) }
  } catch { return unav("ton", T.label, hostOf(T.endpoint)) }
}

// find the freeze event of a freshly blocked address: bounded eth_getLogs since the data was generated
async function scanFreeze(d: Data, res: LiveResult, h20: string): Promise<FoundFreeze> {
  if (res.id === "eth_usdt" || res.id === "tron" || !res.url) return { skipped: true }
  const t = d.live.evm.find((x) => x.id === res.id)
  if (!t) return { skipped: true }
  const topic0 = res.id === "eth_usdc" ? TOPIC_USDC_BL : TOPIC_PLACED, url = res.url, ms = d.live.timeout
  try {
    const hx = (v: string) => parseInt(v, 16)
    const L = hx(await rpcM(url, "eth_blockNumber", [], ms))
    const bl = (n: number) => rpcM(url, "eth_getBlockByNumber", ["0x" + n.toString(16), false], ms)
    const [a, b] = await Promise.all([bl(L), bl(Math.max(1, L - 2000))])
    const rate = Math.max(0.05, (hx(a.timestamp) - hx(b.timestamp)) / Math.max(1, L - Math.max(1, L - 2000)))
    const since = Date.parse(d.generated_at) / 1000 - 600
    const span = Math.ceil((hx(a.timestamp) - since) / rate) + 100
    let chunk = Math.max(500, span), from = Math.max(0, L - span), calls = 0
    while (from <= L && calls < 12) {
      const to = Math.min(L, from + chunk - 1)
      calls++
      try {
        const logs = await rpcM(url, "eth_getLogs", [{ fromBlock: "0x" + from.toString(16), toBlock: "0x" + to.toString(16), address: t.contract, topics: [topic0, padTopic(h20)] }], ms)
        if (logs.length) { const lg = logs[0]; const blk = await bl(hx(lg.blockNumber)); return { ts: hx(blk.timestamp), tx: lg.transactionHash } }
        from = to + 1
      } catch { if (chunk <= 500) return { failed: true }; chunk = Math.max(500, Math.floor(chunk / 2)) }
    }
    return { none: true, partial: from <= L }
  } catch { return { failed: true } }
}

export async function runLive(d: Data, input: string, key: string, hasFreeze: (chain: string) => boolean, other: boolean): Promise<LiveRun> {
  const L = d.live, k = key, res: LiveRun = { results: [], found: {}, at: nowIso(), other, note: "" }
  if (isEvmKey(k)) {
    const nativeTron = /^T/.test(input), tasks: Promise<LiveResult | null>[] = []
    if (!nativeTron || other) L.evm.forEach((t) => tasks.push(liveEvm(t, k.slice(2), L.timeout)))
    if (nativeTron || other) tasks.push(liveTron(k.slice(2), L))
    if (other) tasks.push(liveNear(k, L)) // 0x + 40 hex is also a valid NEAR (eth-implicit) account
    res.results = (await Promise.all(tasks)).filter((x): x is LiveResult => !!x)
  } else if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(k)) res.results = await liveSolana(k, L)
  else if (/^0x[0-9a-f]{64}$/.test(k)) res.results = [await liveAptos(k, L)].filter((x): x is LiveResult => !!x)
  else if (/^0:[0-9a-f]{64}$/.test(k)) res.results = [await liveTon(k, L)].filter((x): x is LiveResult => !!x)
  else if (/^[0-9a-f]{64}$/.test(k) || /^[a-z0-9][a-z0-9._-]{1,63}$/.test(k)) res.results = [await liveNear(k, L)].filter((x): x is LiveResult => !!x)
  else res.note = "Live check is available for EVM, TRON, Solana, NEAR, Aptos and TON addresses."
  const hex = isEvmKey(k) ? k.slice(2) : null
  await Promise.all(res.results.filter((x) => x.blocked === true).map(async (x) => {
    if (!hasFreeze(x.chain) && hex) res.found[x.chain] = await scanFreeze(d, x, hex)
  }))
  return res
}
