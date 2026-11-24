import * as React from "react"
import * as SheetPrimitive from "@radix-ui/react-dialog"
import { cn } from "@/lib/utils"

const Sheet = SheetPrimitive.Root
const SheetTrigger = SheetPrimitive.Trigger
const SheetClose = SheetPrimitive.Close
const SheetContent = ({ className, children, ...p }: React.ComponentProps<typeof SheetPrimitive.Content>) => (
  <SheetPrimitive.Portal>
    <SheetPrimitive.Overlay data-slot="sheet-overlay" className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
    <SheetPrimitive.Content data-slot="sheet-content"
      className={cn("bg-card fixed inset-x-0 bottom-0 z-50 flex flex-col gap-2 rounded-t-2xl border-t p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom", className)} {...p}>
      {children}
    </SheetPrimitive.Content>
  </SheetPrimitive.Portal>
)
const SheetTitle = ({ className, ...p }: React.ComponentProps<typeof SheetPrimitive.Title>) => (
  <SheetPrimitive.Title className={cn("text-muted-foreground text-xs font-semibold uppercase", className)} {...p} />
)
const SheetDescription = (p: React.ComponentProps<typeof SheetPrimitive.Description>) => <SheetPrimitive.Description className="sr-only" {...p} />
export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetTitle, SheetDescription }
