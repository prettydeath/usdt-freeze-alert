import { useCallback, useEffect, useState } from "react"

export type ThemeMode = "auto" | "light" | "dark"
const KEY = "ufa.theme"

function readMode(): ThemeMode {
  try { const t = localStorage.getItem(KEY); if (t === "light" || t === "dark") return t } catch { /* storage unavailable */ }
  return "auto"
}
export function applyTheme(mode: ThemeMode) {
  const dark = mode === "dark" || (mode === "auto" && matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.classList.toggle("dark", dark)
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readMode)
  useEffect(() => {
    applyTheme(mode)
    try { localStorage.setItem(KEY, mode) } catch { /* storage unavailable */ }
    if (mode !== "auto") return
    const mq = matchMedia("(prefers-color-scheme: dark)"), f = () => applyTheme("auto")
    mq.addEventListener("change", f)
    return () => mq.removeEventListener("change", f)
  }, [mode])
  const cycle = useCallback(() => setMode((m) => (m === "auto" ? "light" : m === "light" ? "dark" : "auto")), [])
  return { mode, cycle, setMode }
}
