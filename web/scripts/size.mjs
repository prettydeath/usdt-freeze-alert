// Bundle size report for ../site/app (raw + gzip), marking what the browser loads up-front.
import { readFileSync, readdirSync } from "node:fs"
import { gzipSync } from "node:zlib"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

const dir = fileURLToPath(new URL("../../site/app/", import.meta.url))
const html = readFileSync(join(dir, "index.html"), "utf8")
const initial = new Set([...html.matchAll(/assets\/([^"']+\.(?:js|css))/g)].map((m) => m[1]))
let rawInit = 0, rawAll = 0, gzInit = 0, gzAll = 0
const rows = []
for (const f of readdirSync(join(dir, "assets"))) {
  const b = readFileSync(join(dir, "assets", f)), g = gzipSync(b).length
  const up = initial.has(f)
  rows.push([f, b.length, g, up])
  rawAll += b.length; gzAll += g
  if (up) { rawInit += b.length; gzInit += g }
}
rows.sort((a, b) => b[1] - a[1])
const k = (n) => (n / 1024).toFixed(1).padStart(8) + " kB"
for (const [f, b, g, up] of rows) console.log((up ? "initial " : "lazy    ") + f.padEnd(40) + k(b) + "  gzip" + k(g))
console.log("\ninitial (js+css): " + k(rawInit) + "  gzip" + k(gzInit))
console.log("all assets:       " + k(rawAll) + "  gzip" + k(gzAll))
