import { useApp } from "@/lib/app"
import { cshort, dday, dstr, num, usd } from "@/lib/format"
import { addrHref } from "@/lib/router"
import { Badge } from "@/components/ui/badge"
import { AddrLink, H2, Muted, TxLink } from "@/components/common"
import { LabelTag } from "@/components/chips"
import { DataTable, type Col } from "@/components/DataTable"
import type { FlightRow } from "@/lib/types"

const STATUS: Record<string, { v: "warn" | "ok" | "danger"; t: string }> = {
  watching: { v: "warn", t: "watching" }, confirmed: { v: "danger", t: "confirmed" }, frozen_later: { v: "danger", t: "frozen later" },
}
const epoch = (x: number | string | null | undefined) => (x == null ? null : typeof x === "number" ? x : Date.parse(x) / 1000)

/**
 * 🏃 Pre-proposal flights (last 24 h): an address that received money from a frozen address sends it on before any freeze
 * proposal exists on it. PLACEHOLDER WIRING: the `flights` list in data.json is produced by main's upcoming flight export; the
 * row shape below (FlightRow in lib/types.ts) is the expected one and every field is read defensively. The block renders nothing
 * until `data.flights` is present and non-empty, then lights up without further changes.
 */
export function FlightsBlock() {
  const { d, active } = useApp()
  const rows = (d.flights ?? []).filter((r) => active(r.chain))
  if (!d.flights?.length) return null
  const cols: Col<FlightRow>[] = [
    { h: "Time (UTC)", v: (r) => (epoch(r.ts) ? new Date(epoch(r.ts)! * 1000).toISOString().slice(0, 19) + "Z" : ""), className: "mono", r: (r) => dstr(epoch(r.ts)) },
    { h: "Chain", v: (r) => cshort(r.chain), r: (r) => <b>{cshort(r.chain)}</b> },
    { h: "Watched address", v: (r) => r.address, wide: true, className: "min-w-[240px]", r: (r) => <><AddrLink chain={r.chain} a={r.address} /> <a href={addrHref(r.address)}>page</a></> },
    { h: "Amount", v: (r) => r.amount, r: (r) => usd(r.amount) },
    { h: "Recipient", v: (r) => r.recipient ?? "", wide: true, r: (r) => <><AddrLink chain={r.chain} a={r.recipient} />{r.recipient_label && <> <LabelTag lb={r.recipient_label} /></>}</> },
    { h: "Source (frozen)", v: (r) => (r.source ? r.source + " hop " + r.hop + (r.freeze_ts ? " frozen " + String(r.freeze_ts).slice(0, 10) : "") : ""), wide: true,
      r: (r) => r.source ? <><AddrLink chain={r.chain} a={r.source} /> <Badge variant="outline">hop {r.hop ?? "?"}</Badge>{epoch(r.freeze_ts) ? <span className="text-muted-foreground"> frozen {dday(epoch(r.freeze_ts)!)}</span> : null}</> : null },
    { h: "Status", v: (r) => r.status ?? "", r: (r) => { const s = STATUS[r.status ?? ""]; return s ? <Badge variant={s.v}>{s.t}</Badge> : <Badge>{r.status ?? "-"}</Badge> } },
    { h: "Tx", v: (r) => r.tx ?? "", wide: true, r: (r) => <TxLink chain={r.chain} h={r.tx} /> },
  ]
  return (
    <section aria-label="Pre-proposal flights" data-testid="flights" className="mt-6">
      <H2 className="mt-0">🏃 Pre-proposal flights (last 24 h · {num(rows.length)})</H2>
      <Muted className="mb-1.5">An address that received funds from a frozen address sends money on before any freeze proposal exists on it. Recipients (hop 1) and their recipients (hop 2) are watched.</Muted>
      <DataTable cols={cols} rows={rows} exportName="pre_proposal_flights" rowKey={(r, i) => (r.tx ?? "") + i} empty="No flights in the last 24 h for this selection." />
    </section>
  )
}
