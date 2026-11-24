import { useState, type ReactNode } from "react"
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible"
import { Switch } from "@/components/ui/switch"
import { H2 } from "@/components/common"

/** A heading with a switch that shows / hides its content (state remembered in localStorage). */
export function SwitchBlock({ title, label, storeKey, children }: { title: ReactNode; label: string; storeKey: string; children: ReactNode }) {
  const [on, setOn] = useState(() => { try { return localStorage.getItem(storeKey) === "1" } catch { return false } })
  const set = (v: boolean) => { setOn(v); try { localStorage.setItem(storeKey, v ? "1" : "0") } catch { /* storage unavailable */ } }
  return (
    <Collapsible open={on} onOpenChange={set}>
      <div className="mt-6 mb-2 flex flex-wrap items-center gap-3">
        <H2 className="m-0">{title}</H2>
        <Switch checked={on} onCheckedChange={set} aria-label={label} title={label} />
      </div>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  )
}
