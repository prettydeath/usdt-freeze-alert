import * as React from "react"
import { cn } from "@/lib/utils"

const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(({ className, ...p }, ref) => (
  <textarea ref={ref} data-slot="textarea"
    className={cn("border-input bg-background placeholder:text-muted-foreground flex min-h-24 w-full rounded-md border px-3 py-2 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring", className)} {...p} />
))
Textarea.displayName = "Textarea"
export { Textarea }
