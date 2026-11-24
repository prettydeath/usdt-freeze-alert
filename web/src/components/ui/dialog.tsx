import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

const Dialog = DialogPrimitive.Root
const DialogTrigger = DialogPrimitive.Trigger
const DialogClose = DialogPrimitive.Close
const DialogContent = ({ className, children, ...p }: React.ComponentProps<typeof DialogPrimitive.Content>) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
    <DialogPrimitive.Content data-slot="dialog-content"
      className={cn("bg-card fixed top-1/2 left-1/2 z-50 grid max-h-[85vh] w-[calc(100%-24px)] max-w-2xl -translate-x-1/2 -translate-y-1/2 gap-3 overflow-y-auto rounded-xl border p-5 shadow-lg", className)} {...p}>
      {children}
      <DialogPrimitive.Close className="absolute top-3 right-3 rounded-md p-1 opacity-70 hover:opacity-100 focus-visible:ring-2 focus-visible:ring-ring" aria-label="Close">
        <X className="size-4" />
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
)
const DialogTitle = ({ className, ...p }: React.ComponentProps<typeof DialogPrimitive.Title>) => (
  <DialogPrimitive.Title className={cn("text-base font-semibold", className)} {...p} />
)
const DialogDescription = ({ className, ...p }: React.ComponentProps<typeof DialogPrimitive.Description>) => (
  <DialogPrimitive.Description className={cn("text-muted-foreground text-xs", className)} {...p} />
)
export { Dialog, DialogTrigger, DialogClose, DialogContent, DialogTitle, DialogDescription }
