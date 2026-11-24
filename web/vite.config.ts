import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// base './' keeps every asset URL relative, so the build works from any GitHub Pages sub-path.
// The app is emitted to ../site/app (the static site lives in site/ on the `data` branch).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  build: {
    outDir: "../site/app",
    emptyOutDir: true,
    chunkSizeWarningLimit: 450,
  },
})
