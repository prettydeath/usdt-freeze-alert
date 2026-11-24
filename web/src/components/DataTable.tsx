import { useRef, type ReactNode } from "react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ExportBar, Empty } from "@/components/common"
import { downloadNamed, rowsToJson, toCsv } from "@/lib/exporter"
import { useStagger } from "@/lib/motion"
import { cn } from "@/lib/utils"

export type Cell = string | number | boolean | null | undefined
export interface Col<T> {
  /** header text (also the mobile card label and the CSV column name) */
  h: string
  /** plain value used for CSV / JSON export (and as default display) */
  v: (r: T) => Cell
  /** rich display; defaults to v */
  r?: (r: T) => ReactNode
  className?: string
  /** long cell (addresses, notes): full-width line on mobile cards */
  wide?: boolean
  sortKey?: string
}

export function DataTable<T>({ cols, rows, empty = "No data.", exportName, rowKey, sort, onSort, stagger = true, limit, className, ariaLabel }: {
  cols: Col<T>[]
  rows: T[]
  empty?: ReactNode
  exportName?: string
  rowKey?: (r: T, i: number) => string | number
  sort?: { key: string; dir: 1 | -1 }
  onSort?: (key: string) => void
  stagger?: boolean
  limit?: number
  className?: string
  ariaLabel?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  useStagger(ref, "tbody tr", [stagger ? rows.length : -1, rows[0]], limit ?? 40)
  const doExport = (kind: "csv" | "json") => {
    const data = [cols.map((c) => c.h), ...rows.map((r) => cols.map((c) => c.v(r) ?? ""))]
    downloadNamed(exportName!, kind, kind === "csv" ? toCsv(data) : rowsToJson(data))
  }
  return (
    <div ref={ref} className={className}>
      {exportName && rows.length > 0 && <ExportBar onCsv={() => doExport("csv")} onJson={() => doExport("json")} />}
      <div className="table-wrap bg-card overflow-x-auto rounded-xl border">
        <Table className="cards" aria-label={ariaLabel ?? exportName}>
          {rows.length > 0 && (
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                {cols.map((c) => {
                  const sk = c.sortKey
                  const on = sk && sort?.key === sk
                  return (
                    <TableHead key={c.h} aria-sort={on ? (sort!.dir < 0 ? "descending" : "ascending") : undefined}>
                      {sk && onSort ? (
                        <button type="button" className="hover:text-foreground inline-flex items-center gap-1 uppercase" onClick={() => onSort(sk)}>
                          {c.h}<span aria-hidden="true">{on ? (sort!.dir < 0 ? "▼" : "▲") : ""}</span>
                        </button>
                      ) : c.h}
                    </TableHead>
                  )
                })}
              </TableRow>
            </TableHeader>
          )}
          <TableBody>
            {rows.length === 0 ? (
              <TableRow className="hover:bg-transparent"><TableCell colSpan={cols.length || 1}><Empty>{empty}</Empty></TableCell></TableRow>
            ) : rows.map((r, i) => (
              <TableRow key={rowKey ? rowKey(r, i) : i}>
                {cols.map((c, j) => (
                  <TableCell key={c.h} data-l={j > 0 ? c.h : undefined} className={cn(c.className, c.wide && "wide", j === 0 && "first")}>
                    {c.r ? c.r(r) : String(c.v(r) ?? "")}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
