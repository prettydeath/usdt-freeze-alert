import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"
import { cn } from "@/lib/utils"

const TooltipProvider = ({ delayDuration = 150, ...p }: React.ComponentProps<typeof TooltipPrimitive.Provider>) => (
  <TooltipPrimitive.Provider delayDuration={delayDuration} {...p} />
)
const Tooltip = TooltipPrimitive.Root
const TooltipTrigger = TooltipPrimitive.Trigger
const TooltipContent = ({ className, sideOffset = 6, children, ...p }: React.ComponentProps<typeof TooltipPrimitive.Content>) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content sideOffset={sideOffset} data-slot="tooltip-content"
      className={cn("bg-foreground text-background z-50 max-w-[min(320px,calc(100vw-24px))] rounded-md px-2.5 py-1.5 text-xs leading-snug", className)} {...p}>
      {children}
    </TooltipPrimitive.Content>
  </TooltipPrimitive.Portal>
)
export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
