import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"
import type { Data } from "./types"
import { orderIdx } from "./format"
import { recordLayouts } from "./motion"

export interface Group { id: string; label: string; ch: string[] }

interface AppCtx {
  d: Data
  groups: Group[]
  sel: Set<string>
  toggleGroup: (id: string | null) => void
  active: (chain: string) => boolean
  cname: (chain: string) => string
  /** key that changes whenever the chain filter changes */
  filterKey: string
}
const Ctx = createContext<AppCtx | null>(null)
export const useApp = () => { const c = useContext(Ctx); if (!c) throw new Error("no app context"); return c }

export function AppProvider({ d, children }: { d: Data; children: ReactNode }) {
  const CH = d.chains
  const groups = useMemo<Group[]>(() => {
    const safe = Object.keys(CH).filter((c) => CH[c].family === "usdt0").sort((a, b) => orderIdx(a) - orderIdx(b))
    const g: Group[] = [
      { id: "eth", label: "ETH", ch: ["eth"] }, { id: "tron", label: "TRON", ch: ["tron"] }, { id: "avax", label: "AVAX", ch: ["avax"] },
      { id: "usdt0", label: "USDT0/ARB…", ch: safe }, { id: "usdc", label: "USDC", ch: ["usdc_eth"] }, { id: "sol", label: "SOL", ch: ["sol_usdc", "sol_usdt"] },
      { id: "more", label: "", ch: ["arb_usdt", "celo", "kaia", "kava", "near", "aptos", "ton"] },
    ].map((x) => ({ ...x, ch: x.ch.filter((c) => CH[c]) })).filter((x) => x.ch.length)
    const more = g.find((x) => x.id === "more")
    if (more) more.label = "+" + more.ch.length
    return g
  }, [CH])
  const [sel, setSel] = useState<Set<string>>(new Set())
  const toggleGroup = useCallback((id: string | null) => {
    recordLayouts()
    setSel((s) => { if (id == null) return new Set(); const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }, [])
  const active = useCallback((c: string) => sel.size === 0 || groups.some((g) => sel.has(g.id) && g.ch.includes(c)), [sel, groups])
  const cname = useCallback((c: string) => CH[c]?.label || c, [CH])
  const filterKey = [...sel].sort().join(",")
  const v = useMemo(() => ({ d, groups, sel, toggleGroup, active, cname, filterKey }), [d, groups, sel, toggleGroup, active, cname, filterKey])
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>
}

export const chainsSorted = (cs: string[]) => [...cs].sort((a, b) => orderIdx(a) - orderIdx(b))
