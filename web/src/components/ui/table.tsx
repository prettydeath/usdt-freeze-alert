import * as React from "react"
import { cn } from "@/lib/utils"

const Table = ({ className, ...p }: React.ComponentProps<"table">) => (
  <table data-slot="table" className={cn("w-full min-w-[640px] caption-bottom text-xs", className)} {...p} />
)
const TableHeader = (p: React.ComponentProps<"thead">) => <thead data-slot="table-header" {...p} />
const TableBody = (p: React.ComponentProps<"tbody">) => <tbody data-slot="table-body" {...p} />
const TableRow = ({ className, ...p }: React.ComponentProps<"tr">) => (
  <tr data-slot="table-row" className={cn("border-b last:border-b-0 hover:bg-muted/40", className)} {...p} />
)
const TableHead = ({ className, ...p }: React.ComponentProps<"th">) => (
  <th data-slot="table-head" className={cn("text-muted-foreground h-8 px-2.5 text-left align-middle text-[11px] font-semibold tracking-wide whitespace-nowrap uppercase", className)} {...p} />
)
const TableCell = ({ className, ...p }: React.ComponentProps<"td">) => (
  <td data-slot="table-cell" className={cn("px-2.5 py-1.5 align-top", className)} {...p} />
)
export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
