import { useRef, useState, type ReactNode } from "react"
import { Check, Copy, Download } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useApp } from "@/lib/app"
import { catOf } from "@/lib/format"
import { useCountUp } from "@/lib/motion"

// ---------- tooltips (hover, keyboard focus and tap) ----------
export function Tip({ info, label, className, children }: { info: string; label: string; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <span role="button" tabIndex={0} aria-label={label + ": " + info} className={cn("cursor-help rounded-sm focus-visible:ring-2 focus-visible:ring-ring", className)}
          onClick={(e) => { e.preventDefault(); setOpen((o) => !o) }}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((o) => !o) } }}>
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{info}</TooltipContent>
    </Tooltip>
  )
}

export const INFO: Record<string, string> = {
  freeze: "Freezes the address: it can no longer send this token.",
  unfreeze: "Lifts the freeze.",
  destroy: "Burns the entire balance of an already-frozen address (confiscation).",
  mint: "Mints new tokens to Tether's treasury (supply increase). Not related to freezes.",
  redeem: "Burns tokens from Tether's treasury (supply decrease). Not related to freezes.",
  setOFTContract: "Admin: sets the LayerZero OFT contract that bridges USDT0 between chains.",
  setTrusted: "Admin: marks an address as trusted (for example a bridge or minter).",
  transferOwnership: "Admin: hands control of the token contract to another owner.",
  pause: "Admin: pauses all transfers of the token.",
  unpause: "Admin: resumes transfers of the token.",
  deprecate: "Admin: redirects the token to a new contract (legacy migration).",
  setParams: "Admin: changes the fee parameters of the token.",
  revoke: "Admin: revokes an earlier action (Aptos multisig).",
  other: "Other admin call (raw data in CSV).",
}
export function fnInfo(f: string) {
  const c = catOf(f)
  if (c === "freeze" || c === "unfreeze" || c === "destroy") return INFO[c]
  if (f === "issue" || f === "mint") return INFO.mint
  if (f === "redeem" || f === "burn") return INFO.redeem
  return INFO[f] || INFO.other
}
export const KIND_INFO: Record<string, string> = { f: INFO.freeze, u: INFO.unfreeze, b: INFO.destroy }

export function FnTag({ fn }: { fn: string }) {
  const c = catOf(fn)
  const v = c === "freeze" ? "danger" : c === "destroy" ? "warn" : c === "unfreeze" ? "ok" : "secondary"
  const icon = c === "freeze" ? "🧊 " : c === "destroy" ? "🔥 " : c === "unfreeze" ? "🔓 " : c === "supply" ? "🏦 " : ""
  return <Tip info={fnInfo(fn)} label={fn}><Badge variant={v}>{icon}{fn}</Badge></Tip>
}
export function KindTip({ kind, text }: { kind: string; text: string }) {
  const info = KIND_INFO[kind]
  return info ? <Tip info={info} label={text} className="border-b border-dotted border-muted-foreground">{text}</Tip> : <>{text}</>
}

// ---------- addresses ----------
export function CopyBtn({ text, className }: { text: string; className?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button type="button" aria-label="Copy to clipboard" title="Copy"
      className={cn("text-muted-foreground hover:text-foreground ml-1 inline-flex size-5 items-center justify-center rounded border align-middle focus-visible:ring-2 focus-visible:ring-ring", className)}
      onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) } catch { window.prompt("Copy", text) } }}>
      {done ? <Check className="size-3" /> : <Copy className="size-3" />}
    </button>
  )
}

export function AddrLink({ chain, a, copy = true }: { chain: string; a: string | null | undefined; copy?: boolean }) {
  const { d } = useApp()
  if (!a) return <span className="text-muted-foreground">-</span>
  const b = d.chains[chain]?.addr
  return (
    <span className="mono">
      {b ? <a href={b + a} target="_blank" rel="noopener noreferrer">{a}</a> : a}
      {copy && <CopyBtn text={a} />}
    </span>
  )
}
export function TxLink({ chain, h }: { chain: string; h: string | null | undefined }) {
  const { d } = useApp()
  if (!h) return <>-</>
  const b = d.chains[chain]?.tx
  return b && /^[0-9a-zA-Z]+$/.test(h) ? <a href={b + h} target="_blank" rel="noopener noreferrer" className="mono">{h}</a> : <span className="mono">{h}</span>
}

// ---------- tiles ----------
export function Kpi({ label, value, num, fmt, sub, tag, icon }: { label: ReactNode; value?: ReactNode; num?: number | null; fmt?: (n: number) => string; sub?: ReactNode; tag?: ReactNode; icon?: ReactNode }) {
  const vr = useRef<HTMLDivElement>(null)
  useCountUp(vr, num ?? null, fmt ?? ((n) => Math.round(n).toLocaleString("en-US")))
  return (
    <div data-flip className="bg-card rounded-xl border px-3.5 py-3 max-sm:px-2.5 max-sm:py-2">
      <div className="text-muted-foreground flex items-center gap-1 text-[11px] tracking-wide uppercase max-sm:text-[10px]">{icon}{label}{tag}</div>
      {/* with `num` the text is written by the count-up animation (no React children, so React never fights it) */}
      <div ref={vr} className="mt-0.5 text-2xl font-semibold tabular-nums max-sm:text-xl">{num != null ? null : value}</div>
      <div className="text-muted-foreground text-xs max-sm:line-clamp-2 max-sm:text-[11px]">{sub}</div>
    </div>
  )
}
export const KpiGrid = ({ children, className }: { children: ReactNode; className?: string }) => (
  <div className={cn("grid grid-cols-4 gap-2.5 max-md:grid-cols-2 max-sm:gap-2", className)}>{children}</div>
)
export const Approx = ({ tip }: { tip: string }) => <Tip info={tip} label="approximate"><Badge variant="warn" className="ml-1 normal-case">approx.</Badge></Tip>

export const H2 = ({ children, className, id }: { children: ReactNode; className?: string; id?: string }) => (
  <h2 id={id} className={cn("text-muted-foreground mt-6 mb-2 text-xs font-semibold tracking-wider uppercase max-sm:mt-5", className)}>{children}</h2>
)
export const Muted = ({ children, className }: { children: ReactNode; className?: string }) => <div className={cn("text-muted-foreground text-xs", className)}>{children}</div>
export const Empty = ({ children }: { children: ReactNode }) => <div className="text-muted-foreground p-3.5 text-xs">{children}</div>

export function Seg<T extends string>({ value, items, onChange, label }: { value: T; items: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="bg-card inline-flex overflow-hidden rounded-md border">
      {items.map(([v, l]) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)}
          className={cn("border-r px-3 py-1 text-xs last:border-r-0 focus-visible:ring-2 focus-visible:ring-ring max-sm:min-h-9", value === v ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>{l}</button>
      ))}
    </div>
  )
}

export function ExportBar({ onCsv, onJson, className }: { onCsv: () => void; onJson: () => void; className?: string }) {
  return (
    <div className={cn("no-print mb-1 flex justify-end gap-1.5 max-sm:justify-start", className)}>
      <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={onCsv} title="Download the rows currently shown (CSV)"><Download />CSV</Button>
      <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={onJson} title="Download the rows currently shown (JSON)"><Download />JSON</Button>
    </div>
  )
}
