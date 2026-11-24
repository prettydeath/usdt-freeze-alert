import { useApp } from "@/lib/app"
import { cshort, dday, dstr, num, usd } from "@/lib/format"
import { addrHref } from "@/lib/router"
import { Badge } from "@/components/ui/badge"
import { H2, Muted, TxLink, AddrLink } from "@/components/common"
import { LabelTag } from "@/components/chips"
import { DataTable, type Col } from "@/components/DataTable"
import type { FlightRow } from "@/lib/types"

const STATUS: Record<string, { v: "warn" | "danger" | "secondary"; t: string }> = {
  watching: { v: "warn", t: "watching" },
  confirmed: { v: "danger", t: "confirmed: its own proposal appeared" },
  frozen: { v: "danger", t: "frozen later" },
  "proposal existed": { v: "secondary", t: "proposal already existed" },
}

/** 🏃 Pre-proposal flights (last 24 h): an address that received money from a recently frozen address sent it on before any
 *  freeze proposal existed on it. Data: data.flight.recent (moves of at least $1,000 only). */
export function FlightsBlock() {
  const { d, active } = useApp()
  const F = d.flight
  if (!F) return null
  const rows = (F.recent ?? []).filter((x) => active(x.chain))
  const cols: Col<FlightRow>[] = [
    { h: "Time (UTC)", v: (r) => new Date(r.ts * 1000).toISOString().slice(0, 19) + "Z", className: "mono", r: (r) => dstr(r.ts).replace(" UTC", "") },
    { h: "Chain", v: (r) => cshort(r.chain), r: (r) => <b>{cshort(r.chain)}</b> },
    { h: "Watched address", v: (r) => r.address, wide: true, className: "min-w-[240px]", r: (r) => <span className="mono"><a href={addrHref(r.address)} title="Open the address page">{r.address}</a></span> },
    { h: "Amount", v: (r) => r.amount, r: (r) => <b>{usd(r.amount)}</b> },
    { h: "Sent to", v: (r) => r.recipient ?? "", wide: true, r: (r) => <><AddrLink chain={r.chain} a={r.recipient} />{r.label && <> <LabelTag lb={r.label} /></>}</> },
    { h: "Funds came from (hop)", v: (r) => r.source + " (hop " + r.hop + (r.freeze_ts ? ", frozen " + dday(r.freeze_ts) : "") + ")", wide: true,
      r: (r) => <span className="mono"><a href={addrHref(r.source)}>{r.source}</a> <span className="text-muted-foreground">(hop {r.hop}{r.freeze_ts ? ", frozen " + dday(r.freeze_ts) : ""})</span></span> },
    { h: "Status", v: (r) => STATUS[r.status]?.t ?? r.status, r: (r) => { const s = STATUS[r.status]; return <Badge variant={s?.v ?? "secondary"}>{s?.t ?? r.status}</Badge> } },
    { h: "Tx", v: (r) => r.tx ?? "", wide: true, r: (r) => <TxLink chain={r.chain} h={r.tx} /> },
  ]
  return (
    <section aria-label="Pre-proposal flights" data-testid="flights">
      <H2>🏃 Pre-proposal flights (last 24 h · {num(rows.length)})</H2>
      <Muted className="mb-1.5">An address that received funds from a recently frozen address sent money on before any freeze proposal existed on it. The same events raise the Telegram alert. Watching {F.watched || 0} addresses ({F.hop1 || 0} hop 1, {F.hop2 || 0} hop 2){F.alerts_24h != null ? " · " + F.alerts_24h + " alert" + (F.alerts_24h === 1 ? "" : "s") + " sent in 24 h" : ""}.</Muted>
      <DataTable cols={cols} rows={rows} exportName="pre_proposal_flights" rowKey={(r, i) => (r.tx ?? "") + i} empty="No flights in the last 24 h." />
    </section>
  )
}
