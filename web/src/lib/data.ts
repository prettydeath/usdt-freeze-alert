import type { Analytics, Data, AddrRecord, Status } from "./types"

// Where data.json & co live relative to the page. The app is built to site/app/, so the data is one level up.
// Dev server (public/ fixture) and a future site-root deployment use "./".
export const DATA_BASE: string = (import.meta.env.VITE_DATA_BASE as string | undefined) ?? (import.meta.env.DEV ? "./" : "../")

async function getJson<T>(name: string): Promise<T> {
  const r = await fetch(DATA_BASE + name, { cache: "no-cache" })
  if (!r.ok) throw new Error(name + ": HTTP " + r.status)
  return (await r.json()) as T
}

export const loadData = () => getJson<Data>("data.json")
export const loadHealth = () => getJson<Status>("health.json").catch(() => null)

let anP: Promise<Analytics | null> | null = null
export function loadAnalytics(d: Data): Promise<Analytics | null> {
  if (d.analytics) return Promise.resolve(d.analytics)
  anP ??= getJson<Analytics>(d.analytics_url || "analytics.json").catch(() => null)
  return anP
}

// address index (lazy: only for watchlist / address page)
interface Manifest { sharded?: boolean; a?: Record<string, AddrRecord>; failed?: boolean }
let manP: Promise<Manifest> | null = null
const shards: Record<string, Promise<Record<string, AddrRecord>>> = {}
const manifest = () => (manP ??= getJson<Manifest>("addresses.json").catch(() => ({ failed: true }) as Manifest))

/** undefined = index unavailable, null = not in the index */
export async function getRecord(key: string): Promise<AddrRecord | null | undefined> {
  const m = await manifest()
  if (m.failed) return undefined
  if (!m.sharded) return m.a?.[key] ?? null
  const s = key.startsWith("0x") ? key[2] : "_"
  shards[s] ??= getJson<{ a: Record<string, AddrRecord> }>("addr/" + s + ".json").then((j) => j.a).catch(() => ({}))
  return (await shards[s])[key] ?? null
}

export const RAW = "https://raw.githubusercontent.com/prettydeath/usdt-freeze-alert/data/"
