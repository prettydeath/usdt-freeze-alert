import * as React from "react"
import * as RechartsPrimitive from "recharts"
import { cn } from "@/lib/utils"

// shadcn/ui chart wrapper (Recharts v3). Colours are CSS variables (var(--chart-N)), set per series via ChartConfig.
export type ChartConfig = Record<string, { label?: React.ReactNode; color?: string }>

const ChartContext = React.createContext<{ config: ChartConfig } | null>(null)
function useChart() {
  const c = React.useContext(ChartContext)
  if (!c) throw new Error("useChart must be used within <ChartContainer />")
  return c
}

function ChartContainer({ id, className, children, config, ...p }: React.ComponentProps<"div"> & {
  config: ChartConfig
  children: React.ComponentProps<typeof RechartsPrimitive.ResponsiveContainer>["children"]
}) {
  const uid = React.useId()
  const chartId = `chart-${id || uid.replace(/:/g, "")}`
  return (
    <ChartContext.Provider value={{ config }}>
      <div data-slot="chart" data-chart={chartId}
        className={cn("[&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line]:stroke-border/60 [&_.recharts-curve.recharts-tooltip-cursor]:stroke-border [&_.recharts-rectangle.recharts-tooltip-cursor]:fill-muted/50 flex aspect-video w-full justify-center text-xs [&_.recharts-layer]:outline-hidden [&_.recharts-sector]:outline-hidden [&_.recharts-surface]:outline-hidden", className)} {...p}>
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer initialDimension={{ width: 320, height: 200 }}>{children}</RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  )
}

function ChartStyle({ id, config }: { id: string; config: ChartConfig }) {
  const entries = Object.entries(config).filter(([, c]) => c.color)
  if (!entries.length) return null
  const css = `[data-chart=${id}] {\n${entries.map(([k, c]) => `  --color-${k}: ${c.color};`).join("\n")}\n}`
  return <style dangerouslySetInnerHTML={{ __html: css }} />
}

const ChartTooltip = RechartsPrimitive.Tooltip

function ChartTooltipContent({ active, payload, label, hideLabel = false, className, formatter, labelFormatter }: {
  active?: boolean
  payload?: any[]
  label?: any
  hideLabel?: boolean
  className?: string
  formatter?: (value: any, name: string, item: any) => React.ReactNode
  labelFormatter?: (label: any) => React.ReactNode
}) {
  const { config } = useChart()
  if (!active || !payload?.length) return null
  return (
    <div className={cn("border-border/60 bg-popover text-popover-foreground grid min-w-32 items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs shadow-xl", className)}>
      {!hideLabel && <div className="font-medium">{labelFormatter ? labelFormatter(label) : label}</div>}
      <div className="grid gap-1">
        {payload.filter((i) => i.value != null).map((item, idx) => {
          const key = String(item.dataKey ?? item.name)
          const cfg = config[key]
          const color = item.color || item.payload?.fill || `var(--color-${key})`
          return (
            <div key={idx} className="flex items-center gap-2">
              <span className="size-2.5 shrink-0 rounded-[2px]" style={{ background: color }} />
              <span className="text-muted-foreground">{cfg?.label ?? item.name}</span>
              <span className="text-foreground ml-auto pl-3 font-mono font-medium tabular-nums">
                {formatter ? formatter(item.value, key, item) : Number(item.value).toLocaleString("en-US")}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const ChartLegend = RechartsPrimitive.Legend
function ChartLegendContent({ payload, className }: { payload?: readonly any[]; className?: string }) {
  const { config } = useChart()
  if (!payload?.length) return null
  return (
    <div className={cn("flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-2 text-xs", className)}>
      {payload.map((item) => (
        <div key={String(item.dataKey)} className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px]" style={{ background: item.color }} />
          {config[String(item.dataKey)]?.label ?? item.value}
        </div>
      ))}
    </div>
  )
}

export { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent, ChartStyle }
