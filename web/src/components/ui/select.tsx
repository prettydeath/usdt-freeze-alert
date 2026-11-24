import * as React from "react"
import * as SelectPrimitive from "@radix-ui/react-select"
import { Check, ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

const Select = SelectPrimitive.Root
const SelectValue = SelectPrimitive.Value
const SelectTrigger = ({ className, children, ...p }: React.ComponentProps<typeof SelectPrimitive.Trigger>) => (
  <SelectPrimitive.Trigger data-slot="select-trigger"
    className={cn("border-input bg-background flex h-8 w-fit items-center justify-between gap-2 rounded-md border px-3 text-xs whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring", className)} {...p}>
    {children}
    <SelectPrimitive.Icon asChild><ChevronDown className="size-3.5 opacity-60" /></SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
)
const SelectContent = ({ className, children, ...p }: React.ComponentProps<typeof SelectPrimitive.Content>) => (
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content position="popper" sideOffset={4}
      className={cn("bg-popover text-popover-foreground z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border shadow-md", className)} {...p}>
      <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
)
const SelectItem = ({ className, children, ...p }: React.ComponentProps<typeof SelectPrimitive.Item>) => (
  <SelectPrimitive.Item className={cn("data-[highlighted]:bg-accent relative flex cursor-default items-center rounded-sm py-1.5 pr-8 pl-2 text-xs outline-none select-none", className)} {...p}>
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    <SelectPrimitive.ItemIndicator className="absolute right-2"><Check className="size-3.5" /></SelectPrimitive.ItemIndicator>
  </SelectPrimitive.Item>
)
export { Select, SelectValue, SelectTrigger, SelectContent, SelectItem }
