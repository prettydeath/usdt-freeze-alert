import { Badge } from "@/components/ui/badge"
import { useApp } from "@/lib/app"
import { canonKey } from "@/lib/address"
import type { Label, Reason } from "@/lib/types"

export const REASON_ICON: Record<string, string> = { hack: "🕳", sanctions: "⚖", scam: "🎣", ransomware: "🔒", "law-enforcement": "👮", other: "🧾" }
const SEVERE = ["sanctioned", "hack", "exploit", "ransomware", "scam", "phishing", "terrorism", "darknet", "theft", "fraud", "mixer"]

export function ReasonChip({ x, withUrl }: { x: Reason; withUrl?: boolean }) {
  const [cat, title, url, origin, ofac] = x
  const how = origin === "curated" ? "curated, public source" : "label from wallet-attribution"
  const link = withUrl && url ? <> <a href={url} target="_blank" rel="noopener noreferrer" title="source">source ↗</a></> : null
  if (ofac) return <><Badge variant="danger" title="OFAC SDN digital-currency address (label from wallet-attribution)">⚖ OFAC SDN</Badge>{link}</>
  return <><Badge variant={cat === "sanctions" || cat === "hack" ? "warn" : "secondary"} title={"reason hint: " + title + " (" + how + ")"}>{REASON_ICON[cat] || "🧾"} {cat}</Badge>{link}</>
}

export function HintChip({ chain, a }: { chain: string; a: string | null | undefined }) {
  const { d } = useApp()
  if (!a) return null
  const k = canonKey(a)
  const h = k ? d.hints?.[chain + ":" + k] : null
  return h ? <> <ReasonChip x={h} /></> : null
}

export function LabelTag({ lb, withUrl }: { lb: Label | null | undefined; withUrl?: boolean }) {
  if (!lb) return null
  const name = lb[0] && lb[1] && lb[1] !== lb[0] ? lb[0] + " · " + lb[1] : lb[0] || lb[1]
  return (
    <>
      <Badge variant={SEVERE.includes(lb[2] ?? "") ? "danger" : "secondary"} title={"label from wallet-attribution (" + lb[3] + "), confidence " + (lb[5] || "n/a")}>{name}{lb[2] ? " · " + lb[2] : ""}</Badge>
      {withUrl && lb[4] ? <> <a href={lb[4]} target="_blank" rel="noopener noreferrer" title="upstream source of the label">source ↗</a></> : null}
    </>
  )
}
