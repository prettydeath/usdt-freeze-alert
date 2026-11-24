import { useRef } from "react"
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react"
import { useApp } from "@/lib/app"
import { dt, dur } from "@/lib/format"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { H2, Muted } from "@/components/common"
import { DataTable, type Col } from "@/components/DataTable"
import type { SourceStatus, Status as StatusT } from "@/lib/types"
import { usePulse } from "@/lib/motion"
import { DATA_BASE } from "@/lib/data"

export function StatusBadge({ s }: { s: string }) {
  if (s === "ok") return <span className="text-ok inline-flex items-center gap-1 font-semibold"><CheckCircle2 className="size-3.5" />ok</span>
  if (s === "degraded") return <span className="text-warn inline-flex items-center gap-1 font-semibold"><AlertTriangle className="size-3.5" />degraded</span>
  return <span className="text-danger inline-flex items-center gap-1 font-semibold"><XCircle className="size-3.5" />{s}</span>
}

export default function Status({ health }: { health: StatusT | null }) {
  const { d } = useApp()
  const S = health ?? d.status ?? { sources: [], overall: "ok", bad: [] }
  const ref = useRef<HTMLDivElement>(null)
  usePulse(ref, S.overall !== "ok")
  const bad = S.sources.filter((s) => s.status !== "ok").length
  const R = S.run
  const DL = S.detect
  const slow = R ? R.steps.filter((x) => x[2]).length : 0
  const cols: Col<SourceStatus>[] = [
    { h: "Source", v: (s) => s.label, r: (s) => <><b>{s.label}</b><div className="text-muted-foreground text-xs">{s.source}</div></> },
    { h: "Status", v: (s) => s.status, r: (s) => <StatusBadge s={s.status} /> },
    { h: "Last success", v: (s) => s.last_ok ?? "", className: "mono", r: (s) => (s.last_ok ? dt(s.last_ok) : "-") },
    { h: "Last error", v: (s) => s.last_error ?? "", wide: true,
      r: (s) => s.last_error ? <>{s.last_error}<div className="text-muted-foreground text-xs">{dt(s.last_error_at)}{s.fails ? " · " + s.fails + " failed run" + (s.fails > 1 ? "s" : "") + " in a row" : ""}</div></> : <span className="text-muted-foreground">none</span> },
    { h: "Coverage", v: (s) => s.coverage, wide: true,
      r: (s) => <>{s.coverage}{s.partial && <> <Badge title="Older history is still being collected, a little more on every run.">back-filling</Badge></>}{s.rows_added ? <div className="text-muted-foreground text-xs">+{s.rows_added} rows last run</div> : null}{s.note && <div className="text-muted-foreground text-xs">{s.note}</div>}</> },
  ]
  return (
    <div ref={ref}>
      <H2>Status of data sources</H2>
      <p className="text-muted-foreground mb-2 flex items-center gap-2 text-xs">
        <span className={"pulse-dot " + (S.overall === "ok" ? "" : S.overall === "failing" ? "pend" : "old")} aria-hidden="true" />
        {S.sources.length} sources · {bad ? bad + " not fully healthy" : "all healthy"}{!DL ? "" : DL.collecting || !DL.n ? " · TRON detection lag: collecting data from healthy runs" : " · new TRON proposals are detected a median " + dur(DL.median_s ?? 0) + " after submission (p90 " + dur(DL.p90_s ?? 0) + ", " + DL.n + " in 7 days" + (DL.n_24h ? "; last 24 h median " + dur(DL.median_24h_s ?? 0) : "") + ")"}
      </p>
      {R && (
        <Collapsible className="mb-3 border-t pt-1.5">
          <CollapsibleTrigger className="text-muted-foreground hover:text-foreground text-xs font-semibold uppercase">
            Last run: {dur(R.run_s)} of a {dur(R.deadline_s)} deadline · {slow} step{slow === 1 ? "" : "s"} cut short or skipped · {dt(R.at)}
          </CollapsibleTrigger>
          <CollapsibleContent className="mono pt-1.5">{R.tron_keys && <>{R.tron_keys}<br /></>}{R.steps.map((x) => x[0] + " " + dur(x[1]) + (x[2] ? " (" + x[2] + ")" : "") + (x[3] != null ? " [" + x[3] + "]" : "")).join(" · ")}</CollapsibleContent>
        </Collapsible>
      )}
      <DataTable cols={cols} rows={S.sources} exportName="source_status" rowKey={(s) => s.source} empty="No run recorded yet." />
      <Muted className="mt-2.5">Raw data: <a href={DATA_BASE + "health.json"}>health.json</a>. A source is <b>failing</b> after 3 failed runs in a row, <b>degraded</b> after 1-2 or when the on-chain count is ahead of our data by more than 3. "Back-filling" only means older history is still being collected.</Muted>
    </div>
  )
}
