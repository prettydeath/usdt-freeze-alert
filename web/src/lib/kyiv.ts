// Europe/Kyiv calendar days via Intl (DST-aware): "Today (Kyiv)" starts at 00:00 Kyiv time
const F = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Kyiv", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" })

function parts(ms: number) {
  const o: Record<string, string> = {}
  for (const p of F.formatToParts(new Date(ms))) o[p.type] = p.value
  return { day: o.year + "-" + o.month + "-" + o.day, hour: Number(o.hour) }
}
/** Kyiv calendar day (YYYY-MM-DD) of a UTC instant in ms */
export const kyivDay = (ms: number) => parts(ms).day
export const kyivHour = (ms: number) => parts(ms).hour
/** day +/- n calendar days */
export function addDays(day: string, n: number) {
  const [y, m, d] = day.split("-").map(Number)
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10)
}
/** UTC epoch seconds of 00:00 Kyiv time of `day` (clock changes happen at 03:00 / 04:00, never at midnight) */
export function kyivDayStart(day: string) {
  const [y, m, d] = day.split("-").map(Number)
  const wall = Date.UTC(y, m - 1, d)
  for (const off of [3, 2]) {
    const ms = wall - off * 3600e3
    const p = parts(ms)
    if (p.day === day && p.hour === 0) return ms / 1000
  }
  return (wall - 2 * 3600e3) / 1000
}
