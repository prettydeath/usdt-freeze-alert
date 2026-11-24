import * as React from "react"
import { cn } from "@/lib/utils"

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(({ className, type, ...p }, ref) => (
  <input ref={ref} type={type} data-slot="input"
    className={cn("border-input bg-background placeholder:text-muted-foreground flex h-8 w-full min-w-0 rounded-md border px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", className)} {...p} />
))
Input.displayName = "Input"
export { Input }
