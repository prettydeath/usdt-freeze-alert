import { lazy, Suspense, useMemo, useRef } from "react"
import { useApp } from "@/lib/app"
import { age, cshort, dt, dur, DESTROY, FREEZE, med, NOW, NO_DELAY, usd } from "@/lib/format"
import type { Proposal, Signal } from "@/lib/types"
import { AddrLink, FnTag, H2, Kpi, KpiGrid, Muted, TxLink } from "@/components/common"
import { HintChip } from "@/components/chips"
import { DataTable, type Col } from "@/components/DataTable"
import { FlightsBlock } from "@/components/FlightsBlock"
import { SwitchBlock } from "@/components/SwitchBlock"
import { useFlip } from "@/lib/motion"

const LiveReport = lazy(() => import("./LiveReport"))

const sigsOf = (r: Proposal) => r.confirmations + "/" + (r.required ?? "?")

function pendCols(): Col<Proposal>[] {
  return [
    { h: "Chain", v: (r) => cshort(r.chain), r: (r) => <b className="whitespace-nowrap">{cshort(r.chain)}</b> },
    { h: "ID", v: (r) => r.tx_id, className: "mono" },
    { h: "Action", v: (r) => r.function, r: (r) => <FnTag fn={r.function} /> },
    { h: "Target", v: (r) => r.target ?? (r.amount != null ? "amount " + usd(r.amount) : ""), wide: true, className: "min-w-[260px]",
      r: (r) => r.target ? <><AddrLink chain={r.chain} a={r.target} /><HintChip chain={r.chain} a={r.target} /></> : r.amount != null ? "amount " + usd(r.amount) : <span className="text-muted-foreground">-</span> },
    { h: "Signatures", v: sigsOf },
    { h: "Age", v: (r) => (r.submitted_at ? Math.round(NOW() - Date.parse(r.submitted_at) / 1000) : ""), r: (r) => age(r.submitted_at) },
    { h: "Balance", v: (r) => r.balance_now, r: (r) => usd(r.balance_now) },
  ]
}

function SignalsTable() {
  const { d, active } = useApp()
  const S = d.signals
  const pc = S?.stats?.pending_checks || 0
  const rows = (S?.open ?? []).filter((s) => active(s.frozen_on))
  const risk = (s: Signal) => { const done = new Set(s.resolutions.map((x) => x.chain)); return s.at_risk.filter((e) => !done.has(e[0])) }
  const cols: Col<Signal>[] = [
    { h: "Address", v: (s) => s.show, wide: true, r: (s) => <AddrLink chain={s.frozen_on} a={s.show} /> },
    { h: "Frozen on", v: (s) => cshort(s.frozen_on), r: (s) => <b>{cshort(s.frozen_on)}</b> },
    { h: "When", v: (s) => (s.stage === "proposed" ? "proposed " : "") + age(s.frozen_at) + " ago" },
    { h: "Still holds, not blocked", v: (s) => risk(s).map((e) => usd(e[3]) + " " + e[1] + " on " + cshort(e[0])).join("; "), r: (s) => <>{risk(s).map((e, i) => <div key={i}><b>{usd(e[3])}</b> {e[1]} on {cshort(e[0])}</div>)}</> },
    { h: "Status", v: (s) => s.resolutions.length ? s.resolutions.map((x) => cshort(x.chain) + " blocked " + (x.lag_s == null ? "n/a" : dur(x.lag_s)) + " later" + (x.moved_before ? " (funds moved first)" : "")).join("; ") : "open",
      r: (s) => s.resolutions.length ? <>{s.resolutions.map((x, i) => <div key={i}>{cshort(x.chain)} blocked {x.lag_s == null ? "n/a" : dur(x.lag_s)} later{x.moved_before ? " (funds moved first)" : ""}</div>)}</> : <span className="text-warn font-semibold">open</span> },
  ]
  return (
    <>
      <H2 id="signals">🔗 At risk on other chains ({rows.length})</H2>
      <Muted className="mb-1.5">An address that was just frozen (or has a freeze proposal) on one chain and still holds funds on chains where it is not blocked yet. Checked on every run.{pc ? " " + pc + " chain check" + (pc > 1 ? "s" : "") + " pending a retry (a source was unavailable; nothing is skipped silently)." : ""}</Muted>
      <DataTable cols={cols} rows={rows} exportName="cross_chain_signals" empty="No open signals: nothing frozen recently still sits unblocked on another chain." />
    </>
  )
}

export default function Live() {
  const { d, active, filterKey } = useApp()
  const kpiRef = useRef<HTMLDivElement>(null)
  useFlip(kpiRef, filterKey)

  const P = useMemo(() => d.pending.filter((r) => active(r.chain)), [d, active])
  const pend = useMemo(() => {
    const risk = (r: Proposal) => (FREEZE.includes(r.function) ? 1e9 + (r.balance_now || 0) : 0) + (DESTROY.includes(r.function) ? 5e8 + (r.balance_now || 0) / 1e3 : 0)
    return [...P].sort((a, b) => risk(b) - risk(a) || b.tx_id - a.tx_id)
  }, [P])
  const stale = useMemo(() => d.stale.filter((r) => active(r.chain)), [d, active])
  const staleStats = useMemo(() => {
    let n = 0, v = 0
    for (const [c, s] of Object.entries(d.stale_by_chain || {})) if (active(c)) { n += s[0]; v += s[1] }
    return [n, v] as const
  }, [d, active])
  const sup = useMemo(() => (d.supply || []).filter((r) => active(r.chain)), [d, active])
  const supPend = sup.filter((r) => r.status === "pending")
  const supOrder = [...supPend, ...sup.filter((r) => r.status !== "pending")]
  const recent = useMemo(() => d.recent.filter((r) => active(r.chain)).slice(0, 60), [d, active])

  const k = useMemo(() => {
    const addP = P.filter((r) => FREEZE.includes(r.function)), desP = P.filter((r) => DESTROY.includes(r.function))
    const risk = addP.reduce((s, r) => s + (r.balance_now || 0), 0), desv = desP.reduce((s, r) => s + (r.balance_now || 0), 0)
    const ex = d.ex.filter((e) => active(e[0]) && e[2] >= 0), now = NOW()
    const m30 = med(ex.filter((e) => now - e[1] <= 30 * 86400).map((e) => e[2]))
    const mp = med(ex.filter((e) => now - e[1] > 30 * 86400 && now - e[1] <= 60 * 86400).map((e) => e[2]))
    let delta = "no comparison yet"
    if (m30 != null && mp) { const p = Math.round((m30 / mp - 1) * 100); delta = (p > 0 ? "▲ " : p < 0 ? "▼ " : "= ") + Math.abs(p) + "% vs previous 30 days" }
    const b7 = d.ex.filter((e) => active(e[0]) && e[3] === 1 && now - e[1] <= 7 * 86400)
    const per: Record<string, number> = {}
    b7.forEach((e) => { per[e[0]] = (per[e[0]] || 0) + 1 })
    const brk = Object.entries(per).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([c, n]) => cshort(c) + " " + n).join(" · ") || "none"
    return { addP, desP, risk, desv, m30, delta, b7: b7.length, brk }
  }, [d, active, P])

  const pc = pendCols()
  const supCols: Col<Proposal>[] = [
    { h: "Chain", v: (r) => cshort(r.chain), r: (r) => <b>{cshort(r.chain)}</b> },
    { h: "ID", v: (r) => r.tx_id, className: "mono" },
    { h: "Operation", v: (r) => r.function, r: (r) => <FnTag fn={r.function} /> },
    { h: "Category", v: (r) => r.category ?? "" },
    { h: "Amount", v: (r) => r.amount, r: (r) => (r.amount != null ? usd(r.amount) : "-") },
    { h: "Signatures", v: sigsOf },
    { h: "Status", v: (r) => r.status },
    { h: "When", v: (r) => (r.status === "pending" ? "age " + age(r.submitted_at) : dt(r.executed_at).replace(" UTC", "")), className: "mono" },
  ]
  const recCols: Col<(typeof recent)[number]>[] = [
    { h: "Chain", v: (r) => cshort(r.chain), r: (r) => <b>{cshort(r.chain)}</b> },
    { h: "Address", v: (r) => r.target, wide: true, className: "min-w-[260px]", r: (r) => <><AddrLink chain={r.chain} a={r.target} /><HintChip chain={r.chain} a={r.target} />{r.amount != null && <span className="text-muted-foreground"> {usd(r.amount)}</span>}</> },
    { h: "Action", v: (r) => r.function, r: (r) => <FnTag fn={r.function} /> },
    { h: "Executed (UTC)", v: (r) => r.at.replace("T", " ").replace("Z", ""), className: "mono" },
    { h: "Delay", v: (r) => (r.delay != null && !NO_DELAY.includes(r.chain) ? r.delay : "n/a"),
      r: (r) => r.delay == null || NO_DELAY.includes(r.chain) ? <span className="text-muted-foreground">n/a</span> : r.delay === 0 ? <span className="text-warn">no warning</span> : <>{dur(r.delay)}</> },
    { h: "Tx", v: (r) => r.tx, wide: true, r: (r) => <TxLink chain={r.chain} h={r.tx} /> },
  ]

  return (
    <div>
      <div ref={kpiRef} className="mt-3.5">
        <KpiGrid>
          <Kpi label="Pending" num={P.length} sub={k.addP.length + " addBlackList · stale: " + staleStats[0]} />
          <Kpi label="At risk" num={k.risk} fmt={usd} sub={(k.desP.length ? "destroy pending: " + usd(k.desv) + " · " : "") + "stale: " + usd(staleStats[1])} />
          <Kpi label="Median delay, 30d" value={dur(k.m30)} sub={k.delta} />
          <Kpi label="Freezes, 7d" num={k.b7} sub={k.brk} />
        </KpiGrid>
      </div>

      <FlightsBlock />

      <H2>Pending queue</H2>
      <DataTable cols={pc} rows={pend} exportName="pending" rowKey={(r) => r.chain + r.tx_id} empty="No live pending proposals." />

      <SignalsTable />

      <SwitchBlock storeKey="ufa.stale" label="Show stale proposals" title={"Stale proposals (" + staleStats[0] + ")"}>
        <Muted className="mb-1.5">Proposed but never executed - funds may still be on the address. Stale = more than {d.stale_days} days without execution{staleStats[0] > stale.length ? " (top " + stale.length + " by balance shown; full list in data/proposals.csv)" : ""}. Total balance: {usd(staleStats[1])}.</Muted>
        <DataTable cols={pc} rows={stale} exportName="stale" rowKey={(r) => r.chain + r.tx_id} empty="None." />
      </SwitchBlock>

      <SwitchBlock storeKey="ufa.supply" label="Show treasury / supply operations" title={"🏦 Treasury / supply operations (" + supPend.length + " pending)"}>
        <Muted className="mb-1.5">Minting and burning of Tether's own supply (and other admin calls). Not freezes, so they are kept out of the queue, the KPIs and the alerts.</Muted>
        <DataTable cols={supCols} rows={supOrder} exportName="treasury_supply_ops" rowKey={(r) => r.chain + r.tx_id + r.status} empty="None." />
      </SwitchBlock>

      <H2>Recently executed</H2>
      <DataTable cols={recCols} rows={recent} exportName="recently_executed" rowKey={(r, i) => (r.tx ?? "") + i} empty="No data." />

      <Suspense fallback={<div className="text-muted-foreground mt-8 text-xs">Loading charts…</div>}>
        <LiveReport />
      </Suspense>
    </div>
  )
}
