"""Real (networked) test of the opt-in "Check live on-chain" feature plus prefers-reduced-motion.

    python scripts/verify_live.py [--site ../site]

Needs network access: the live check POSTs eth_call / TronGrid requests from the headless browser to the public RPC list in data.json.
Expectations are about addresses that are frozen today (0x5227... on ETH USDT, USDC-ETH, AVAX, POL; the TRON one on TRON) and a random clean address.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import argparse, functools, http.server, socketserver, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--site", default="../site")
ap.add_argument("--port", type=int, default=4181)
a = ap.parse_args()

class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *x): pass
    def handle(self):
        try: super().handle()
        except (ConnectionError, OSError): pass
socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.ThreadingTCPServer(("127.0.0.1", a.port), functools.partial(Q, directory=str(Path(a.site).resolve())))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{a.port}/"
A1, A2, A3 = "0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c", "TC5ZTuCLYcV1hQimnBcyp88ctEX2vX2jyy", "0x9f2b7c1d4e6a8035b1c2d3e4f5a6b7c8d9e0f1a2"
fails = []
def check(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: fails.append(m)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1360, "height": 900})
    page = ctx.new_page()
    errs, bad_net = [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("requestfailed", lambda r: bad_net.append(r.url[:80]))
    page.goto(BASE + "#watch=" + ",".join([A1, A2, A3]))
    page.wait_for_selector("[data-testid=addr-card]")
    page.get_by_role("button", name="Check all live on-chain").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=addr-card] .bg-ok-bg').length >= 3 || document.body.innerText.split('live check').length > 3", timeout=90000)
    page.wait_for_timeout(8000)
    cards = page.get_by_test_id("addr-card")
    txt = [cards.nth(i).inner_text() for i in range(cards.count())]
    check(len(txt) == 3, "3 cards")
    t1, t2, t3 = txt
    check("live check" in t1 and "live check" in t2 and "live check" in t3, "every card carries a live-check chip")
    for ch in ["ETH", "USDC-ETH", "AVAX", "POL"]:
        check(ch in t1.split("also:")[0] or ch in t1, f"card 1 shows {ch}")
    check("BLOCKED" in t1, "card 1 BLOCKED")
    check("BLOCKED" in t2 and "TRON" in t2, "card 2 (TRON) BLOCKED on TRON")
    check("NOT BLOCKED" in t3, "random address NOT BLOCKED")
    page.screenshot(path="screenshots/live-check-results.png", full_page=True)
    # per-chain verdict text for each address
    for i in range(3):
        cards.nth(i).get_by_text("Live check per chain").click()
    page.wait_for_timeout(500)
    v = [cards.nth(i).inner_text() for i in range(3)]
    for i, x in enumerate(v):
        u = x.upper(); j = u.find("LIVE CHECK PER CHAIN")
        print(f"--- card {i+1} verdict ---", " | ".join(x[j:j + 700].splitlines()))
    first = lambda t: t.split("Blocked on:")[1].splitlines()[0] if "Blocked on:" in t else ""
    blocked = first(v[0])
    for ch in ["ETH (USDT)", "USDC-ETH (USDC)", "AVAX", "POL"]:
        check(ch in blocked, f"card 1 verdict blocked on {ch}")
    check("TRON" in first(v[1]), "card 2 verdict blocked on TRON")
    check("Not blocked on any checked chain" in v[2], "card 3 verdict: not blocked on any checked chain")
    check("Unavailable" not in v[0] and "Unavailable" not in v[1], "no unavailable RPC for the frozen addresses")
    print("failed requests:", bad_net[:5])
    check(not errs, f"console errors: {errs[:3]}")
    ctx.close()

    # reduced motion: nothing hidden or mid-animation, content visible
    ctx = b.new_context(viewport={"width": 1360, "height": 900}, reduced_motion="reduce")
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    for tab in ["live", "analytics", "watchlist", "status"]:
        page.goto(BASE + "#" + tab); page.reload(); page.wait_for_load_state("networkidle"); page.wait_for_timeout(600)
        hidden = page.evaluate("""() => [...document.querySelectorAll('main *')].filter(e => {
            const s = getComputedStyle(e); return s.opacity === '0' && e.offsetWidth > 0 && !e.closest('[data-state=closed]') }).length""")
        anim = page.evaluate("document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.getTiming().iterations === Infinity).length")
        check(hidden == 0, f"reduced-motion/{tab}: no zero-opacity elements ({hidden})")
        check(anim == 0, f"reduced-motion/{tab}: no infinite animations ({anim})")
    page.goto(BASE + "#live"); page.wait_for_timeout(500)
    kp = page.locator("main").inner_text()
    check("MEDIAN DELAY" in kp.upper() and "FREEZES, 7D" in kp.upper(), "reduced-motion: KPI values rendered immediately")
    page.get_by_role("button", name="ETH", exact=True).first.click(); page.wait_for_timeout(300)
    check(page.get_by_role("button", name="ETH", exact=True).first.get_attribute("aria-pressed") == "true", "reduced-motion: filter works")
    check(not errs, f"reduced-motion console errors: {errs[:3]}")
    ctx.close(); b.close()
print("\nFAILURES:", len(fails))
sys.exit(1 if fails else 0)
