import { useEffect, useState } from "react"
import { parseAddrs } from "./address"

export const TABS = ["live", "analytics", "watchlist", "signers", "status"] as const
export type TabId = (typeof TABS)[number]
export type Route = { name: TabId } | { name: "addr"; addr: string } | { name: "watch"; list: string[] }

const safeDecode = (s: string) => { try { return decodeURIComponent(s) } catch { return s } }

export function parseHash(hash: string): Route {
  const h = hash.replace(/^#/, "")
  let m = h.match(/^addr=(.*)$/)
  if (m) return { name: "addr", addr: safeDecode(m[1]).trim() }
  m = h.match(/^watch=(.*)$/)
  if (m) return { name: "watch", list: parseAddrs(safeDecode(m[1])) }
  if ((TABS as readonly string[]).includes(h)) return { name: h as TabId }
  let t = "live"
  try { t = localStorage.getItem("ufa.tab") || "live" } catch { /* storage unavailable */ }
  return { name: (TABS as readonly string[]).includes(t) ? (t as TabId) : "live" }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash))
  useEffect(() => {
    const f = () => setRoute(parseHash(location.hash))
    window.addEventListener("hashchange", f)
    return () => window.removeEventListener("hashchange", f)
  }, [])
  return route
}

export function go(hash: string) {
  if (location.hash === "#" + hash) window.dispatchEvent(new HashChangeEvent("hashchange"))
  else location.hash = hash
}
export const addrHref = (a: string) => "#addr=" + encodeURIComponent(a)
export function rememberTab(t: TabId) { try { localStorage.setItem("ufa.tab", t) } catch { /* storage unavailable */ } }
