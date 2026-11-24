import { useMemo, useRef } from "react"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { useApp } from "@/lib/app"
import { cshort, dur, med, mondayOf, NOW, orderIdx, num } from "@/lib/format"
import { chartColor } from "@/lib/charts"
import { useBarsRise, useDrawLine, useFlip, useStagger } from "@/lib/motion"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { DataTable, type Col } from "@/components/DataTable"
import { H2, Muted, Tip, INFO } from "@/components/common"
import type { CrossRow } from "@/lib/types"

const BL = ["0 s", "< 5 min", "5-30 min", "30-60 min", "1-3 h", "3-6 h", "6-24 h", "> 24 h"]
const bucketOf = (d: number) => (d === 0 ? 0 : d < 300 ? 1 : d < 1800 ? 2 : d < 3600 ? 3 : d < 10800 ? 4 : d < 21600 ? 5 : d < 86400 ? 6 : 7)

function Hero() {
  const { d } = useApp()
  const now = NOW()
  const eth = d.ex.filter((e) => e[0] === "eth" && e[2] >= 0 && now - e[1] <= 365 * 86400)
  const m = med(eth.map((e) => e[2]))
  const z = eth.length ? eth.filter((e) => e[2] === 0).length / eth.length : null
  const fz = d.ex.filter((e) => e[3] === 1 && now - e[1] <= 365 * 86400)
  const nch = new Set(fz.map((e) => e[0])).size
  const items: [string, string][] = [
    [dur(m), "median delay from proposal to execution on Ethereum, last 12 months: how long a proposal usually sits in the queue"],
    [z == null ? "n/a" : Math.round(z * 100) + "%", "of Ethereum executions land in the same block as the proposal, so nobody can see them coming"],
    [fz.length.toLocaleString("en-US"), "freezing actions in the last 12 months across " + nch + " chains and tokens (incl. USDC and Solana, which have no queue)"],
  ]
  return (
    <div className="my-4 grid grid-cols-3 gap-4 max-md:grid-cols-1">
      {items.map(([n, c]) => (
        <div key={c}><div className="text-5xl leading-none font-semibold tracking-tight max-sm:text-4xl">{n}</div><div className="text-muted-foreground mt-1.5 max-w-[34ch] text-[13px]">{c}</div></div>
      ))}
    </div>
  )
}

function Histogram() {
  const { d, active } = useApp()
  const ref = useRef<HTMLDivElement>(null)
  const { rows, series } = useMemo(() => {
    const data: Record<string, number[]> = {}, now = NOW()
    for (const e of d.ex) if (e[2] >= 0 && now - e[1] <= 365 * 86400 && active(e[0])) (data[e[0]] ||= []).push(e[2])
    const chains = Object.keys(data).filter((c) => data[c].length >= 5).sort((a, b) => orderIdx(a) - orderIdx(b))
    const series = chains.map((c, i) => {
      const cnt = new Array(8).fill(0)
      data[c].forEach((x) => cnt[bucketOf(x)]++)
      return { key: c, label: cshort(c), n: data[c].length, color: chartColor(i), pct: cnt.map((x) => +((x / data[c].length) * 100).toFixed(1)) }
    })
    return { series, rows: BL.map((b, bi) => ({ bucket: b, ...Object.fromEntries(series.map((s) => [s.key, s.pct[bi]])) })) }
  }, [d, active])
  const cfg: ChartConfig = Object.fromEntries(series.map((s) => [s.key, { label: s.label, color: s.color }]))
  useBarsRise(ref, [series.length])
  return (
    <Card>
      <CardContent className="max-sm:px-2">
        {series.length === 0 ? <Muted>Not enough data for the selected chains.</Muted> : (
          <>
            <div className="mb-2 flex flex-wrap gap-x-3.5 gap-y-1 text-xs">
              {series.map((s) => <span key={s.key} className="inline-flex items-center gap-1.5"><i className="inline-block size-2.5 rounded-[2px]" style={{ background: s.color }} />{s.label} <span className="text-muted-foreground">(n={s.n})</span></span>)}
            </div>
            <div ref={ref}>
              <ChartContainer config={cfg} className="aspect-auto h-64 max-sm:h-52">
                <BarChart data={rows} accessibilityLayer>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="bucket" tickLine={false} axisLine={false} interval={0} tick={{ fontSize: 10 }} />
                  <YAxis tickFormatter={(v) => v + "%"} tickLine={false} axisLine={false} width={40} />
                  <ChartTooltip content={<ChartTooltipContent formatter={(v) => v + "%"} />} />
                  {series.map((s) => <Bar key={s.key} dataKey={s.key} fill={"var(--color-" + s.key + ")"} radius={[2, 2, 0, 0]} isAnimationActive={false} />)}
                </BarChart>
              </ChartContainer>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Weekly() {
  const { d, active } = useApp()
  const ref = useRef<HTMLDivElement>(null)
  const rows = useMemo(() => {
    const W = 26, m0 = mondayOf(NOW()), ws: number[] = []
    for (let i = W - 1; i >= 0; i--) ws.push(m0 - i * 7 * 86400)
    const cnt = new Array(W).fill(0)
    for (const e of d.ex) { if (e[3] !== 1 || !active(e[0])) continue; const k = ws.indexOf(mondayOf(e[1])); if (k >= 0) cnt[k]++ }
    return ws.map((t, i) => ({ week: new Date(t * 1000).toISOString().slice(5, 10), freezes: cnt[i] }))
  }, [d, active])
  useDrawLine(ref, ".recharts-area-curve", [rows.length, JSON.stringify(rows.map((r) => r.freezes))])
  const cfg: ChartConfig = { freezes: { label: "Freezes", color: "var(--chart-1)" } }
  return (
    <Card>
      <CardHeader><CardTitle>Executed freezes per week (26 weeks)</CardTitle></CardHeader>
      <CardContent className="max-sm:px-2">
        <div ref={ref}>
          <ChartContainer config={cfg} className="aspect-auto h-56 max-sm:h-44">
            <AreaChart data={rows} accessibilityLayer>
              <defs>
                <linearGradient id="gFreezes" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-freezes)" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="var(--color-freezes)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="week" tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <Area type="monotone" dataKey="freezes" stroke="var(--color-freezes)" strokeWidth={2} fill="url(#gFreezes)" isAnimationActive={false} />
            </AreaChart>
          </ChartContainer>
        </div>
      </CardContent>
    </Card>
  )
}

function Mechanism() {
  const { d, active, filterKey } = useApp()
  const ref = useRef<HTMLDivElement>(null)
  useFlip(ref, filterKey)
  const lbl: Record<string, string> = { onchain: "on-chain pending", safe_api: "Safe API pending (not guaranteed)", executed_only: "executed only" }
  const cls: Record<string, string> = { onchain: "border-l-ok", safe_api: "border-l-warn", executed_only: "border-l-muted-foreground" }
  const chains = Object.keys(d.chains).sort((a, b) => orderIdx(a) - orderIdx(b)).filter(active)
  return (
    <Card>
      <CardHeader><CardTitle>Mechanism by chain</CardTitle></CardHeader>
      <CardContent>
        <div ref={ref} className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-2">
          {chains.map((c) => {
            const m = d.chains[c], cv = d.coverage?.[c], o = d.others?.[c]
            const nopub = cv && cv.freezes && m.family === "usdt0" ? " · " + cv.no_public + " of " + cv.freezes + " freezes had no public Safe proposal" : ""
            const extra = cv ? cv.proposals + " proposals, " + cv.executed + " executed" + nopub : o ? o.freezes + " freezes" + (o.coverage?.complete ? "" : ", partial history") : ""
            return (
              <div key={c} data-flip className={"bg-background rounded-lg border border-l-[3px] px-2.5 py-2 " + (cls[m.mech] || "")}>
                <b>{m.label}</b>
                <div className="text-muted-foreground text-[11px]">{lbl[m.mech] || m.mech} · {m.threshold || ""}</div>
                <div className="text-muted-foreground text-[11px]">{extra}</div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

function Story() {
  const { d } = useApp()
  const now = NOW()
  const eth = d.ex.filter((e) => e[0] === "eth" && e[2] >= 0)
  const mn = med(eth.filter((e) => now - e[1] <= 90 * 86400).map((e) => e[2]))
  const ma = med(eth.filter((e) => now - e[1] > 365 * 86400 && now - e[1] <= 455 * 86400).map((e) => e[2]))
  const parts: string[] = []
  if (mn != null) {
    let s = "Over the last 90 days the median delay on Ethereum is " + dur(mn)
    if (ma != null) { const p = Math.round((mn / ma - 1) * 100); s += "; a year ago, in the same window, it was " + dur(ma) + (Math.abs(p) < 5 ? " (no material change)" : p < 0 ? " - " + Math.abs(p) + "% faster now" : " - " + p + "% slower now") }
    parts.push(s + ".")
  }
  const tron = d.ex.filter((e) => e[0] === "tron" && e[2] >= 0 && now - e[1] <= 90 * 86400).map((e) => e[2])
  if (tron.length) parts.push("On TRON the median over the same period is " + dur(med(tron)) + " (execution time is inferred from confirmations).")
  const per: Record<string, number> = {}
  d.ex.filter((e) => e[3] === 1 && now - e[1] <= 90 * 86400).forEach((e) => { per[e[0]] = (per[e[0]] || 0) + 1 })
  const top = Object.entries(per).sort((a, b) => b[1] - a[1]).slice(0, 3)
  if (top.length) parts.push("Most freezes in 90 days: " + top.map(([c, n]) => (d.chains[c]?.label || c) + " (" + n + ")").join(", ") + ".")
  parts.push(d.pending.length ? d.pending.length + " live proposals are in the queues right now." : "No live proposals are in the queues right now.")
  return <p className="my-4 max-w-[72ch] text-base leading-relaxed">{parts.join(" ")}</p>
}

function Cross() {
  const { d, active } = useApp()
  const rows = d.cross.filter((r) => r.items.some((i) => active(i.chain))).slice(0, 60)
  const cols: Col<CrossRow>[] = [
    { h: "Address (EVM-normalized)", v: (r) => r.address, wide: true, className: "mono min-w-[260px]" },
    { h: "Chains", v: (r) => r.n_chains },
    { h: "Where and when", v: (r) => r.items.map((i) => cshort(i.chain) + " " + (i.ts || "").slice(0, 10) + " " + i.class).join("; "),
      r: (r) => <div className="flex flex-wrap gap-1">{r.items.map((i, k) => <Tip key={k} info={INFO[i.class] || ""} label={i.class + " (" + i.status + ")"}><Badge variant={i.class === "freeze" ? "danger" : i.class === "destroy" ? "warn" : "ok"}>{cshort(i.chain)} {(i.ts || "").slice(0, 10)}</Badge></Tip>)}</div> },
  ]
  return (
    <>
      <H2>Cross-chain: addresses on several chains</H2>
      <DataTable cols={cols} rows={rows} exportName="cross_chain" empty="No addresses on several chains." />
      <Muted className="mt-1">Addresses on 2+ chains: {num(d.cross_total)}. TRON addresses are matched to EVM by the shared 20-byte hex.</Muted>
    </>
  )
}

export default function LiveReport() {
  const root = useRef<HTMLElement>(null)
  useStagger(root as React.RefObject<HTMLElement>, "[data-slot=card]", [], 6)
  return (
    <section ref={root} className="mt-8 border-t pt-2">
      <Hero />
      <H2>How much time is there? Delay from proposal to execution (last 12 months)</H2>
      <Histogram />
      <div className="mt-4 grid grid-cols-2 gap-4 max-md:grid-cols-1">
        <Weekly />
        <Mechanism />
      </div>
      <Story />
      <Cross />
    </section>
  )
}
