import { rmSync } from "node:fs"
import path from "node:path"
import { defineConfig, type Plugin } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

const SITE = path.resolve(import.meta.dirname, "../site")

// The app is the root page of the static site: it is emitted straight into ../site (index.html + assets/), next to the JSON
// files and classic.html that the Python pipeline writes. emptyOutDir is therefore off; only the old hashed assets are removed.
const cleanAssets = (): Plugin => ({
  name: "clean-site-assets",
  apply: "build",
  buildStart() { rmSync(path.join(SITE, "assets"), { recursive: true, force: true }) },
})

// base './' keeps every URL relative, so the build works from any GitHub Pages sub-path.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), cleanAssets()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  build: {
    outDir: SITE,
    emptyOutDir: false,
    chunkSizeWarningLimit: 450,
  },
})
