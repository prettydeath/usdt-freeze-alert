import { useEffect, useState } from "react"
import { ArrowLeft, Check, Copy, Download, FileText, Link2, Printer } from "lucide-react"
import { useApp } from "@/lib/app"
import { buildReport, reportJson, type WlResult } from "@/lib/wl"
import { downloadFile } from "@/lib/exporter"
import { Button } from "@/components/ui/button"
import { AddrCard } from "@/components/AddrCard"
import { Muted } from "@/components/common"
import { liveFor, resolveAddr } from "./Watchlist"

function Flash({ onClick, icon, label, disabled }: { onClick: () => Promise<string | void> | string | void; icon: React.ReactNode; label: string; disabled?: boolean }) {
  const [t, setT] = useState<string | null>(null)
  return (
    <Button variant="outline" size="sm" disabled={disabled} onClick={async () => { const r = await onClick(); if (r) { setT(r); setTimeout(() => setT(null), 1500) } }}>
      {t ? <Check /> : icon}{t ?? label}
    </Button>
  )
}

export default function AddressPage({ addr }: { addr: string }) {
  const { d } = useApp()
  const [r, setR] = useState<WlResult | null>(null)
  useEffect(() => {
    let live = true
    setR(null)
    if (addr) resolveAddr(addr).then((x) => { if (live) setR(x) })
    document.title = "USDT Freeze Alert - " + addr.slice(0, 12) + "…"
    return () => { live = false; document.title = "USDT Freeze Alert" }
  }, [addr])
  const now = () => new Date().toISOString().slice(0, 19) + "Z"
  const invalid = !r || !!r.invalid
  const slug = addr.slice(0, 14)
  const link = location.href.split("#")[0] + "#addr=" + encodeURIComponent(addr)
  return (
    <div>
      <div className="no-print mt-3.5 flex flex-wrap items-center gap-1.5">
        <a href="#watchlist" onClick={(e) => { e.preventDefault(); history.back() }} className="mr-1 inline-flex items-center gap-1 text-xs"><ArrowLeft className="size-3.5" />back</a>
        <Flash disabled={invalid} icon={<Copy />} label="Copy report" onClick={async () => { const md = buildReport(d, r!, now()); try { await navigator.clipboard.writeText(md); return "Copied" } catch { downloadFile("report.md", "text/markdown", md); return "Downloaded" } }} />
        <Button variant="outline" size="sm" disabled={invalid} onClick={() => downloadFile("report-" + slug + ".md", "text/markdown", buildReport(d, r!, now()))}><FileText />.md</Button>
        <Button variant="outline" size="sm" disabled={invalid} onClick={() => downloadFile("report-" + slug + ".json", "application/json", reportJson(d, r!))}><Download />.json</Button>
        <Button variant="outline" size="sm" onClick={() => window.print()}><Printer />Print / PDF</Button>
        <Flash icon={<Link2 />} label="Copy link" onClick={async () => { try { await navigator.clipboard.writeText(link); return "Copied" } catch { window.prompt("Link", link) } }} />
      </div>
      {!addr ? <Muted className="p-3.5">No address.</Muted> : !r ? <Muted className="p-3.5">Loading address index…</Muted> : (
        <AddrCard r={r} page onLive={async () => { const lv = await liveFor(d, r, false); setR({ ...r, live: lv }) }} />
      )}
    </div>
  )
}
