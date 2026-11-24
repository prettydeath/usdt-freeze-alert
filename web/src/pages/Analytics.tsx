import { useEffect, useMemo, useRef, useState } from "react"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import { useApp, chainsSorted } from "@/lib/app"
import { DATA_BASE, loadAnalytics } from "@/lib/data"
import type { Analytics as AN, Cluster, Pulse } from "@/lib/types"
import { cshort, dday, dur, med, NOW, NO_DELAY, num, usd, usdS } from "@/lib/format"
import { chartColor } from "@/lib/charts"
import { addDays, kyivDay, kyivDayStart, kyivHour } from "@/lib/kyiv"
import { takeClusterFocus, openGlossary } from "@/lib/focus"
import { useBarsRise, useDrawLine } from "@/lib/motion"
import { addrHref } from "@/lib/router"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Approx, AddrLink, H2, Kpi, KpiGrid, Muted, Seg } from "@/components/common"
import { LabelTag, REASON_ICON } from "@/components/chips"
import { DataTable, type Col } from "@/components/DataTable"

type Period = "today" | "7d" | "30d" | "90d" | "12m" | "all"
const PERIODS: Period[] = ["today", "7d", "30d", "90d", "12m", "all"]
type Token = "all" | "USDT" | "USDT0" | "USDC"
const lsGet = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* storage unavailable */ } }

export default function Analytics() {
  const { d } = useApp()
  const [AN, setAN] = useState<AN | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => { loadAnalytics(d).then((a) => (a ? setAN(a) : setFailed(true))) }, [d])
  if (failed) return <div className="text-muted-foreground p-4">Analytics data unavailable.</div>
  if (!AN) return <div className="text-muted-foreground p-4">Loading analytics…</div>
  return <AnalyticsBody AN={AN} />
}

function AnalyticsBody({ AN }: { AN: AN }) {
  const { active } = useApp()
  const [period, setPeriodS] = useState<Period>(() => { const p = lsGet("ufa.period"); return PERIODS.includes(p as Period) ? (p as Period) : "12m" })
  const [token, setTokenS] = useState<Token>(() => { const p = lsGet("ufa.token"); return p === "all" || p === "USDT" || p === "USDT0" || p === "USDC" ? p : "all" })
  const setPeriod = (p: Period) => { setPeriodS(p); lsSet("ufa.period", p) }
  const setToken = (p: Token) => { setTokenS(p); lsSet("ufa.token", p) }
  const now = NOW()
  // Today / 7d are Kyiv calendar days (summed from the per-day cells); the others are UTC months as before
  const fine = (period === "today" || period === "7d") && !!AN.daily
  const kd = useMemo(() => { const t = kyivDay(Date.now()); return { today: t, days: period === "today" ? [t] : Array.from({ length: 7 }, (_, i) => addDays(t, i - 6)) } }, [period])
  const since = useMemo(() => { const days = { today: 0, "7d": 0, "30d": 30, "90d": 90, "12m": 365, all: 0 }[period]; return days ? new Date((now - days * 86400) * 1000).toISOString().slice(0, 7) : null }, [period, now])
  const sinceEp = fine ? kyivDayStart(kd.days[0]) : since ? Date.parse(since + "-01T00:00:00Z") / 1000 : 0
  const keep = (k: string) => (fine ? kd.days.includes(k) : !since || k >= since)
  const chOk = (c: string) => active(c) && (token === "all" || (AN.tokens[c] || "USDT") === token)

  const sum = (chain?: string) => {
    const tot: Record<string, number> = Object.fromEntries(AN.idx.map((k) => [k, 0]))
    for (const [c, months] of Object.entries(fine ? AN.daily! : AN.monthly)) {
      if (!chOk(c) || (chain && c !== chain)) continue
      for (const [mo, v] of Object.entries(months)) { if (!keep(mo)) continue; AN.idx.forEach((k, i) => { tot[k] += v[i] || 0 }) }
    }
    return tot
  }
  const s = sum()
  const inf = [0, 0, 0, 0, 0, 0]
  for (const [c, v] of Object.entries(AN.inflow?.periods[period] || {})) if (chOk(c)) v.forEach((x, i) => { inf[i] += x })
  const sup = { i: 0, r: 0, ni: 0, nr: 0 }
  for (const [c, months] of Object.entries((fine ? AN.supply_daily : AN.supply) || {})) { if (!chOk(c)) continue; for (const [mo, v] of Object.entries(months)) { if (!keep(mo)) continue; sup.i += v[0]; sup.r += v[1]; sup.ni += v[2]; sup.nr += v[3] } }
  const cov = AN.coverage, ic = AN.inflow?.coverage, ex = AN.inflow?.excluded

  return (
    <div>
      <div className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-muted-foreground">Period</span>
        <Seg label="Period" value={period} onChange={setPeriod} items={[["today", "Today"], ["7d", "7d"], ["30d", "30d"], ["90d", "90d"], ["12m", "12m"], ["all", "All"]]} />
        {fine && <span className="text-muted-foreground text-xs">{period === "today" ? "since 00:00 Kyiv time" : "last 7 Kyiv days incl. today"}</span>}
        <span className="text-muted-foreground">Token</span>
        <Seg label="Token" value={token} onChange={setToken} items={[["all", "All"], ["USDT", "USDT"], ["USDT0", "USDT0"], ["USDC", "USDC"]]} />
      </div>
      <KpiGrid className="mt-3.5 grid-cols-4 max-md:grid-cols-2">
        <Kpi icon="🧊" label="Frozen" tag={<Approx tip="Burned amount if later destroyed, else current balance of the frozen address (frozen funds cannot move). Unknown = balance not read yet." />}
          num={s.frozen_usd} fmt={usdS} sub={num(s.frozen_n) + " freezes" + (s.frozen_unk ? " · +" + num(s.frozen_unk) + " unknown" : "")} />
        <Kpi icon="🔥" label="Burned" num={s.burned_usd} fmt={usdS} sub={num(s.burned_n) + " destroys" + (s.burned_unk ? " · " + s.burned_unk + " amount unknown" : "")} />
        <Kpi icon="💸" label="Sent after freeze" tag={<Approx tip="Transfers to an address while it was blacklisted (zero address and token contracts excluded). Only the scanned chains are included." />}
          num={inf[3]} fmt={usdS} sub={usdS(inf[5]) + " later burned · " + num(inf[0]) + " addresses (" + num(inf[4]) + " burned) · " + num(inf[1]) + " senders"} />
        <Kpi icon="🟢" label="Unfrozen" tag={<Approx tip="Balance last observed while the address was frozen; the exact amount at unfreeze is not recorded on chain." />}
          value={s.unfrozen_usd > 0 ? usdS(s.unfrozen_usd) + " · " + s.unfrozen_n + " addr." : s.unfrozen_n + " addresses"}
          sub={s.unfrozen_unk && s.unfrozen_usd > 0 ? s.unfrozen_unk + " without known balance" : "balance when unfrozen is rarely known"} />
      </KpiGrid>
      <Muted className="mt-1.5">Reissue to victims happens off-chain / through treasury transfers and is not tracked.</Muted>
      <Muted>🏦 Treasury supply operations (separate from freezes): issued/minted {usdS(sup.i)} in {sup.ni} operations, redeemed/burned {usdS(sup.r)} in {sup.nr}.</Muted>
      {AN.unique && <Muted>All time: {num(AN.unique.freezes)} freezes on {num(AN.unique.addresses)} unique addresses (an address frozen on several chains counts once here, once per chain in the tables).</Muted>}
      {ic && <Muted>Transfers to frozen addresses: {num(ic[0])} of {num(ic[1])} addresses scanned (Ethereum, USDC, Celo, Avalanche, TRON; other chains not covered yet).</Muted>}
      {ex && <Muted>Excluded as mistaken sends to the zero / token-contract addresses: {usd(ex.usd)} in {num(ex.n)} transfers.</Muted>}
      <Muted>{cov.checked < cov.total ? "Balances checked for " + num(cov.checked) + " of " + num(cov.total) + ' frozen addresses - "still frozen" and "frozen $" are partial until the check completes.' : "Balances checked for all " + num(cov.total) + " frozen addresses."}</Muted>

      <H2>Lifecycle</H2>
      <Funnel s={s} />
      <Muted className="mt-1">{num(s.never_n)} proposals never executed (stale / revoked / failed) · {usd(s.never_usd)} still on those addresses</Muted>

      <H2>{!fine ? "Money per month" : period === "today" ? "Money per hour" : "Money per day (Kyiv days)"}</H2>
      <Monthly AN={AN} since={since} chOk={chOk} fine={fine ? (period as "today" | "7d") : null} days={kd.days} />

      <H2>By chain</H2>
      <ByChain AN={AN} sum={sum} chOk={chOk} sinceEp={sinceEp} />

      <div className="mt-4 grid grid-cols-2 gap-4 max-md:grid-cols-1">
        <Card><CardHeader><CardTitle>Top 20 frozen addresses by balance</CardTitle></CardHeader><CardContent><TopFrozen AN={AN} chOk={chOk} sinceEp={sinceEp} /></CardContent></Card>
        <Card><CardHeader><CardTitle>Freezes by chain</CardTitle></CardHeader><CardContent><Share AN={AN} chOk={chOk} sum={sum} /></CardContent></Card>
      </div>

      <H2>Funds sent to already-frozen addresses</H2>
      <Inflow AN={AN} period={period} chOk={chOk} />

      <Reasons period={fine ? period : null} />
      <Outflows period={fine ? period : null} />
      <CrossLag sinceEp={fine ? sinceEp : 0} />
      <PulseSection sinceEp={fine ? sinceEp : 0} />
      <Clusters />
      <div className="mt-5"><Button variant="outline" onClick={openGlossary}>Glossary</Button></div>
      <Muted className="mt-3.5">Sent after freeze = token transfers whose recipient was blacklisted at the time; those funds are locked at once and are typically burned at the next destroy. Burned = amount in the DestroyedBlackFunds event. Still frozen = current balance of blacklisted addresses (frozen funds cannot move), refreshed incrementally. Frozen $ = burned amount if later destroyed, else current balance, else last balance seen while frozen. Raw aggregates: <a href={DATA_BASE + "analytics.json"}>analytics.json</a>.</Muted>
    </div>
  )
}

function Funnel({ s }: { s: Record<string, number> }) {
  const step = (n: string, l: string, v?: string, sub?: string) => (
    <div className="bg-card min-w-[120px] rounded-xl border px-3 py-2 max-sm:min-w-0 max-sm:flex-1">
      <div className="text-xl font-semibold tabular-nums">{n}</div>
      <div className="text-muted-foreground text-[11px] tracking-wide uppercase">{l}</div>
      {v && <div className="font-semibold">{v}</div>}
      {sub && <div className="text-muted-foreground text-[11px]">{sub}</div>}
    </div>
  )
  const arrow = <span className="text-muted-foreground self-center text-lg max-sm:hidden" aria-hidden="true">→</span>
  return (
    <div className="flex flex-wrap items-start gap-2">
      {step(num(s.proposed_n), "Proposed", undefined, "queue chains + executed-only freezes")}{arrow}
      {step(num(s.frozen_n), "Frozen", usdS(s.frozen_usd))}{arrow}
      {step(num(s.still_n), "Still frozen", usdS(s.still_usd), "balance now")}{arrow}
      <div className="flex flex-col gap-1.5">{step(num(s.burned_n), "Burned", usdS(s.burned_usd))}{step(num(s.unfrozen_n), "Unfrozen", s.unfrozen_usd > 0 ? usdS(s.unfrozen_usd) : undefined)}</div>
    </div>
  )
}

function Monthly({ AN, since, chOk, fine, days }: { AN: AN; since: string | null; chOk: (c: string) => boolean; fine: "today" | "7d" | null; days: string[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const { rows, cut } = useMemo(() => {
    const agg: Record<string, number[]> = {}
    const src = fine === "today" ? AN.hourly : fine ? AN.daily : AN.monthly
    for (const [c, months] of Object.entries(src || {})) {
      if (!chOk(c)) continue
      for (const [mo, v] of Object.entries(months)) {
        if (fine === "today" ? mo.slice(0, 10) !== days[0] : fine ? !days.includes(mo) : since && mo < since) continue
        const a = (agg[mo] ||= [0, 0, 0]); a[0] += v[1] || 0; a[1] += v[4] || 0; a[2] += v[7] || 0
      }
    }
    if (fine === "today") { const hn = kyivHour(Date.now()); for (let h = 0; h <= hn; h++) agg[days[0] + "T" + String(h).padStart(2, "0")] ||= [0, 0, 0] }   // empty hours stay visible
    else if (fine) for (const dd of days) agg[dd] ||= [0, 0, 0]
    let ms = Object.keys(agg).sort(), cut = ""
    if (ms.length > 48) { ms = ms.slice(-48); cut = " (last 48 months shown)" }
    return { cut, rows: ms.map((m) => ({ month: fine === "today" ? m.slice(11) + ":00" : fine ? m.slice(5) : m, frozen: agg[m][0], burned: agg[m][1], unfrozen: agg[m][2] })) }
  }, [AN, since, chOk, fine, days])
  const cfg: ChartConfig = { frozen: { label: "Frozen $", color: "var(--chart-1)" }, burned: { label: "Burned $", color: "var(--chart-4)" }, unfrozen: { label: "Unfrozen $ (known only)", color: "var(--chart-3)" } }
  useBarsRise(ref, [rows.length, since, fine])
  return (
    <Card><CardContent className="max-sm:px-2">
      <div className="mb-2 flex flex-wrap gap-x-3.5 gap-y-1 text-xs">
        {Object.entries(cfg).map(([k, v]) => <span key={k} className="inline-flex items-center gap-1.5"><i className="inline-block size-2.5 rounded-[2px]" style={{ background: v.color }} />{v.label}</span>)}
        <span className="text-muted-foreground">{cut}</span>
      </div>
      <div ref={ref}>
        <ChartContainer config={cfg} className="aspect-auto h-64 max-sm:h-52">
          <BarChart data={rows} accessibilityLayer>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="month" tickLine={false} axisLine={false} minTickGap={16} />
            <YAxis tickFormatter={(v) => usdS(v)} tickLine={false} axisLine={false} width={56} />
            <ChartTooltip content={<ChartTooltipContent formatter={(v) => usd(v)} />} />
            <Bar dataKey="frozen" fill="var(--color-frozen)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
            <Bar dataKey="burned" fill="var(--color-burned)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
            <Bar dataKey="unfrozen" fill="var(--color-unfrozen)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ChartContainer>
      </div>
    </CardContent></Card>
  )
}

type ChainRow = { chain: string; median: number | null } & Record<string, number | string | null>
function ByChain({ AN, sum, chOk, sinceEp }: { AN: AN; sum: (c?: string) => Record<string, number>; chOk: (c: string) => boolean; sinceEp: number }) {
  const { d } = useApp()
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: "frozen_usd", dir: -1 })
  const rows = useMemo(() => {
    const out: ChainRow[] = []
    for (const c of Object.keys(AN.monthly)) {
      if (!chOk(c)) continue
      const s = sum(c)
      if (!s.frozen_n && !s.burned_n && !s.unfrozen_n) continue
      const x = d.ex.filter((e) => e[0] === c && e[2] >= 0 && e[1] >= sinceEp && !NO_DELAY.includes(c)).map((e) => e[2])
      out.push({ chain: c, ...s, median: x.length ? med(x) : null })
    }
    const { key, dir } = sort
    out.sort((a, b) => (key === "chain" ? dir * cshort(a.chain).localeCompare(cshort(b.chain)) : dir * (((a[key] as number) ?? -1) - ((b[key] as number) ?? -1))))
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [AN, d, sort, sinceEp, chOk])
  const n = (r: ChainRow, k: string) => r[k] as number
  const cols: Col<ChainRow>[] = [
    { h: "Chain", sortKey: "chain", v: (r) => cshort(r.chain), r: (r) => <b>{cshort(r.chain)}</b> },
    { h: "Frozen #", sortKey: "frozen_n", v: (r) => n(r, "frozen_n"), r: (r) => num(n(r, "frozen_n")) },
    { h: "Frozen $", sortKey: "frozen_usd", v: (r) => n(r, "frozen_usd"), r: (r) => <>{usdS(n(r, "frozen_usd"))}{n(r, "frozen_unk") ? <span className="text-muted-foreground"> +{n(r, "frozen_unk")}?</span> : null}</> },
    { h: "Burned $", sortKey: "burned_usd", v: (r) => n(r, "burned_usd"), r: (r) => (n(r, "burned_usd") ? usdS(n(r, "burned_usd")) : "-") },
    { h: "Unfrozen $", sortKey: "unfrozen_usd", v: (r) => n(r, "unfrozen_usd"), r: (r) => (n(r, "unfrozen_usd") ? usdS(n(r, "unfrozen_usd")) : <span className="text-muted-foreground">{n(r, "unfrozen_n")} addr.</span>) },
    { h: "Still frozen $", sortKey: "still_usd", v: (r) => n(r, "still_usd"), r: (r) => usdS(n(r, "still_usd")) },
    { h: "Median delay", sortKey: "median", v: (r) => r.median, r: (r) => (r.median == null ? <span className="text-muted-foreground">n/a</span> : dur(r.median)) },
  ]
  return <DataTable cols={cols} rows={rows} exportName="analytics_by_chain" rowKey={(r) => r.chain} sort={sort}
    onSort={(k) => setSort((s) => (s.key === k ? { key: k, dir: (-s.dir) as 1 | -1 } : { key: k, dir: k === "chain" ? 1 : -1 }))} empty="No data for this selection." />
}

function TopFrozen({ AN, chOk, sinceEp }: { AN: AN; chOk: (c: string) => boolean; sinceEp: number }) {
  const rows = AN.top.filter((r) => chOk(r[0]) && r[3] >= sinceEp).sort((a, b) => b[2] - a[2]).slice(0, 20)
  const cols: Col<(typeof rows)[number]>[] = [
    { h: "Chain", v: (r) => cshort(r[0]), r: (r) => <b>{cshort(r[0])}</b> },
    { h: "Address", v: (r) => r[1], wide: true, r: (r) => <AddrLink chain={r[0]} a={r[1]} /> },
    { h: "Frozen now", v: (r) => r[2], r: (r) => usd(r[2]) },
    { h: "Since", v: (r) => dday(r[3]), className: "mono" },
  ]
  return <DataTable cols={cols} rows={rows} exportName="top_frozen" rowKey={(r) => r[0] + r[1]} empty="No balances known yet for this selection." />
}

function Share({ AN, chOk, sum }: { AN: AN; chOk: (c: string) => boolean; sum: (c?: string) => Record<string, number> }) {
  const per: [string, number][] = []
  let tot = 0
  for (const c of Object.keys(AN.monthly)) { if (!chOk(c)) continue; const n = sum(c).frozen_n; if (n) { per.push([c, n]); tot += n } }
  per.sort((a, b) => b[1] - a[1])
  return (
    <>
      <div className="my-2 flex h-5 overflow-hidden rounded-md border" role="img" aria-label="Share of freezes by chain">
        {per.map(([c, n], i) => <span key={c} title={cshort(c) + ": " + num(n)} style={{ flex: n, background: chartColor(i), minWidth: 2 }} />)}
      </div>
      <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-xs">
        {per.slice(0, 12).map(([c, n], i) => <span key={c} className="inline-flex items-center gap-1.5"><i className="inline-block size-2.5 rounded-[2px]" style={{ background: chartColor(i) }} />{cshort(c)} {Math.round((n / tot) * 100)}% <span className="text-muted-foreground">({num(n)})</span></span>)}
      </div>
    </>
  )
}

function Inflow({ AN, period, chOk }: { AN: AN; period: string; chOk: (c: string) => boolean }) {
  const I = AN.inflow
  if (!I) return null
  const snd = (I.senders[period] || []).filter((r) => chOk(r[0])).slice(0, 15)
  const rcv = (I.receivers[period] || []).filter((r) => chOk(r[0])).slice(0, 15)
  const c1: Col<(typeof snd)[number]>[] = [
    { h: "Chain", v: (r) => cshort(r[0]), r: (r) => <b>{cshort(r[0])}</b> },
    { h: "Sender", v: (r) => r[1], wide: true, r: (r) => <AddrLink chain={r[0]} a={r[1]} /> },
    { h: "Transfers", v: (r) => r[2], r: (r) => num(r[2]) },
    { h: "Sent", v: (r) => r[3], r: (r) => usd(r[3]) },
  ]
  const c2: Col<(typeof rcv)[number]>[] = [
    { h: "Chain", v: (r) => cshort(r[0]), r: (r) => <b>{cshort(r[0])}</b> },
    { h: "Frozen address", v: (r) => r[1], wide: true, r: (r) => <AddrLink chain={r[0]} a={r[1]} /> },
    { h: "Transfers", v: (r) => r[2], r: (r) => num(r[2]) },
    { h: "Received", v: (r) => r[3], r: (r) => usd(r[3]) },
    { h: "Last", v: (r) => dday(r[4]), className: "mono" },
  ]
  return (
    <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
      <Card><CardHeader><CardTitle>Top senders (often exchanges and bridges)</CardTitle></CardHeader><CardContent><DataTable cols={c1} rows={snd} exportName="inflow_senders" rowKey={(r) => r[0] + r[1]} empty="No transfers found yet for this selection." /></CardContent></Card>
      <Card><CardHeader><CardTitle>Frozen addresses still receiving funds</CardTitle></CardHeader><CardContent><DataTable cols={c2} rows={rcv} exportName="frozen_addresses_receiving" rowKey={(r) => r[0] + r[1]} empty="No transfers found yet for this selection." /></CardContent></Card>
    </div>
  )
}

function Reasons({ period }: { period: "today" | "7d" | null }) {
  const { d } = useApp()
  const W = period ? d.windows?.[period] : null
  const R = W ? W.reasons : d.reasons
  if (!R) return null
  const cats = Object.entries(R.by_category)
  type RRow = { c: string; n: number; usd: number }
  const rows: RRow[] = [...cats.map(([c, v]) => ({ c, n: v.n, usd: v.usd })), { c: "unknown", n: R.unknown, usd: R.unknown_usd }]
  const rc: Col<RRow>[] = [
    { h: "Reason", v: (r) => r.c, r: (r) => <>{r.c === "unknown" ? "❔" : REASON_ICON[r.c] || ""} {r.c}</> },
    { h: "Freezes", v: (r) => r.n, r: (r) => num(r.n) },
    { h: "Frozen", v: (r) => r.usd, r: (r) => usd(r.usd) },
  ]
  const oc: Col<(typeof R.ofac.top)[number]>[] = [
    { h: "Chain", v: (x) => cshort(x.chain), r: (x) => <b>{cshort(x.chain)}</b> },
    { h: "Address", v: (x) => x.address, wide: true, r: (x) => <><AddrLink chain={x.chain} a={x.address} /> <a href={addrHref(x.address)}>page</a></> },
    { h: "Frozen", v: (x) => x.usd, r: (x) => usd(x.usd) },
    { h: "Source", v: (x) => x.url ?? "", r: (x) => (x.url ? <a href={x.url} target="_blank" rel="noopener noreferrer">source ↗</a> : null) },
  ]
  return (
    <>
      <H2>Freezes by reason{W ? (period === "today" ? " (today)" : " (last 7 days)") : ""}</H2>
      <Muted>A reason is known only from a curated entry with a public source or from a wallet-attribution label (hack / scam / ransomware / sanctions ...). Everything else is "unknown": most freezes are never explained publicly.{R.curated ? "" : " No curated entries yet (config/reasons.yaml)."}</Muted>
      <KpiGrid className="mt-2 grid-cols-3 max-md:grid-cols-1">
        <Kpi icon="🧾" label="Known reason" value={R.known_pct == null ? "n/a" : R.known_pct + "%"} sub={num(R.known) + " of " + num(R.freezes) + " freezes (" + num(R.known_unique) + " of " + num(R.unique) + " unique addresses) · " + usdS(R.known_usd)} />
        <Kpi icon="❔" label="Unknown" num={R.unknown} sub={"freezes with no public reason · " + usdS(R.unknown_usd)} />
        <Kpi icon="⚖" label="OFAC SDN" value={num(R.ofac.unique) + " addresses"} sub={num(R.ofac.n) + " freezes · " + usdS(R.ofac.usd) + " frozen on OFAC-listed addresses" + (R.sanctioned_other ? " · +" + R.sanctioned_other + ' other "sanctioned" labels' : "")} />
      </KpiGrid>
      <div className="mt-3 grid grid-cols-2 gap-4 max-md:grid-cols-1">
        <Card><CardHeader><CardTitle>By reason category</CardTitle></CardHeader><CardContent><DataTable cols={rc} rows={rows} exportName="freezes_by_reason" rowKey={(r) => r.c} /></CardContent></Card>
        <Card><CardHeader><CardTitle>⚖ OFAC SDN: largest frozen addresses</CardTitle></CardHeader><CardContent><DataTable cols={oc} rows={R.ofac.top} exportName="ofac_frozen_top" rowKey={(x) => x.chain + x.address} empty="None." /></CardContent></Card>
      </div>
    </>
  )
}

function Outflows({ period }: { period: "today" | "7d" | null }) {
  const { d } = useApp()
  const W = period ? d.windows?.[period] : null
  const O = W ? W.outflows : d.outflows
  if (!O) return null
  const pct = O.moved_12m + O.frozen_12m > 0 ? ((100 * O.moved_12m) / (O.moved_12m + O.frozen_12m)).toFixed(1) + "% of escaped + frozen (" + usdS(O.frozen_12m) + " frozen)" : "amount frozen unknown"
  const ec: Col<(typeof O.top_entities)[number]>[] = [
    { h: "Entity", v: (e) => e.entity, r: (e) => <b>{e.entity}</b> },
    { h: "Category", v: (e) => e.category },
    { h: "Received", v: (e) => e.usd, r: (e) => usd(e.usd) },
    { h: "Transfers", v: (e) => e.n },
    { h: "Source", v: (e) => e.url ?? "", r: (e) => (e.url ? <a href={e.url} target="_blank" rel="noopener noreferrer">wallet-attribution ↗</a> : null) },
  ]
  const fc: Col<(typeof O.frontrun)[number]>[] = [
    { h: "Chain", v: (f) => cshort(f.chain), r: (f) => <b>{cshort(f.chain)}</b> },
    { h: "Address", v: (f) => f.address, wide: true, r: (f) => <><AddrLink chain={f.chain} a={f.address} />{f.label && <> <Badge variant="warn" title="label from wallet-attribution">{f.label}</Badge></>}</> },
    { h: "Frozen (UTC)", v: (f) => f.freeze_ts, className: "mono", r: (f) => f.freeze_ts.replace("T", " ").replace("Z", "") },
    { h: "Escaped in window", v: (f) => f.window_out, r: (f) => usd(f.window_out) },
    { h: "Frozen", v: (f) => f.frozen_usd, r: (f) => usd(f.frozen_usd) },
    { h: "Share", v: (f) => Math.round(100 * f.share) + "%" },
  ]
  return (
    <>
      <H2>Moved out before the freeze</H2>
      <Muted>{W ? (period === "today" ? "Freezes executed today" : "Freezes executed in the last 7 days") : "Last 12 months"}, {num(O.checked || 0)} executed freezes checked so far (Ethereum, USDC, Celo, Avalanche, TRON; the scan is budgeted per run and continues). Window = 72 h before execution. "Escaped" excludes transfers to addresses that were (or became) frozen too and moves inside one cluster{O.truncated ? "; " + O.truncated + " freezes with very many transfers are marked truncated and never counted as front-run" : ""}{O.service_n ? ". " + O.service_n + " labelled exchange / service wallets (" + usdS(O.service_12m) + " of routine transfers) are reported apart and not counted" : ""}.</Muted>
      <KpiGrid className="mt-2">
        <Kpi icon="📤" label="Escaped, 72 h before" num={O.moved_12m} fmt={usdS} sub={pct + " · " + O.with_out + " of " + O.checked + " freezes had outgoing transfers"} />
        <Kpi icon="🔁" label="Moved between frozen addresses" num={O.internal_12m} fmt={usdS} sub="not an escape: the recipients were also frozen, or the same entity or cluster (kept out of the totals)" />
        <Kpi icon="⏱" label="Escaped in the window" num={O.window_12m} fmt={usdS} sub="between proposal and execution" />
        <Kpi icon="🏃" label="Front-run" num={O.n_frontrun} sub="escaped in-window outflow was at least half of (outflow + frozen); truncated freezes excluded" />
      </KpiGrid>
      <OutflowExtras O={O} />
      <div className="mt-3 grid grid-cols-2 gap-4 max-md:grid-cols-1">
        <Card><CardHeader><CardTitle>Top recipient entities</CardTitle></CardHeader><CardContent><DataTable cols={ec} rows={O.top_entities} exportName="top_recipient_entities" rowKey={(e) => e.entity} empty="No labelled recipients yet." /></CardContent></Card>
        <Card><CardHeader><CardTitle>Front-run in the window</CardTitle><Muted>Shown: the 25 largest.</Muted></CardHeader><CardContent><DataTable cols={fc} rows={O.frontrun} exportName="front_run" rowKey={(f) => f.chain + f.address + f.freeze_ts} empty="None yet." /></CardContent></Card>
      </div>
    </>
  )
}

function CrossLag({ sinceEp }: { sinceEp: number }) {
  const { d, active } = useApp()
  const S = d.signals
  if (!S) return null
  const st = S.stats
  const rows = S.history.filter((s) => active(s.frozen_on) && (!sinceEp || Date.parse(s.frozen_at) / 1000 >= sinceEp)).slice(0, 40).flatMap((s) => s.resolutions.map((x) => ({ s, x })))
  const cols: Col<(typeof rows)[number]>[] = [
    { h: "Address", v: (r) => r.s.show, wide: true, r: (r) => <AddrLink chain={r.s.frozen_on} a={r.s.show} /> },
    { h: "First frozen on", v: (r) => cshort(r.s.frozen_on), r: (r) => <b>{cshort(r.s.frozen_on)}</b> },
    { h: "Then blocked on", v: (r) => cshort(r.x.chain), r: (r) => <b>{cshort(r.x.chain)}</b> },
    { h: "Lag", v: (r) => r.x.lag_s ?? "n/a", r: (r) => (r.x.lag_s == null ? "n/a" : dur(r.x.lag_s)) },
    { h: "Funds", v: (r) => (r.x.moved_before ? "moved first " : "") + usd(r.x.balance_before) + " -> " + usd(r.x.balance_at_block), r: (r) => <>{r.x.moved_before && <span className="text-warn">moved first </span>}{usd(r.x.balance_before)} → {usd(r.x.balance_at_block)}</> },
  ]
  return (
    <>
      <H2>Cross-chain lag</H2>
      <KpiGrid className="grid-cols-3 max-md:grid-cols-1">
        <Kpi icon="🔗" label="Signals" num={st.signals} sub={st.open + " open · " + usd(st.open_usd) + " still unblocked elsewhere"} />
        <Kpi icon="⏳" label="Median lag" value={st.median_lag_s == null ? "n/a" : dur(st.median_lag_s)} sub={"first freeze to the next chain blocking it (" + st.resolved_chains + " cases)"} />
        <Kpi icon="🏃" label="Moved before" value={st.moved_before_pct == null ? "n/a" : st.moved_before_pct + "%"} sub={st.moved_balance_pct == null ? "of cases" : st.moved_balance_pct + "% of the balance had left"} />
      </KpiGrid>
      <div className="mt-2.5"><DataTable cols={cols} rows={rows} exportName="cross_chain_lag" rowKey={(r, i) => r.s.show + r.x.chain + i} empty="No resolved signals yet." /></div>
    </>
  )
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
const groupOf = (d: ReturnType<typeof useApp>["d"], c: string) => (c === "eth" ? "ETH" : c === "tron" ? "TRON" : d.chains[c]?.family === "usdt0" ? "USDT0" : "Other")

function PulseSection({ sinceEp }: { sinceEp: number }) {
  const { d, active } = useApp()
  const P = d.pulse
  const [grp, setGrp] = useState("all")
  const [chain, setChain] = useState<string | null>(null)
  const speedRef = useRef<HTMLDivElement>(null), queueRef = useRef<HTMLDivElement>(null)
  const heat = useMemo(() => {
    const grid = Array.from({ length: 7 }, () => Array(24).fill(0) as number[])
    let total = 0, mx = 0
    for (const e of d.ex) { if (!active(e[0]) || e[1] < sinceEp || (grp !== "all" && groupOf(d, e[0]) !== grp)) continue; const t = new Date(e[1] * 1000); grid[(t.getUTCDay() + 6) % 7][t.getUTCHours()]++; total++ }
    grid.forEach((r) => r.forEach((v) => { mx = Math.max(mx, v) }))
    const hours = Array(24).fill(0), wd = Array(7).fill(0)
    grid.forEach((r, di) => r.forEach((v, hi) => { hours[hi] += v; wd[di] += v }))
    return { grid, total, mx, topH: hours.indexOf(Math.max(...hours)), topD: wd.indexOf(Math.max(...wd)), weekend: total ? Math.round((100 * (wd[5] + wd[6])) / total) : 0 }
  }, [d, active, grp, sinceEp])
  const speed = useMemo(() => {
    if (!P) return { chains: [] as string[], rows: [] as Record<string, any>[] }
    const chains = Object.keys(P.speed).filter(active).sort((a, b) => Object.keys(P.speed[b]).length - Object.keys(P.speed[a]).length).slice(0, 5)
    const months = [...new Set(chains.flatMap((c) => Object.keys(P.speed[c])))].sort()
    return { chains, rows: months.map((m) => ({ month: m, ...Object.fromEntries(chains.map((c) => [c, P.speed[c][m] != null ? P.speed[c][m] / 60 : null])) })) }
  }, [P, active])
  const queue = useMemo(() => (P ? P.queue.map((q) => ({ day: new Date(q[0] * 1000).toISOString().slice(5, 10), open: q[1] })) : []), [P])
  useDrawLine(speedRef, ".recharts-line-curve", [speed.rows.length, speed.chains.join()])
  useDrawLine(queueRef, ".recharts-area-curve", [queue.length])
  if (!P) return null
  const sc = chainsSorted(Object.keys(P.signers))
  const cur = chain && P.signers[chain] ? chain : sc.includes("eth") ? "eth" : sc[0]
  const mlist: string[] = []
  const nw = new Date()
  for (let i = 11; i >= 0; i--) mlist.push(new Date(Date.UTC(nw.getUTCFullYear(), nw.getUTCMonth() - i, 1)).toISOString().slice(0, 7))
  const spark = (months: Record<string, number>) => { const v = mlist.map((m) => months[m] || 0), mx = Math.max(1, ...v); return v.map((x) => "▁▂▃▄▅▆▇█"[Math.min(7, Math.round((7 * x) / mx))]).join("") }
  const sg = P.signers[cur] || []
  const speedCfg: ChartConfig = Object.fromEntries(speed.chains.map((c, i) => [c, { label: cshort(c), color: chartColor(i) }]))
  const queueCfg: ChartConfig = { open: { label: "open proposals", color: "var(--chart-2)" } }
  const cols: Col<Pulse["signers"][string][number]>[] = [
    { h: "Signer", v: (s) => s.signer, wide: true, r: (s) => <AddrLink chain={cur} a={s.signer} /> },
    { h: "Confirmations", v: (s) => s.n, r: (s) => num(s.n) },
    { h: "Last 12 months", v: (s) => spark(s.months), r: (s) => <span className="text-primary font-mono tracking-[-1px]" title="monthly confirmations, oldest to newest">{spark(s.months)}</span> },
    { h: "Median time to confirm", v: (s) => s.median_confirm_s == null ? "n/a" : Math.round(s.median_confirm_s), r: (s) => (s.median_confirm_s == null ? <span className="text-muted-foreground">n/a</span> : dur(s.median_confirm_s)) },
    { h: "Gave the last signature", v: (s) => (s.last_share == null ? "" : Math.round(100 * s.last_share) + "% (" + s.last_n + ")") },
  ]
  return (
    <>
      <H2>Pulse</H2>
      <Muted>Executions by hour of day and weekday (UTC), signer behaviour, speed trend and queue length.{sinceEp ? " The heatmap follows the period; the trends and signer table always cover the whole history." : ""}</Muted>
      <div className="mt-2 flex items-center gap-2.5"><span className="text-muted-foreground">Heatmap</span>
        <Seg label="Heatmap chain group" value={grp} onChange={setGrp} items={[["all", "All"], ["ETH", "ETH"], ["TRON", "TRON"], ["USDT0", "USDT0"], ["Other", "Other"]]} />
      </div>
      <Card className="mt-2"><CardContent className="max-sm:px-2">
        <div className="heat" role="img" aria-label="Executions by hour and weekday, UTC">
          <span />{Array.from({ length: 24 }, (_, i) => <span key={i} className="hh">{i % 3 === 0 ? i : ""}</span>)}
          {heat.grid.map((row, di) => [<span key={"l" + di} className="hl">{DAYS[di]}</span>, ...row.map((v, hi) => <span key={di + "-" + hi} className="hc" title={DAYS[di] + " " + String(hi).padStart(2, "0") + ":00 UTC: " + v + " execution" + (v === 1 ? "" : "s")} style={v ? { background: "color-mix(in srgb, var(--primary) " + Math.round(12 + (88 * v) / heat.mx) + "%, transparent)" } : undefined} />)])}
        </div>
        <Muted className="mt-1.5">{heat.total ? num(heat.total) + " executions (about the last 16 months) · busiest hour " + String(heat.topH).padStart(2, "0") + ":00 UTC · busiest day " + DAYS[heat.topD] + " · weekend share " + heat.weekend + "%" : "No executions for this selection."}</Muted>
      </CardContent></Card>
      <div className="mt-3 grid grid-cols-2 gap-4 max-md:grid-cols-1">
        <Card><CardHeader><CardTitle>Monthly median delay (proposal to execution)</CardTitle></CardHeader><CardContent className="max-sm:px-2">
          <div ref={speedRef}>
            <ChartContainer config={speedCfg} className="aspect-auto h-56 max-sm:h-44">
              <LineChart data={speed.rows} accessibilityLayer>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="month" tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis scale="log" domain={["auto", "auto"]} tickFormatter={(v) => dur(v * 60)} tickLine={false} axisLine={false} width={52} allowDataOverflow />
                <ChartTooltip content={<ChartTooltipContent formatter={(v) => dur(v * 60)} />} />
                {speed.chains.map((c) => <Line key={c} dataKey={c} type="monotone" stroke={"var(--color-" + c + ")"} strokeWidth={2} dot={{ r: 2 }} connectNulls isAnimationActive={false} />)}
              </LineChart>
            </ChartContainer>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3.5 text-xs">{speed.chains.map((c, i) => <span key={c} className="inline-flex items-center gap-1.5"><i className="inline-block size-2.5 rounded-[2px]" style={{ background: chartColor(i) }} />{cshort(c)}</span>)}</div>
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Open proposals over time</CardTitle></CardHeader><CardContent className="max-sm:px-2">
          <div ref={queueRef}>
            <ChartContainer config={queueCfg} className="aspect-auto h-56 max-sm:h-44">
              <AreaChart data={queue} accessibilityLayer>
                <defs><linearGradient id="gQueue" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--color-open)" stopOpacity={0.4} /><stop offset="100%" stopColor="var(--color-open)" stopOpacity={0.02} /></linearGradient></defs>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="day" tickLine={false} axisLine={false} minTickGap={32} />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={36} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Area dataKey="open" type="monotone" stroke="var(--color-open)" strokeWidth={2} fill="url(#gQueue)" isAnimationActive={false} />
              </AreaChart>
            </ChartContainer>
          </div>
        </CardContent></Card>
      </div>
      <div className="mt-3 flex items-center gap-2.5"><span className="text-muted-foreground">Signers on</span>
        <Select value={cur} onValueChange={setChain}>
          <SelectTrigger aria-label="Chain for signer statistics"><SelectValue /></SelectTrigger>
          <SelectContent>{sc.map((c) => <SelectItem key={c} value={c}>{d.chains[c]?.label || c}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="mt-2"><DataTable cols={cols} rows={sg} exportName="signer_activity" rowKey={(s) => cur + s.signer} empty="No signer data." /></div>
    </>
  )
}

function Clusters() {
  const { d } = useApp()
  const C = d.clusters
  const [open, setOpen] = useState<Record<string, boolean>>({})
  useEffect(() => {
    const id = takeClusterFocus()
    if (!id) return
    setOpen((o) => ({ ...o, [id]: true }))
    const t = setTimeout(() => { const el = document.getElementById("cl-" + id); if (el) { el.scrollIntoView({ block: "center" }); el.style.outline = "2px solid var(--primary)" } }, 200)
    return () => clearTimeout(t)
  }, [])
  if (!C) return null
  const k = C.counts || {}
  const cols: Col<Cluster>[] = [
    { h: "Cluster", v: (c) => c.id, className: "mono", r: (c) => <span id={"cl-" + c.id} className="mono">{c.id}</span> },
    { h: "Type", v: (c) => c.kind },
    { h: "Members", v: (c) => c.size },
    { h: "Chains", v: (c) => c.chains.map(cshort).join(", ") },
    { h: "Frozen", v: (c) => c.usd, r: (c) => usd(c.usd) },
    { h: "Span", v: (c) => (c.first ? dday(c.first) + (c.last && c.last !== c.first ? " -> " + dday(c.last) : "") : "-"), r: (c) => (c.first ? dday(c.first) + (c.last && c.last !== c.first ? " → " + dday(c.last) : "") : "-") },
    { h: "Addresses", v: (c) => c.members.map((m) => m[1]).join(" "), wide: true,
      r: (c) => (
        <Collapsible open={!!open[c.id]} onOpenChange={(v) => setOpen((o) => ({ ...o, [c.id]: v }))}>
          <CollapsibleTrigger className="text-primary cursor-pointer underline-offset-2 hover:underline">{c.size} address{c.size > 1 ? "es" : ""}</CollapsibleTrigger>
          <CollapsibleContent>
            {c.members.map((m, i) => <div key={i} className="mono"><b>{cshort(m[0])}</b> <AddrLink chain={m[0]} a={m[1]} /> {m[2] && <LabelTag lb={m[2]} />}</div>)}
            {c.size > c.members.length && <div className="text-muted-foreground">+{c.size - c.members.length} more (see addresses.json / proposals.csv)</div>}
          </CollapsibleContent>
        </Collapsible>
      ) },
  ]
  return (
    <>
      <H2>Clusters</H2>
      <Muted className="mb-1.5">Batch {k.batch || 0} · cross-chain {k.crosschain || 0} · counterparty {k.counterparty || 0} clusters. Top by amount frozen. A cluster is a lead, not proof of a common owner.</Muted>
      <DataTable cols={cols} rows={C.top} exportName="clusters" rowKey={(c) => c.id} empty="No clusters yet." />
    </>
  )
}

const EXIT_NAME: Record<string, string> = { cex: "exchange", bridge: "bridge", dex: "DEX", mixer: "mixer", watchlist: "verified evasion address", unlabelled: "unlabelled wallet" }
const CLASS_VARIANT: Record<string, "ok" | "danger" | "warn" | "secondary"> = { clean: "ok", racing: "danger", weak: "warn", partial: "secondary" }

/** exit classes, outcome classes, lead times, third-party pulls, sweeper candidates, repeat recipients, hop-ahead watch */
function OutflowExtras({ O }: { O: NonNullable<ReturnType<typeof useApp>["d"]["outflows"]> }) {
  const { d } = useApp()
  const F = d.flight
  const hasAny = O.exits || O.classes || O.third_party || O.sweepers?.length || O.repeat?.length || (F && F.watched)
  if (!hasAny) return null
  const exits = Object.entries(O.exits || {}).filter(([, v]) => v.n).map(([k, v]) => ({ k, ...v }))
  const tp = O.third_party || { n: 0, usd: 0, looked_up: 0 }
  const lead = O.lead
  const ex: Col<(typeof exits)[number]>[] = [
    { h: "Exit", v: (r) => EXIT_NAME[r.k] || r.k },
    { h: "Escaped", v: (r) => r.usd, r: (r) => usd(r.usd) },
    { h: "Transfers", v: (r) => r.n },
  ]
  const sw: Col<NonNullable<typeof O.sweepers>[number]>[] = [
    { h: "Contract", v: (s) => s.contract, wide: true, className: "mono" },
    { h: "Victims", v: (s) => s.victims },
    { h: "Callers", v: (s) => s.callers.join(" "), wide: true, r: (s) => <>{s.callers.map((c) => <div key={c} className="mono">{c}</div>)}</> },
  ]
  const rp: Col<NonNullable<typeof O.repeat>[number]>[] = [
    { h: "Chain", v: (s) => cshort(s.chain), r: (s) => <b>{cshort(s.chain)}</b> },
    { h: "Recipient", v: (s) => s.recipient, wide: true, r: (s) => <AddrLink chain={s.chain} a={s.recipient} /> },
    { h: "Frozen sources", v: (s) => s.sources },
    { h: "Received", v: (s) => s.usd, r: (s) => usd(s.usd) },
  ]
  return (
    <div className="mt-3 space-y-3" data-testid="outflow-extras">
      <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
        {O.exits && (
          <Card><CardHeader><CardTitle>Where the escaped money went</CardTitle></CardHeader><CardContent>
            <DataTable cols={ex} rows={exits} exportName="escaped_exit_classes" rowKey={(r) => r.k} empty="None yet." />
            <Muted className="mt-1">Exchange / bridge / DEX come from wallet-attribution labels and the verified watchlist; everything else is an unlabelled wallet.</Muted>
          </CardContent></Card>
        )}
        {(O.classes || O.third_party) && (
          <Card><CardHeader><CardTitle>Outcome per freeze</CardTitle></CardHeader><CardContent>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(O.classes || {}).map(([k, v]) => <Badge key={k} variant={CLASS_VARIANT[k] || "secondary"} className="px-2.5 text-xs" title={v.n + " freezes, " + usd(v.escaped) + " escaped"}>{k} {v.n}</Badge>)}
            </div>
            <Muted className="mt-1.5">This tool's own heuristic: <b>clean</b> under 1% of the money left; <b>racing</b> at least 20% moved between proposal and execution; <b>weak</b> at least half had left before the proposal; otherwise <b>partial</b>. Needs a known frozen amount.</Muted>
            {lead && <div className="mt-1.5 text-[13px]">In-window transfers ({lead.n}): median <b>{dur(lead.median_after_submit_s)}</b> after the proposal, <b>{dur(lead.median_before_execute_s)}</b> before execution; the first one after <b>{dur(lead.first_after_submit_s)}</b>.</div>}
            <div className="mt-1.5 text-[13px]">{tp.n} escaped transfers ({usd(tp.usd)}) were <b>pulled by a third party</b> (the caller is not the frozen address); {tp.looked_up || 0} transactions looked up so far{O.fee_flagged ? " · " + O.fee_flagged + " paid an unusual priority fee" : ""}.</div>
          </CardContent></Card>
        )}
      </div>
      {!!O.sweepers?.length && (
        <Card><CardHeader><CardTitle>Sweeper candidates</CardTitle><Muted>Contracts that pulled from at least two later-frozen addresses.</Muted></CardHeader>
          <CardContent><DataTable cols={sw} rows={O.sweepers} exportName="sweeper_candidates" rowKey={(s) => s.contract} /></CardContent></Card>
      )}
      {!!O.repeat?.length && (
        <Card><CardHeader><CardTitle>Repeat recipients</CardTitle><Muted>Received escaped funds from two or more frozen addresses (service-fee collectors and dust excluded).</Muted></CardHeader>
          <CardContent><DataTable cols={rp} rows={O.repeat} exportName="repeat_recipients" rowKey={(s) => s.chain + s.recipient} /></CardContent></Card>
      )}
      {F && !!F.watched && (
        <Muted>🏃 Hop-ahead watch: {F.watched} addresses that received funds from recent freezes are polled ({F.hop1} at hop 1, {F.hop2} at hop 2); {F.moves} moves worth {usd(F.moved_usd)} seen, {F.confirmed} of them ({usd(F.confirmed_usd)}) were followed by the address's own freeze proposal.</Muted>
      )}
    </div>
  )
}
