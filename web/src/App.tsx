import { lazy, Suspense, useEffect, useState } from "react"
import { Tabs, TabsContent } from "@/components/ui/tabs"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppProvider } from "@/lib/app"
import { loadData, loadHealth } from "@/lib/data"
import { rememberTab, useRoute, type TabId } from "@/lib/router"
import type { Data, Status as StatusT } from "@/lib/types"
import { Header } from "@/components/Header"
import { Footer, MobileFilters } from "@/components/Footer"
import { GlossaryDialog } from "@/components/Glossary"
import Live from "@/pages/Live"
import Signers from "@/pages/Signers"
import Status from "@/pages/Status"

const Analytics = lazy(() => import("@/pages/Analytics"))
const Watchlist = lazy(() => import("@/pages/Watchlist"))
const AddressPage = lazy(() => import("@/pages/AddressPage"))
const Loading = () => <div className="text-muted-foreground p-4 text-xs">Loading…</div>

function Shell({ d, health }: { d: Data; health: StatusT | null }) {
  const route = useRoute()
  const tab: TabId | null = route.name === "addr" ? null : route.name === "watch" ? "watchlist" : route.name
  useEffect(() => { if (tab) rememberTab(tab) }, [tab])
  useEffect(() => { window.scrollTo(0, 0) }, [route.name])
  return (
    <AppProvider d={d}>
      <TooltipProvider>
        <Tabs value={tab ?? ""} onValueChange={() => undefined}>
          <Header tab={tab} />
          <main className="mx-auto max-w-[1280px] px-4 max-sm:px-3">
            <MobileFilters />
            <Suspense fallback={<Loading />}>
              {route.name === "addr" && <AddressPage addr={route.addr} />}
              <TabsContent value="live">{tab === "live" && <Live />}</TabsContent>
              <TabsContent value="analytics">{tab === "analytics" && <Analytics />}</TabsContent>
              <TabsContent value="watchlist">{tab === "watchlist" && <Watchlist key={route.name === "watch" ? route.list.join(",") : "wl"} initial={route.name === "watch" ? route.list : undefined} />}</TabsContent>
              <TabsContent value="signers">{tab === "signers" && <Signers />}</TabsContent>
              <TabsContent value="status">{tab === "status" && <Status health={health} />}</TabsContent>
            </Suspense>
            <Footer />
          </main>
        </Tabs>
        <GlossaryDialog />
      </TooltipProvider>
    </AppProvider>
  )
}

export default function App() {
  const [d, setD] = useState<Data | null>(null)
  const [health, setHealth] = useState<StatusT | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    loadData().then(setD).catch((e: Error) => setErr(e.message))
    loadHealth().then(setHealth)
  }, [])
  if (err) return <div className="mx-auto max-w-lg p-6"><h1 className="text-lg font-bold">USDT Freeze Alert</h1><p className="text-danger mt-2">Could not load data.json ({err}).</p></div>
  if (!d) return <div className="text-muted-foreground p-6 text-sm" role="status">Loading data…</div>
  return <Shell d={d} health={health} />
}
