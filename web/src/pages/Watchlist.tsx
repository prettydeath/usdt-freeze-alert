import { useCallback, useEffect, useRef, useState } from "react"
import { Download, Eraser, FileUp, Lock, ShieldAlert } from "lucide-react"
import { useApp } from "@/lib/app"
import { canonKey, parseAddrs } from "@/lib/address"
import { getRecord } from "@/lib/data"
import { runLive, type LiveRun } from "@/lib/live"
import { downloadNamed, toCsv } from "@/lib/exporter"
import { statusOf, watchJson, watchRows, type WlResult } from "@/lib/wl"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Textarea } from "@/components/ui/textarea"
import { H2, Muted } from "@/components/common"
import { AddrCard } from "@/components/AddrCard"
import type { Data } from "@/lib/types"

// live results survive switching between the watchlist and an address page
export const liveCache = new Map<string, LiveRun>()

export async function resolveAddr(input: string): Promise<WlResult> {
  const key = canonKey(input)
  if (!key) return { input, key: null, invalid: true, rec: null, live: null }
  return { input, key, rec: await getRecord(key), live: liveCache.get(key) ?? null }
}
export async function liveFor(d: Data, r: WlResult, other: boolean): Promise<LiveRun | null> {
  if (!r.key) return null
  const hasFreeze = (chain: string) => !!r.rec?.e?.some((e) => e[0] === chain && e[1] === "f")
  const run = await runLive(d, r.input, r.key, hasFreeze, other)
  liveCache.set(r.key, run)
  return run
}

export default function Watchlist({ initial }: { initial?: string[] }) {
  const { d } = useApp()
  const [text, setText] = useState(() => {
    if (initial?.length) return initial.join("\n")
    try { const s = JSON.parse(localStorage.getItem("ufa.watch") || "[]"); if (Array.isArray(s) && s.length) return s.join("\n") } catch { /* ignore */ }
    return ""
  })
  const [results, setResults] = useState<WlResult[]>([])
  const [info, setInfo] = useState("")
  const [loading, setLoading] = useState(false)
  const [other, setOther] = useState(false)
  const [liveAll, setLiveAll] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const run = useCallback(async (inputs: string[], save: boolean) => {
    setLoading(true)
    const res: WlResult[] = []
    for (const i of inputs) res.push(await resolveAddr(i))
    setResults(res)
    setLoading(false)
    setInfo(res.length ? res.length + " checked · " + res.filter((r) => r.rec).length + " found · " + res.filter((r) => r.invalid).length + " invalid" : "")
    if (save) { try { localStorage.setItem("ufa.watch", JSON.stringify(inputs)) } catch { /* storage unavailable */ } }
  }, [])

  // deep link (#watch=a,b) and first visit with a saved list
  const initKey = initial?.join(",") ?? ""
  useEffect(() => {
    if (initial?.length) { setText(initial.join("\n")); void run(initial, false) }
    else if (text.trim()) void run(parseAddrs(text), false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initKey])

  const setLive = (i: number) => async () => {
    const r = results[i]
    const lv = await liveFor(d, r, other)
    setResults((rs) => rs.map((x, j) => (j === i ? { ...x, live: lv } : x)))
  }
  const checkAll = async () => {
    setLiveAll(true)
    const next: WlResult[] = []
    for (const r of results) next.push(r.key ? { ...r, live: await liveFor(d, r, other) } : r)
    setResults(next)
    setLiveAll(false)
  }
  const onFile = (f: File | undefined) => {
    if (!f) return
    const rd = new FileReader()
    rd.onload = () => { const t = (text ? text + "\n" : "") + String(rd.result); setText(t); void run(parseAddrs(t), false) }
    rd.readAsText(f)
  }
  const counts = { BLOCKED: 0, PROPOSED: 0, UNFROZEN: 0, NOT_BLOCKED: 0, UNKNOWN: 0 }
  results.forEach((r) => { counts[statusOf(d, r).status]++ })
  const summary = results.length ? [results.length + " checked", counts.BLOCKED && "⛔ " + counts.BLOCKED + " blocked", counts.PROPOSED && "⏳ " + counts.PROPOSED + " proposed", counts.UNFROZEN && "🔓 " + counts.UNFROZEN + " unfrozen", counts.NOT_BLOCKED && "✅ " + counts.NOT_BLOCKED + " clean", counts.UNKNOWN && "❔ " + counts.UNKNOWN + " unknown"].filter(Boolean).join(" · ") : ""

  return (
    <div>
      <H2>Watchlist and address lookup</H2>
      <Card className="p-3.5">
        <Textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Addresses to check" className="min-h-28 text-sm"
          placeholder="Paste addresses (comma, space or newline separated): EVM, TRON, Solana, NEAR, Aptos, TON" />
        <div className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(150px,max-content))] items-center gap-2 max-sm:grid-cols-2">
          <Button onClick={() => run(parseAddrs(text), false)} className="max-sm:h-auto max-sm:min-h-10 max-sm:whitespace-normal max-sm:text-sm">Check</Button>
          <Button variant="outline" onClick={() => run(parseAddrs(text), true)} className="max-sm:h-auto max-sm:min-h-10 max-sm:whitespace-normal max-sm:text-sm">Check and save in this browser</Button>
          <Button variant="outline" onClick={() => fileRef.current?.click()} className="max-sm:h-auto max-sm:min-h-10 max-sm:whitespace-normal max-sm:text-sm"><FileUp />Load .txt / .csv</Button>
          <input ref={fileRef} type="file" accept=".txt,.csv,text/plain,text/csv" className="sr-only" aria-label="Load addresses from a file" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = "" }} />
          <Button variant="outline" className="max-sm:h-auto max-sm:min-h-10 max-sm:whitespace-normal max-sm:text-sm" onClick={() => { setText(""); liveCache.clear(); try { localStorage.removeItem("ufa.watch") } catch { /* storage unavailable */ } void run([], false) }}><Eraser />Clear</Button>
          <span className="text-muted-foreground text-xs">{info}</span>
        </div>
        <div className="mt-2.5 border-t border-dashed pt-2">
          <Button variant="outline" size="sm" className="border-primary text-primary max-sm:h-10 max-sm:w-full max-sm:text-sm" disabled={!results.length || liveAll} onClick={checkAll}>{liveAll ? "Checking…" : "Check all live on-chain"}</Button>
          <label className="ml-2.5 inline-flex items-center gap-1.5 text-xs max-sm:mt-2 max-sm:ml-0 max-sm:flex"><input type="checkbox" checked={other} onChange={(e) => setOther(e.target.checked)} /> also check the same key on the other family (EVM ↔ TRON)</label>
          <Collapsible className="text-muted-foreground mt-1.5 text-xs">
            <CollapsibleTrigger className="flex items-center gap-1 text-left"><ShieldAlert className="size-3.5 shrink-0" />Live check sends the address to public RPC providers - show list</CollapsibleTrigger>
            <CollapsibleContent className="mt-1">The live check is opt-in, never runs automatically and <b>does send the address</b> to: <span className="mono">{(d.live.providers || []).join(", ")}</span>. The index lookup does not.</CollapsibleContent>
          </Collapsible>
        </div>
        <div className="text-muted-foreground mt-2 flex items-start gap-1.5 text-xs"><Lock className="mt-0.5 size-3.5 shrink-0" /><span>Addresses never leave your browser (index lookup runs locally; the saved list stays in localStorage). <span className="mono">#watch=addr1,addr2</span> in the URL pre-fills the list.</span></div>
      </Card>
      {results.length > 0 && (
        <div className="no-print mt-2.5 flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground">Results:</span>
          <Button variant="outline" size="xs" onClick={() => downloadNamed("watchlist", "csv", toCsv(watchRows(d, results)))}><Download />CSV</Button>
          <Button variant="outline" size="xs" onClick={() => downloadNamed("watchlist", "json", watchJson(d, results))}><Download />JSON</Button>
        </div>
      )}
      <div className="mt-3 text-sm font-semibold" aria-live="polite" data-testid="wl-summary">{summary}</div>
      <div>
        {loading && <Muted className="p-3.5">Loading address index…</Muted>}
        {!loading && results.length === 0 && <Muted className="p-3.5">Paste one or more addresses above.</Muted>}
        {!loading && results.map((r, i) => <AddrCard key={r.input} r={r} onLive={setLive(i)} />)}
      </div>
    </div>
  )
}
