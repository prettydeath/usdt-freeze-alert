import { useRef, useState } from "react"
import { AlertTriangle, BookOpen, Menu, Monitor, Moon, Search, Snowflake, Sun, X, XCircle } from "lucide-react"
import { useApp } from "@/lib/app"
import { TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { go, TABS, type TabId } from "@/lib/router"
import { useTheme, type ThemeMode } from "@/lib/theme"
import { usePulse } from "@/lib/motion"
import { openGlossary } from "@/lib/focus"
import { cn } from "@/lib/utils"

const NAMES: Record<TabId, string> = { live: "Live", analytics: "Analytics", watchlist: "Watchlist", signers: "Signers", status: "Status" }
const ICON: Record<ThemeMode, React.ReactNode> = { auto: <Monitor className="size-3.5" />, light: <Sun className="size-3.5" />, dark: <Moon className="size-3.5" /> }
const LBL: Record<ThemeMode, string> = { auto: "Auto", light: "Light", dark: "Dark" }

export function ChainFilter({ className }: { className?: string }) {
  const { groups, sel, toggleGroup } = useApp()
  const pill = (on: boolean) => cn("inline-flex h-7 shrink-0 items-center rounded-full border px-3 text-xs font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring max-sm:h-9 max-sm:px-3.5 max-sm:text-[13px]", on ? "bg-primary text-primary-foreground border-primary" : "bg-card hover:bg-accent")
  return (
    <div role="group" aria-label="Filter by chain" className={cn("flex gap-1.5", className)}>
      <button type="button" aria-pressed={sel.size === 0} className={pill(sel.size === 0)} onClick={() => toggleGroup(null)}>All</button>
      {groups.map((g) => (
        <button key={g.id} type="button" aria-pressed={sel.has(g.id)} className={pill(sel.has(g.id))} title={g.ch.map((c) => c.toUpperCase()).join(", ")} onClick={() => toggleGroup(g.id)}>{g.label}</button>
      ))}
    </div>
  )
}

export function Header({ tab }: { tab: TabId | null }) {
  const { d } = useApp()
  const { mode, cycle } = useTheme()
  const [q, setQ] = useState("")
  const [searchOpen, setSearchOpen] = useState(false)
  const [menu, setMenu] = useState(false)
  const ref = useRef<HTMLElement>(null)
  const ageMin = Math.max(0, Math.round((Date.now() / 1000 - Date.parse(d.generated_at) / 1000) / 60))
  const old = ageMin > 180
  usePulse(ref, true)
  const bad = Object.entries(d.health || {}).filter(([, h]) => h && h.stale).map(([c]) => c.toUpperCase())
  const S = d.status
  const submit = () => { const v = q.trim(); if (!v) return; go("watch=" + encodeURIComponent(v)); setSearchOpen(false) }
  return (
    <header ref={ref} className="bg-background/90 supports-[backdrop-filter]:bg-background/75 sticky top-0 z-30 border-b backdrop-blur no-print">
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-3.5 gap-y-2 px-4 py-2.5 max-sm:min-h-14 max-sm:flex-nowrap max-sm:gap-2 max-sm:px-3 max-sm:py-2">
        <a href="#live" onClick={(e) => { e.preventDefault(); go("live") }} className={cn("text-foreground flex min-w-0 flex-1 items-center gap-2 no-underline hover:no-underline sm:flex-none", searchOpen && "max-sm:invisible")}>
          <Snowflake className="text-primary size-5 shrink-0" aria-hidden="true" />
          <h1 className="m-0 truncate text-[15px] font-bold tracking-tight">USDT Freeze Alert<small className="text-muted-foreground ml-2 text-xs font-normal max-sm:hidden">See USDT freezes before they happen</small></h1>
        </a>
        <span className={cn("bg-card inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs max-sm:size-[26px] max-sm:justify-center max-sm:p-0", searchOpen && "max-sm:invisible")} role="status" aria-label={"live, data changed " + (ageMin < 90 ? ageMin + " minutes" : Math.round(ageMin / 60) + " hours") + " ago"}>
          <span className={"pulse-dot" + (old ? " old" : "")} aria-hidden="true" />
          <span className="max-sm:hidden">live · data changed {ageMin < 90 ? ageMin + " min" : Math.round(ageMin / 60) + " h"} ago</span>
        </span>
        <span className="bg-card rounded-full border px-2.5 py-0.5 text-xs max-sm:hidden">{Object.keys(d.chains).length} chains &amp; tokens</span>
        {bad.length > 0 && (
          <span className="bg-danger-bg text-danger inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold max-sm:size-[30px] max-sm:justify-center max-sm:p-0" title="On-chain transaction count is ahead of our data; ingestion is behind.">
            <AlertTriangle className="size-3.5" /><span className="max-sm:sr-only">{bad.join(", ")} data stale</span>
          </span>
        )}
        {S && S.overall !== "ok" && (
          <a href="#status" onClick={(e) => { e.preventDefault(); go("status") }} title={"Sources with problems: " + S.bad.join(", ") + ". Click for details."}
            className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold no-underline hover:no-underline max-sm:size-[30px] max-sm:justify-center max-sm:p-0", S.overall === "failing" ? "bg-danger-bg text-danger" : "bg-warn-bg text-warn", searchOpen && "max-sm:invisible")}>
            {S.overall === "failing" ? <XCircle className="size-3.5" /> : <AlertTriangle className="size-3.5" />}
            <span className="max-sm:sr-only">{S.bad.length} source{S.bad.length > 1 ? "s" : ""} {S.overall === "failing" ? "failing" : "degraded"}</span>
          </a>
        )}
        <Input value={q} onChange={(e) => setQ(e.target.value)} type="search" placeholder="Search an address…" aria-label="Search an address" autoComplete="off" spellCheck={false}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") setSearchOpen(false) }}
          className={cn("h-7 w-52 rounded-full text-xs", "max-sm:absolute max-sm:top-2 max-sm:right-[104px] max-sm:left-3 max-sm:h-10 max-sm:w-auto max-sm:text-base", searchOpen ? "" : "max-sm:hidden")} />
        <Button variant="outline" size="sm" className="ml-auto rounded-full max-sm:hidden" onClick={cycle} aria-label={"Theme: " + LBL[mode] + ". Click to switch"} title="Theme: auto / light / dark">{ICON[mode]}{LBL[mode]}</Button>
        <Button variant="outline" size="icon" className="size-10 rounded-[10px] sm:hidden" aria-label="Search" aria-expanded={searchOpen} onClick={() => setSearchOpen((o) => !o)}>{searchOpen ? <X /> : <Search />}</Button>
        <Button variant="outline" size="icon" className="size-10 rounded-[10px] sm:hidden" aria-label="Menu" aria-expanded={menu} onClick={() => setMenu(true)}><Menu /></Button>
      </div>
      <div className="mx-auto flex max-w-[1280px] items-center justify-between gap-4 px-4 max-sm:border-t max-sm:px-2">
        <div className="overflow-x-auto [scrollbar-width:none]"><TabsList aria-label="Sections" className="max-sm:flex max-sm:w-max">
          {TABS.map((t) => <TabsTrigger key={t} value={t} onClick={() => go(t)} data-active={tab === t}>{NAMES[t]}</TabsTrigger>)}
        </TabsList></div>
        <ChainFilter className="max-sm:hidden" />
      </div>
      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetContent>
          <SheetTitle>Menu</SheetTitle>
          <SheetDescription>Theme, status, glossary and project links</SheetDescription>
          <button type="button" onClick={cycle} className="bg-card flex min-h-[46px] items-center gap-2 rounded-[10px] border px-3.5 text-[15px]">{ICON[mode]} Theme: {LBL[mode]} <span className="text-muted-foreground">(tap to switch)</span></button>
          <SheetClose asChild><a href="#status" onClick={(e) => { e.preventDefault(); go("status") }} className="bg-card text-foreground flex min-h-[46px] items-center rounded-[10px] border px-3.5 text-[15px] no-underline">Source status</a></SheetClose>
          <SheetClose asChild><button type="button" onClick={openGlossary} className="bg-card flex min-h-[46px] items-center gap-2 rounded-[10px] border px-3.5 text-[15px]"><BookOpen className="size-4" />Glossary</button></SheetClose>
          <a href="https://github.com/prettydeath/usdt-freeze-alert" target="_blank" rel="noopener noreferrer" className="bg-card text-foreground flex min-h-[46px] items-center rounded-[10px] border px-3.5 text-[15px] no-underline">GitHub ↗</a>
          <SheetClose asChild><button type="button" className="text-muted-foreground flex min-h-[46px] items-center justify-center rounded-[10px] border px-3.5 text-[15px]">Close</button></SheetClose>
        </SheetContent>
      </Sheet>
    </header>
  )
}
