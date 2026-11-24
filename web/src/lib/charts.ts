// chart colours come from the theme's --chart-N CSS variables (not hsl()); series beyond five are darker/lighter mixes of them
export function chartColor(i: number): string {
  const base = "var(--chart-" + ((i % 5) + 1) + ")"
  if (i < 5) return base
  return "color-mix(in oklch, " + base + " " + (i < 10 ? "55%, var(--foreground)" : "60%, var(--background)") + ")"
}
