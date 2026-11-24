import { useApp, chainsSorted } from "@/lib/app"
import { cshort, dur, NO_DELAY } from "@/lib/format"
import { AddrLink, H2 } from "@/components/common"
import { DataTable, type Col } from "@/components/DataTable"
import type { Signer } from "@/lib/types"

export default function Signers() {
  const { d, active } = useApp()
  const rows = chainsSorted(Object.keys(d.signers)).filter(active).flatMap((c) => d.signers[c].map((s) => ({ c, s })))
  const cols: Col<{ c: string; s: Signer }>[] = [
    { h: "Chain", v: (r) => cshort(r.c), r: (r) => <b>{cshort(r.c)}</b> },
    { h: "Signer", v: (r) => r.s.signer, wide: true, className: "min-w-[260px]", r: (r) => <AddrLink chain={r.c} a={r.s.signer} /> },
    { h: "Proposed", v: (r) => r.s.submits },
    { h: "Confirmed", v: (r) => r.s.confirms },
    { h: "Revoked", v: (r) => r.s.revokes },
    { h: "Avg confirm latency", v: (r) => (NO_DELAY.includes(r.c) ? "n/a" : r.s.avg_confirm_latency == null ? "n/a" : Math.round(r.s.avg_confirm_latency)), r: (r) => (NO_DELAY.includes(r.c) ? "n/a" : dur(r.s.avg_confirm_latency)) },
  ]
  return (
    <div>
      <H2>Signers</H2>
      <DataTable cols={cols} rows={rows} exportName="signers" rowKey={(r) => r.c + r.s.signer} />
    </div>
  )
}
