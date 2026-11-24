import { useApp } from "@/lib/app"
import { RAW } from "@/lib/data"
import { dt } from "@/lib/format"
import { go } from "@/lib/router"
import { openGlossary } from "@/lib/focus"
import { ChainFilter } from "@/components/Header"

const LINKS: [string, string][] = [["data/proposals.csv", "proposals.csv"], ["data/blacklist.csv", "blacklist.csv"], ["data/inflows.csv", "inflows.csv"], ["site/analytics.json", "analytics.json"], ["site/addresses.json", "addresses.json"], ["data/freezewatch.sqlite.gz", "freezewatch.sqlite.gz"]]

/** Mobile chain filter row (a scrollable strip under the header) */
export function MobileFilters() {
  return (
    <div className="-mx-3 overflow-x-auto px-3 pt-2.5 pb-0.5 [scrollbar-width:none] sm:hidden no-print">
      <ChainFilter className="w-max pr-6" />
    </div>
  )
}

export function Footer() {
  const { d } = useApp()
  return (
    <footer className="text-muted-foreground mt-8 mb-5 border-t pt-2.5 text-xs no-print">
      <div>
        <button type="button" className="text-primary underline" onClick={openGlossary}>Glossary</button> · <a href="#status" onClick={(e) => { e.preventDefault(); go("status") }}>Source status</a> · <a href="https://github.com/prettydeath/usdt-freeze-alert" target="_blank" rel="noopener noreferrer">GitHub</a> · <a href="classic.html">Classic view</a>
      </div>
      {Object.entries(d.health || {}).filter(([, h]) => h && h.stale).map(([c, h]) => (
        <div key={c} className="text-warn">⚠ {c.toUpperCase()} data stale: on-chain transaction count {h.onchain_count}, newest id in our data {h.max_id}.</div>
      ))}
      <div className="mt-1.5 mb-1.5 flex flex-wrap gap-x-3 gap-y-1"><b>Data:</b>{LINKS.map(([p, n]) => <a key={p} href={RAW + p} target="_blank" rel="noopener noreferrer" className="max-sm:inline-flex max-sm:min-h-7 max-sm:items-center">{n}</a>)}</div>
      Sources: {d.sources.join(", ")}. Labels: <a href="https://github.com/prettydeath/wallet-attribution" target="_blank" rel="noopener noreferrer">wallet-attribution</a> (MIT) and its upstream sources. Data changed: {dt(d.generated_at)}. Freezing actions = addBlackList / addToBlockedList; destroy*; remove*. TRON execution time is inferred from confirmations; Safe queues live off-chain; USDC and Solana have no queue (delay n/a). Not financial advice.
    </footer>
  )
}
