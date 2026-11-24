import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"
import { cn } from "@/lib/utils"

const Tabs = TabsPrimitive.Root
const TabsList = ({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.List>) => (
  <TabsPrimitive.List data-slot="tabs-list" className={cn("inline-flex items-center", className)} {...p} />
)
const TabsTrigger = ({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.Trigger>) => (
  <TabsPrimitive.Trigger data-slot="tabs-trigger"
    className={cn("text-muted-foreground data-[state=active]:text-foreground data-[state=active]:border-primary inline-flex h-10 items-center justify-center border-b-2 border-transparent px-3 text-[13px] font-semibold whitespace-nowrap transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring max-sm:h-11 max-sm:px-3.5 max-sm:text-sm", className)} {...p} />
)
const TabsContent = ({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.Content>) => (
  <TabsPrimitive.Content data-slot="tabs-content" className={cn("outline-none", className)} {...p} />
)
export { Tabs, TabsList, TabsTrigger, TabsContent }
