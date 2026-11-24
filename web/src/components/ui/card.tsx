import * as React from "react"
import { cn } from "@/lib/utils"

const Card = React.forwardRef<HTMLDivElement, React.ComponentProps<"div">>(({ className, ...p }, ref) => (
  <div ref={ref} data-slot="card" className={cn("bg-card text-card-foreground rounded-xl border", className)} {...p} />
))
Card.displayName = "Card"
const CardHeader = ({ className, ...p }: React.ComponentProps<"div">) => (
  <div data-slot="card-header" className={cn("flex flex-col gap-1 px-4 pt-4", className)} {...p} />
)
const CardTitle = ({ className, ...p }: React.ComponentProps<"h3">) => (
  <h3 data-slot="card-title" className={cn("text-sm leading-none font-semibold", className)} {...p} />
)
const CardDescription = ({ className, ...p }: React.ComponentProps<"p">) => (
  <p data-slot="card-description" className={cn("text-muted-foreground text-xs", className)} {...p} />
)
const CardContent = ({ className, ...p }: React.ComponentProps<"div">) => (
  <div data-slot="card-content" className={cn("px-4 py-4", className)} {...p} />
)
export { Card, CardHeader, CardTitle, CardDescription, CardContent }
