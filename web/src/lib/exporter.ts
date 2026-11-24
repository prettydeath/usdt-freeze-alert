const isoDate = () => new Date().toISOString().slice(0, 10)

export function downloadFile(filename: string, mime: string, text: string) {
  const a = document.createElement("a")
  a.href = URL.createObjectURL(new Blob([text], { type: mime + ";charset=utf-8" }))
  a.download = filename
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove() }, 500)
}
export const downloadNamed = (name: string, kind: "csv" | "json", text: string) =>
  downloadFile("usdt-freeze-alert_" + name + "_" + isoDate() + "." + kind, kind === "csv" ? "text/csv" : "application/json", text)

const csvCell = (v: unknown) => { const s = String(v ?? ""); return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s }
export const toCsv = (rows: unknown[][]) => rows.map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n"
export function rowsToJson(rows: unknown[][]) {
  const [h, ...b] = rows
  return JSON.stringify(b.map((r) => Object.fromEntries((h as string[]).map((k, i) => [k, r[i]]))), null, 1)
}
