// address normalisation (mirrors freezewatch/addresses.py and the classic dashboard)
const B58A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

export function b58dec(s: string): number[] | null {
  let n = 0n
  for (const c of s) { const i = B58A.indexOf(c); if (i < 0) return null; n = n * 58n + BigInt(i) }
  let h = n.toString(16)
  if (h.length % 2) h = "0" + h
  const bytes = (h.match(/../g) || []).map((x) => parseInt(x, 16))
  const pad = (s.match(/^1*/) || [""])[0].length
  return [...Array(pad).fill(0), ...bytes]
}

export function canonKey(a: string): string | null {
  a = (a || "").trim()
  if (/^0x[0-9a-fA-F]{40}$/.test(a) || /^0x[0-9a-fA-F]{64}$/.test(a)) return a.toLowerCase()
  if (/^0:[0-9a-fA-F]{64}$/.test(a)) return a.toLowerCase()
  if (/^T[1-9A-HJ-NP-Za-km-z]{33}$/.test(a)) {
    const b = b58dec(a)
    if (b && b.length === 25 && b[0] === 0x41) return "0x" + b.slice(1, 21).map((x) => x.toString(16).padStart(2, "0")).join("")
    return null
  }
  if (/^[0-9a-fA-F]{64}$/.test(a)) return a.toLowerCase()
  if (/^[a-z0-9][a-z0-9._-]{1,63}$/.test(a) && (a.includes(".") || a.endsWith("near"))) return a
  if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a)) return a
  return null
}

export const parseAddrs = (text: string) => [...new Set((text || "").split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean))]
export const isEvmKey = (k: string | null) => /^0x[0-9a-f]{40}$/.test(k || "")
export const tronHex = (b58: string) => { const b = b58dec(b58); return b ? b.slice(0, 21).map((x) => x.toString(16).padStart(2, "0")).join("") : null }
