// anime.js v4 motion helpers. Every animation lives inside a createScope (selectors are resolved against the root,
// everything is reverted on unmount) and is skipped entirely when the visitor prefers reduced motion.
import { useEffect, useLayoutEffect, useRef, type RefObject } from "react"
import { animate, createDrawable, createLayout, createScope, stagger, utils } from "animejs"

type Scope = ReturnType<typeof createScope>
const REDUCE = "(prefers-reduced-motion: reduce)"

/** Run `fn` inside an anime.js scope rooted at `ref`, re-run when `deps` change, revert on cleanup. */
export function useMotion(ref: RefObject<HTMLElement | null>, fn: () => void, deps: unknown[]) {
  useEffect(() => {
    if (!ref.current) return
    const scope: Scope = createScope({ root: ref.current, mediaQueries: { reduce: REDUCE } }).add((self) => {
      if (self?.matches?.reduce) return
      fn()
    })
    return () => scope.revert()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

/** Count a number up to `to` (formatted by `fmt`) inside the element; static when motion is reduced. */
export function useCountUp(ref: RefObject<HTMLElement | null>, to: number | null, fmt: (n: number) => string) {
  useEffect(() => {
    const el = ref.current
    if (!el || to == null) return
    if (matchMedia(REDUCE).matches) { el.textContent = fmt(to); return }
    const o = { v: 0 }
    const scope = createScope({ root: el }).add(() => {
      animate(o, { v: to, duration: 900, ease: "outExpo", onUpdate: () => { el.textContent = fmt(o.v) }, onComplete: () => { el.textContent = fmt(to) } })
    })
    return () => { scope.revert(); el.textContent = fmt(to) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [to])
}

/** Staggered entrance of matching children (table rows, cards, bars). */
export function useStagger(ref: RefObject<HTMLElement | null>, selector: string, deps: unknown[], limit = 40) {
  useMotion(ref, () => {
    const els = Array.from(ref.current!.querySelectorAll<HTMLElement>(selector)).slice(0, limit)
    if (!els.length) return
    animate(els, { opacity: [0, 1], y: [8, 0], delay: stagger(18), duration: 420, ease: "outQuad" })
  }, deps)
}

/** Draw the stroke of the first SVG path matching `selector` (after the chart has mounted). */
export function useDrawLine(ref: RefObject<HTMLElement | null>, selector: string, deps: unknown[]) {
  useMotion(ref, () => {
    const id = requestAnimationFrame(() => {
      const paths = ref.current?.querySelectorAll<SVGPathElement>(selector)
      if (!paths?.length) return
      animate(createDrawable(Array.from(paths)), { draw: ["0 0", "0 1"], duration: 1100, ease: "inOutQuad" })
    })
    return () => cancelAnimationFrame(id)
  }, deps)
}

/** Bars rise from the baseline, staggered. */
export function useBarsRise(ref: RefObject<HTMLElement | null>, deps: unknown[]) {
  useMotion(ref, () => {
    const id = requestAnimationFrame(() => {
      const bars = Array.from(ref.current?.querySelectorAll<SVGElement>(".recharts-bar-rectangle path, .recharts-bar-rectangle rect") ?? []).slice(0, 220)
      if (!bars.length) return
      bars.forEach((b) => { b.style.transformBox = "fill-box"; b.style.transformOrigin = "50% 100%" })
      animate(bars, { scaleY: [0, 1], opacity: [0, 1], delay: stagger(6), duration: 520, ease: "outQuad" })
    })
    return () => cancelAnimationFrame(id)
  }, deps)
}

/** Infinite soft pulse for live / pending indicators. */
export function usePulse(ref: RefObject<HTMLElement | null>, active = true) {
  useMotion(ref, () => {
    if (!active) return
    animate(ref.current!.querySelectorAll(".pulse-dot"), { scale: [1, 1.7, 1], opacity: [1, 0.35, 1], duration: 1800, loop: true, ease: "inOutSine" })
  }, [active])
}

/** Entrance for a single block (status banner, card). */
export function useEnter(ref: RefObject<HTMLElement | null>, selector: string, deps: unknown[]) {
  useMotion(ref, () => {
    animate(ref.current!.querySelectorAll(selector), { opacity: [0, 1], x: [-14, 0], duration: 520, ease: "outExpo" })
  }, deps)
}

// ---- FLIP layout animation when the chain filter changes -------------------------------------------------------
type FlipEntry = { record: () => void }
const flips = new Set<FlipEntry>()
/** Call right before changing state that reshuffles the registered lists. */
export const recordLayouts = () => { if (!matchMedia(REDUCE).matches) flips.forEach((f) => f.record()) }

/** Register `ref` as a FLIP container: children with [data-flip] animate between layouts when `key` changes. */
export function useFlip(ref: RefObject<HTMLElement | null>, key: unknown) {
  const layout = useRef<ReturnType<typeof createLayout> | null>(null)
  const recorded = useRef(false)
  useEffect(() => {
    const el = ref.current
    if (!el || matchMedia(REDUCE).matches) return
    const l = createLayout(el, { children: "[data-flip]", duration: 380, ease: "outQuad" })
    layout.current = l
    const entry: FlipEntry = { record: () => { l.record(); recorded.current = true } }
    flips.add(entry)
    return () => { flips.delete(entry); l.revert(); layout.current = null }
  }, [ref])
  useLayoutEffect(() => {
    if (layout.current && recorded.current) { recorded.current = false; layout.current.animate() }
  }, [key])
}
export { utils }
