import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border border-transparent px-1.5 py-0 text-[11px] leading-5 font-semibold whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground",
        secondary: "bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        danger: "bg-danger-bg text-danger",
        warn: "bg-warn-bg text-warn",
        ok: "bg-ok-bg text-ok",
      },
    },
    defaultVariants: { variant: "secondary" },
  },
)
export interface BadgeProps extends React.ComponentProps<"span">, VariantProps<typeof badgeVariants> {}
function Badge({ className, variant, ...p }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...p} />
}
export { Badge, badgeVariants }
