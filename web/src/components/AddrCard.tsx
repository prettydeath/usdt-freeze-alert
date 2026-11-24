import { useRef, useState, type ReactNode } from "react"
import { ChevronRight, ExternalLink } from "lucide-react"
import { useApp } from "@/lib/app"
import { cshort, dstr, dur, dt, NOW, usd } from "@/lib/format"
import { addrHref, go } from "@/lib/router"
import { setClusterFocus } from "@/lib/focus"
import { ACTION, balanceRows, cname, isSafeChain, statusOf, STATE_TXT, timelineOf, type WlResult, type BalState } from "@/lib/wl"
import type { LiveResult } from "@/lib/live"
import { useEnter } from "@/lib/motion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { AddrLink, FnTag, KindTip, TxLink, CopyBtn } from "@/components/common"
import { LabelTag, ReasonChip, REASON_ICON } from "@/components/chips"
import { DataTable, type Col } from "@/components/DataTable"
import { cn } from "@/lib/utils"

const BANNER: Record<string, string> = {
  red: "bg-danger-bg text-danger border-danger", amb: "bg-warn-bg text-warn border-warn", gry: "bg-secondary text-secondary-foreground border-muted-foreground",
  grn: "bg-ok-bg text-ok border-ok", unk: "bg-muted text-foreground border-muted-foreground",
}
const BAL_ICON: Record<BalState, string> = { blocked: "🧊", unfrozen: "🔓", clear: "✓" }
const BAL_BADGE: Record<BalState, "danger" | "ok" | "secondary"> = { blocked: "danger", unfrozen: "ok", clear: "secondary" }
const BAL_BORDER: Record<BalState, string> = { blocked: "border-l-danger", unfrozen: "border-l-ok", clear: "border-l-muted-foreground" }

function Sec({ title, children, open }: { title: ReactNode; children: ReactNode; open?: boolean }) {
  const [o, setO] = useState(!!open)
  return (
    <Collapsible open={o} onOpenChange={setO} className="mt-2.5 border-t pt-1.5">
      <CollapsibleTrigger className="text-muted-foreground data-[state=open]:text-foreground flex items-center gap-1 py-0.5 text-xs font-semibold tracking-wide uppercase">
        <ChevronRight className={cn("size-3.5 transition-transform", o && "rotate-90")} />{title}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-1.5">{children}</CollapsibleContent>
    </Collapsible>
  )
}

function chainDetail(d: ReturnType<typeof useApp>["d"], r: WlResult, x: LiveResult) {
  const rec = r.rec, c = x.chain
  const ev = rec ? (rec.e || []).filter((e) => e[0] === c) : []
  const fr = ev.filter((e) => e[1] === "f"), later = ev.filter((e) => e[1] !== "f")
  const st = rec?.s ? rec.s[c] : null
  const found = r.live?.found[c]
  return (
    <div key={x.id} className="my-1.5 rounded-lg border px-2.5 py-2 text-xs">
      <div className="mb-1"><b>{x.label}</b>{" "}
        {x.blocked === true ? <Badge variant="danger">BLOCKED</Badge> : x.blocked === false ? <Badge variant="ok">not blocked</Badge> : <Badge>unavailable</Badge>}
        {x.balance != null && <span className="text-muted-foreground"> balance {usd(x.balance)}</span>}
        <span className="text-muted-foreground"> checked {x.checked_at.slice(11, 19)} UTC via {x.source}</span>
      </div>
      {x.blocked === true && (fr.length ? (() => {
        const e = fr[fr.length - 1]
        const pid = e[7] != null && e[7] < 1e12 ? (isSafeChain(d, c) ? "Safe nonce " + Math.floor(e[7] / 100) : "proposal #" + e[7]) : null
        const delay = e[11] === 0 ? "no public proposal" : e[5] == null ? "n/a" : e[5] === 0 ? "no advance notice" : dur(e[5])
        return (
          <ul className="my-1 ml-4 list-disc">
            <li>Frozen <b>{dstr(e[2])}</b> · tx <TxLink chain={c} h={e[3]} /></li>
            {pid ? <li>{pid}{e[10] ? ", submitted " + dstr(e[10]) : ""} · submitted→executed: <b>{delay}</b>{e[9] ? " · signers " + e[8] + "/" + e[9] : ""}</li> : <li>Delay: <b>{delay}</b></li>}
            {e[4] != null && e[0] !== "eth" && <li>Balance at freeze: {usd(e[4])}</li>}
          </ul>
        )
      })() : found?.ts ? (
        <ul className="my-1 ml-4 list-disc"><li>Frozen <b>{dstr(found.ts)}</b> (found on chain, newer than our data) · tx <TxLink chain={c} h={found.tx} /></li></ul>
      ) : <div className="text-muted-foreground">Blocked after our last update ({dt(d.generated_at)}) - details appear after the next run (~10 min).</div>)}
      {x.blocked === false && (st === "frozen" || st === "burned") && <div className="text-warn">Unfrozen recently? Our index shows it frozen{fr.length ? " since " + dstr(fr[fr.length - 1][2]) : ""}; the chain says not blocked now. The index will update on the next run.</div>}
      {x.blocked === true && st === "unfrozen" && <div className="text-warn">Live says blocked, our index says unfrozen: it was probably blocked again after our last update.</div>}
      {later.map((e, i) => <div key={i}>{e[1] === "b" ? <>🔥 Burned{e[4] != null && <> <b>{usd(e[4])}</b></>}</> : "🟢 Unfrozen"} {dstr(e[2])} · tx <TxLink chain={c} h={e[3]} /></div>)}
      {rec?.i?.[c] && <div>💸 {rec.i[c][0]} transfer(s) after the freeze, {usd(rec.i[c][1])}; top sender <AddrLink chain={c} a={rec.i[c][2][0][0]} /></div>}
      {(rec?.p || []).filter((p) => p[0] === c).map((p, i) => <div key={i} className="text-muted-foreground">{p[6] === "stale" ? "Stale" : "Pending"} proposal {p[2]} {p[3]}/{p[4] ?? "?"} signatures</div>)}
    </div>
  )
}

function Verdict({ r }: { r: WlResult }) {
  const { d } = useApp()
  const L = r.live
  if (!L) return null
  if (L.note) return <div className="bg-muted rounded-md p-2 text-sm">{L.note}</div>
  const b = L.results.filter((x) => x.blocked === true), n = L.results.filter((x) => x.blocked === false), u = L.results.filter((x) => x.blocked === null)
  const nm = (x: LiveResult) => cshort(x.chain) + (x.id === "eth_usdt" ? " (USDT)" : x.id === "eth_usdc" ? " (USDC)" : "")
  const ofac = r.rec?.r && Object.values(r.rec.r).some((x) => x[4])
  const det = L.results.filter((x) => x.blocked === true || (x.blocked === false && r.rec?.s && ["frozen", "burned"].includes(r.rec.s[x.chain])))
  return (
    <>
      {ofac && <div className="bg-danger-bg text-danger mb-1.5 rounded-md p-2 text-sm">⚖ <b>OFAC SDN listed</b> (label from wallet-attribution)</div>}
      <div className="bg-muted rounded-md p-2 text-sm">
        {b.length ? <>🧊 <b>Blocked on:</b> {b.map(nm).join(", ")}</> : <>✅ <b>Not blocked on any checked chain</b></>}
        {n.length > 0 && b.length > 0 && <> · Not blocked on: {n.map(nm).join(", ")}</>}
        {u.length > 0 && <span className="text-muted-foreground"> · Unavailable: {u.map(nm).join(", ")}</span>}
        <span className="text-muted-foreground"> (live, {L.at.slice(11, 19)} UTC)</span>
      </div>
      {det.map((x) => chainDetail(d, r, x))}
    </>
  )
}

export function AddrCard({ r, onLive, page }: { r: WlResult; onLive: () => Promise<void>; page?: boolean }) {
  const { d } = useApp()
  const [busy, setBusy] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const st = statusOf(d, r)
  useEnter(root, "[data-banner]", [st.status, r.input])
  const rec = r.rec, k = r.key
  const doLive = async () => { setBusy(true); try { await onLive() } finally { setBusy(false) } }

  // chips
  const chips: ReactNode[] = []
  st.blocked.forEach((c) => chips.push(<Badge key={"b" + c} variant="danger" className="px-2.5 text-xs">⛔ {cshort(c)}</Badge>))
  st.proposed.forEach((c) => chips.push(<Badge key={"p" + c} variant="warn" className="px-2.5 text-xs">⏳ {cshort(c)}</Badge>))
  st.unfrozen.forEach((c) => chips.push(<Badge key={"u" + c} className="px-2.5 text-xs">🔓 {cshort(c)}</Badge>))
  if (rec?.r) {
    const rs = Object.values(rec.r)
    if (rs.some((x) => x[4])) chips.push(<Badge key="ofac" variant="danger" className="px-2.5 text-xs" title="OFAC SDN digital-currency address (label from wallet-attribution)">⚖ OFAC SDN</Badge>)
    rs.filter((x) => !x[4]).slice(0, 2).forEach((x, i) => chips.push(<Badge key={"r" + i} variant="warn" className="px-2.5 text-xs" title={x[1] + " (" + (x[3] === "curated" ? "curated, public source" : "label from wallet-attribution") + ")"}>{REASON_ICON[x[0]] || "🧾"} {x[0]}</Badge>))
  }
  if (st.clean.length) chips.push(<Badge key="clean" variant="secondary" className="px-2.5 text-xs" title={st.clean.map(cshort).join(", ")}>+{st.clean.length} clean</Badge>)
  if (r.live) chips.push(<Badge key="live" variant="ok" className="px-2.5 text-xs">live check {r.live.at.slice(11, 16)} UTC ✓</Badge>)

  const banner = (
    <div data-banner role="status" className={cn("flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-lg border-l-[6px] px-4 py-3.5 max-sm:flex-col max-sm:items-start max-sm:gap-0.5", BANNER[st.cls])}>
      <div>
        <div className="text-[22px] leading-tight font-extrabold tracking-wide max-sm:text-xl"><span aria-hidden="true">{st.icon}</span> {st.word}</div>
        <div className="mt-0.5 text-[13px]">{st.summary}</div>
      </div>
      {st.amount && <div className="ml-auto text-[22px] font-extrabold max-sm:ml-0 max-sm:text-[26px]">{st.amount}</div>}
    </div>
  )
  if (r.invalid) return <Card ref={root} className="mt-3 p-3.5" data-testid="addr-card">{banner}<div className="mono mt-2 text-[13px]">{r.input}</div></Card>

  const sections: ReactNode[] = []
  const tl = timelineOf(d, r)
  if (tl.length) sections.push(
    <Sec key="tl" title="Timeline" open={page}>
      <div>{tl.map((i, n) => (
        <div key={n} className="my-0.5 ml-0.5 border-l-2 pl-2 text-xs"><span className="mono">{dstr(i.ts)}</span> <b>{cshort(i.chain)}</b> {i.kind ? <KindTip kind={i.kind} text={i.label} /> : i.label}{i.tx && <> · <TxLink chain={i.chain} h={i.tx} /></>}</div>
      ))}</div>
    </Sec>)
  const bal = balanceRows(r)
  if (bal.length) sections.push(
    <Sec key="bal" title="Balances" open>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-2.5 max-sm:grid-cols-2 max-sm:gap-2">
        {bal.map((b) => (
          <div key={b.c} className={cn("bg-background rounded-lg border border-l-4 px-3 py-2.5 max-sm:px-2.5 max-sm:py-2", BAL_BORDER[b.state])}>
            <div className="text-muted-foreground text-[11px] tracking-wide uppercase">{cname(d, b.c)}</div>
            <div className="my-0.5 text-2xl font-bold tabular-nums max-sm:text-xl">{usd(b.v)}</div>
            <Badge variant={BAL_BADGE[b.state]} className="whitespace-normal">{BAL_ICON[b.state]} {STATE_TXT[b.state]}</Badge>
            <div className="text-muted-foreground mt-1 text-[11px]">{b.src === "live" ? "live check, " : ""}balance as of {b.asof || "last scan"}</div>
          </div>
        ))}
      </div>
    </Sec>)
  if (rec) {
    const ec: Col<any[]>[] = [
      { h: "Chain", v: (x) => cshort(x[0]), r: (x) => <b>{cshort(x[0])}</b> },
      { h: "Action", v: (x) => ACTION[x[1]] || x[1], r: (x) => <><KindTip kind={x[1]} text={ACTION[x[1]] || x[1]} />{x[6] && <span className="text-muted-foreground"> token account {x[6]}</span>}</> },
      { h: "When (UTC)", v: (x) => dstr(x[2]), className: "mono" },
      { h: "Delay", v: (x) => (x[5] == null ? "n/a" : x[5]), r: (x) => (x[5] == null ? <span className="text-muted-foreground">n/a</span> : x[5] === 0 ? <span className="text-warn">no warning</span> : dur(x[5])) },
      { h: "Amount", v: (x) => x[4], r: (x) => (x[4] != null ? usd(x[4]) : "-") },
      { h: "Tx", v: (x) => x[3], wide: true, r: (x) => <TxLink chain={x[0]} h={x[3]} /> },
    ]
    if (rec.e?.length) sections.push(<Sec key="ex" title={"Executed actions (" + rec.e.length + ")"} open={page}><DataTable cols={ec} rows={rec.e} stagger={false} /></Sec>)
    const outf = Object.entries(rec.o || {})
    if (outf.length) sections.push(
      <Sec key="out" title="Funds moved before the freeze" open={page}>
        {outf.map(([c, fl]) => fl.map((f, i) => {
          const rc: Col<any[]>[] = [
            { h: "Recipient", v: (x) => x[0], wide: true, r: (x) => <><AddrLink chain={c} a={x[0]} />{x[4] ? <> <Badge title="This recipient was (or became) frozen too">also frozen</Badge></> : null}{x[5] ? <> <Badge variant="warn" title={"The transaction was sent by someone else: " + x[5]}>pulled by third party</Badge></> : null}</> },
            { h: "72 h", v: (x) => x[1], r: (x) => usd(x[1]) },
            { h: "In-window", v: (x) => x[2], r: (x) => (x[2] > 0 ? <span className="text-warn">{usd(x[2])}</span> : "-") },
            { h: "Label", v: (x) => (x[3] ? [x[3][0], x[3][1], x[3][2]].filter(Boolean).join(" / ") : ""), r: (x) => <LabelTag lb={x[3]} withUrl /> },
          ]
          return (
            <div key={c + i} className="mb-2">
              <div className="text-muted-foreground mb-1 text-xs">{cshort(c)} · frozen {dstr(f[0])}: <b className="text-foreground">{usd(f[1])}</b> escaped in the 72 h before
                {f[6] ? <> <Badge title="This frozen address is a labelled exchange / service wallet: its transfers are routine operations, not counted in the escape totals">service wallet, not counted</Badge></> : null}
                {f[5] ? <> <Badge variant="warn" title="This address has so many transfers that the list hit a cap; totals are a lower bound and it is never flagged as front-run.">truncated: at least these</Badge></> : null}
                , of which <b className="text-foreground">{usd(f[2])}</b> between proposal and execution{f[4] > 0 && <><br />+ {usd(f[4])} moved between addresses that were also frozen (not counted)</>}</div>
              <DataTable cols={rc} rows={f[3]} stagger={false} />
            </div>
          )
        }))}
      </Sec>)
    const inf = Object.entries(rec.i || {})
    if (inf.length) {
      const ic: Col<(typeof inf)[number]>[] = [
        { h: "Chain", v: (x) => cshort(x[0]), r: (x) => <b>{cshort(x[0])}</b> },
        { h: "Transfers", v: (x) => x[1][0] },
        { h: "Total", v: (x) => x[1][1], r: (x) => usd(x[1][1]) },
        { h: "Top senders", v: (x) => x[1][2].map((s) => s[0]).join(" "), wide: true, r: (x) => <>{x[1][2].map((s, i) => <div key={i}><AddrLink chain={x[0]} a={s[0]} /> ({s[1]}, {usd(s[2])})</div>)}</> },
      ]
      sections.push(<Sec key="inf" title="Funds sent after the freeze" open={page}><DataTable cols={ic} rows={inf} stagger={false} /></Sec>)
    }
    const live = (rec.p || []).filter((x) => x[6] === "pending"), stale = (rec.p || []).filter((x) => x[6] === "stale")
    if (live.length || stale.length) {
      const pc: Col<any[]>[] = [
        { h: "Chain", v: (x) => cshort(x[0]), r: (x) => <b>{cshort(x[0])}</b> },
        { h: "ID", v: (x) => x[1], className: "mono" },
        { h: "Action", v: (x) => x[2], r: (x) => <FnTag fn={x[2]} /> },
        { h: "Signatures", v: (x) => x[3] + "/" + (x[4] ?? "?") },
        { h: "Age", v: (x) => (x[5] ? dur(NOW() - x[5]) : "unknown") },
      ]
      sections.push(
        <Sec key="pp" title={"Pending / stale proposals (" + (live.length + stale.length) + ")"} open={page}>
          {live.length > 0 && <><h4 className="text-muted-foreground my-1 text-xs font-semibold uppercase">Pending</h4><DataTable cols={pc} rows={live} stagger={false} /></>}
          {stale.length > 0 && <><h4 className="text-muted-foreground my-1 text-xs font-semibold uppercase">Stale (never executed)</h4><DataTable cols={pc} rows={stale} stagger={false} /></>}
        </Sec>)
    }
  }
  if (r.live) sections.push(<Sec key="lv" title="Live check per chain" open={page}><Verdict r={r} /></Sec>)

  const inflow = rec ? Object.values(rec.i || {}).reduce((s, v) => s + v[1], 0) : 0
  const burns = rec ? (rec.e || []).filter((e) => e[1] === "b").sort((a, b) => b[2] - a[2]) : []
  const have = new Set((d.clusters?.top || []).map((c) => c.id))
  return (
    <Card ref={root} className="mt-3 p-3.5 max-sm:p-3" data-testid="addr-card">
      {banner}
      {st.also && <div className="text-muted-foreground mx-0.5 mt-1.5 text-xs">also: {st.also}</div>}
      <div className="my-2.5 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1 max-sm:flex-nowrap max-sm:overflow-x-auto">{chips}</div>
        <Button variant="outline" size="sm" className="border-primary text-primary max-sm:h-10 max-sm:text-sm" disabled={busy} onClick={doLive} title="Sends this address to public RPC providers">
          {busy ? "Checking…" : r.live ? "Re-check live" : "Check live on-chain"}
        </Button>
      </div>
      <div className="mono text-[13px]">{r.input}<CopyBtn text={r.input} />
        {!page && <> <a href={addrHref(r.input)} className="ml-1 inline-flex items-center gap-1 rounded-full border px-2 text-xs" title="Shareable full page with a report">Open page <ExternalLink className="size-3" /></a></>}
      </div>
      {k && r.input.toLowerCase() !== k && /^T/.test(r.input) && <div className="text-muted-foreground mono">EVM-style key {k}</div>}
      {rec === undefined ? <div className="text-muted-foreground p-3 text-xs">Address index could not be loaded.</div> : (
        <>
          {rec ? (
            <div className="mt-1.5 space-y-1 text-[13px]">
              {inflow > 0 && <div>💸 {usd(inflow)} sent to it after the freeze</div>}
              {burns.length > 0 && <div>🔥 {burns.reduce((s, e) => s + (e[4] || 0), 0) ? usd(burns.reduce((s, e) => s + (e[4] || 0), 0)) + " burned" : "funds burned"} on {dstr(burns[0][2])}</div>}
              {rec.r && Object.entries(rec.r).map(([c, x]) => <div key={"r" + c}>{x[4] ? "⚖" : "🧾"} <b>{x[4] ? "OFAC SDN listed" : "Reason hint"}</b> ({cshort(c)}): <ReasonChip x={x} withUrl /> {!x[4] && <span className="text-muted-foreground">{x[1]}{x[3] === "curated" ? " - curated, public source" : " - label from wallet-attribution"}</span>}</div>)}
              {Object.entries(rec.l || {}).map(([c, l]) => <div key={"l" + c}>🏷 <b>frozen address labeled:</b> <LabelTag lb={l} withUrl /> <span className="text-muted-foreground">({cshort(c)}, label from wallet-attribution)</span></div>)}
              {(rec.x || []).map((x, i) => <div key={"x" + i}>🔗 {x[1] === "proposed" ? "freeze proposed" : "frozen"} on {cshort(x[0])}, still unblocked: {x[3].map((e: any[], j: number) => <span key={j}>{j > 0 && ", "}<b>{usd(e[3])}</b> on {cshort(e[0])}</span>)}</div>)}
              {(rec.ex || []).map((x, i) => x[0] === "eip7702"
                ? <div key={"ex" + i} className="text-warn">⚠️ <b>EIP-7702 delegated</b> to <span className="mono">{x[1]}</span> - the delegate can act as this account</div>
                : <div key={"ex" + i} className="text-warn">⚠️ <b>Allowance</b> ({x[2] >= 1e30 || x[3] === "unlimited" ? "unlimited" : usd(x[2])}) to <span className="mono">{x[1] === "0x000000000022d473030f116ddee9f6b43ac78ba3" ? x[1] + " (Permit2)" : x[1]}</span> - the spender can still move funds after the proposal</div>)}
              {rec.w && <div>🏃 Watched (hop {rec.w[0]}): received {usd(rec.w[2])} from frozen <span className="mono">{rec.w[1]}</span>{rec.w[3] > 0 ? ", has moved " + usd(rec.w[3]) + " on" : ""}</div>}
              {rec.c && rec.c.length > 0 && <div>🧩 Clusters: {rec.c.map((id) => have.has(id)
                ? <a key={id} href="#analytics" className="mr-1 inline-flex rounded-full border px-2 text-xs" title="Open the cluster list" onClick={(e) => { e.preventDefault(); setClusterFocus(id); go("analytics") }}>{id}</a>
                : <Badge key={id} variant="outline" className="mr-1" title="Cluster not in the top list">{id}</Badge>)}</div>}
            </div>
          ) : <div className="text-muted-foreground mt-1.5 text-xs">Not in our freeze index (as of {dt(d.generated_at)}). Only addresses with a proposal, freeze, unfreeze or burn are indexed.</div>}
          {sections}
        </>
      )}
    </Card>
  )
}
