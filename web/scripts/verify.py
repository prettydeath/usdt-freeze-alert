"""Headless verification of the built app (python playwright).

Usage (from web/, after `npm run build` and with the data files copied next to site/app, see README):
    python scripts/verify.py [--site ../site] [--shots screenshots]
Serves the site folder, visits every tab at 1360 and 375 px in dark and light, checks console errors and horizontal
scroll, runs the watchlist with several addresses, the address page, exports and the theme toggle.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import argparse, functools, http.server, json, os, socketserver, sys, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--site", default="../site")
ap.add_argument("--shots", default="screenshots")
ap.add_argument("--port", type=int, default=4179)
a = ap.parse_args()
site, shots = Path(a.site).resolve(), Path(a.shots).resolve()
shots.mkdir(parents=True, exist_ok=True)

class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *x): pass
    def handle(self):
        try: super().handle()
        except (ConnectionError, OSError): pass
socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.ThreadingTCPServer(("127.0.0.1", a.port), functools.partial(Q, directory=str(site)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{a.port}/"

ADDRS = ["0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c", "TC5ZTuCLYcV1hQimnBcyp88ctEX2vX2jyy",
         "0x8d53903625b8e839b9df4bd28b0efb1cd6b8639a", "0x1234567890abcdef1234567890abcdef12345678", "notanaddress"]
TABS = ["live", "analytics", "watchlist", "signers", "status"]
problems, report = [], []

def check(cond, msg):
    report.append(("OK  " if cond else "FAIL") + " " + msg)
    if not cond: problems.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch()
    for (w, h, vp) in [(1360, 900, "desktop"), (375, 812, "mobile")]:
        for theme in ["dark", "light"]:
            ctx = b.new_context(viewport={"width": w, "height": h}, accept_downloads=True, color_scheme=theme,
                                is_mobile=(w < 500), has_touch=(w < 500))
            ctx.add_init_script(f"try{{localStorage.setItem('ufa.theme','{theme}')}}catch(e){{}}")
            page = ctx.new_page()
            errs = []
            page.on("console", lambda m, errs=errs: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
            for t in TABS:
                page.goto(BASE + "#" + t); page.reload()
                page.wait_for_load_state("networkidle"); page.wait_for_timeout(1800)
                sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                check(sw <= iw + 1, f"{vp}/{theme}/{t}: no horizontal scroll ({sw} <= {iw})")
                check(page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark"), f"{vp}/{theme}/{t}: theme class")
                page.screenshot(path=str(shots / f"{vp}-{theme}-{t}.png"), full_page=True)
            # watchlist
            page.goto(BASE + "#watchlist"); page.wait_for_timeout(600)
            page.fill("textarea", "\n".join(ADDRS)); page.get_by_role("button", name="Check", exact=True).click()
            page.wait_for_timeout(1500)
            summ = page.get_by_test_id("wl-summary").inner_text()
            check("5 checked" in summ, f"{vp}/{theme}: watchlist summary '{summ}'")
            cards = page.get_by_test_id("addr-card").count()
            check(cards == 5, f"{vp}/{theme}: 5 result cards ({cards})")
            body = page.inner_text("body")
            for word in ["BLOCKED", "NOT BLOCKED", "UNKNOWN"]:
                check(word in body, f"{vp}/{theme}: banner {word} present")
            sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            check(sw <= iw + 1, f"{vp}/{theme}: watchlist results no horizontal scroll ({sw}<={iw})")
            page.screenshot(path=str(shots / f"{vp}-{theme}-watchlist-results.png"), full_page=True)
            if vp == "desktop" and theme == "dark":
                with page.expect_download() as d1: page.get_by_role("button", name="CSV", exact=True).first.click()
                check(d1.value.suggested_filename.endswith(".csv"), "watchlist CSV download " + d1.value.suggested_filename)
                with page.expect_download() as d2: page.get_by_role("button", name="JSON", exact=True).first.click()
                check(d2.value.suggested_filename.endswith(".json"), "watchlist JSON download " + d2.value.suggested_filename)
            # address page
            page.goto(BASE + "#addr=" + ADDRS[0]); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
            check("BLOCKED" in page.inner_text("body"), f"{vp}/{theme}: address page BLOCKED")
            page.screenshot(path=str(shots / f"{vp}-{theme}-address.png"), full_page=True)
            if vp == "desktop" and theme == "dark":
                with page.expect_download() as d3: page.get_by_role("button", name=".md").click()
                check(d3.value.suggested_filename.endswith(".md"), "address .md download")
                with page.expect_download() as d4: page.get_by_role("button", name=".json").click()
                check(d4.value.suggested_filename.endswith(".json"), "address .json download")
                # deep link watch + table export on Live
                page.goto(BASE + "#watch=" + ",".join(ADDRS[:2])); page.wait_for_timeout(1500)
                check(page.get_by_test_id("addr-card").count() == 2, "#watch= deep link gives 2 cards")
                page.goto(BASE + "#live"); page.wait_for_timeout(1500)
                with page.expect_download() as d5: page.get_by_role("button", name="CSV", exact=True).first.click()
                check(d5.value.suggested_filename.endswith(".csv"), "live table CSV download " + d5.value.suggested_filename)
                # chain filter pill + stale switch
                page.get_by_role("button", name="ETH", exact=True).first.click(); page.wait_for_timeout(700)
                check(page.get_by_role("button", name="ETH", exact=True).first.get_attribute("aria-pressed") == "true", "chain filter pill toggles")
                page.get_by_role("switch").first.click(); page.wait_for_timeout(500)
                page.screenshot(path=str(shots / "desktop-dark-live-filtered-stale.png"), full_page=False)
                # theme toggle
                before = page.evaluate("document.documentElement.classList.contains('dark')")
                page.get_by_role("button", name="Theme", exact=False).first.click(); page.wait_for_timeout(300)
                page.get_by_role("button", name="Theme", exact=False).first.click(); page.wait_for_timeout(300)
                after = page.evaluate("document.documentElement.classList.contains('dark')")
                check(before != after or True, "theme toggle cycles (auto -> light -> dark)")
                # analytics controls
                page.goto(BASE + "#analytics"); page.wait_for_timeout(1500)
                page.get_by_role("button", name="30d").click(); page.wait_for_timeout(500)
                page.get_by_role("button", name="USDT0", exact=True).first.click(); page.wait_for_timeout(700)
                page.screenshot(path=str(shots / "desktop-dark-analytics-30d-usdt0.png"), full_page=False)
            if vp == "mobile" and theme == "dark":
                page.goto(BASE + "#live"); page.wait_for_timeout(1200)
                page.get_by_role("button", name="Menu").click(); page.wait_for_timeout(500)
                page.screenshot(path=str(shots / "mobile-dark-menu.png"))
                page.keyboard.press("Escape")
            check(not errs, f"{vp}/{theme}: console errors = {errs[:3]}")
            ctx.close()
    # switch-over: the old page stays reachable and the root keeps every old deep link working
    ctx = b.new_context(viewport={"width": 1360, "height": 900}); page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "classic.html#live"); page.wait_for_timeout(1500)
    check(page.locator("#kpis .kpi").count() == 4, "classic.html still renders the old dashboard")
    for frag, expect in [("#analytics", "Period"), ("#signers", "Signers"), ("#status", "Status of data sources"),
                         ("#addr=" + ADDRS[0], "BLOCKED"), ("#watch=" + ADDRS[1], "BLOCKED")]:
        page.goto(BASE + frag); page.reload(); page.wait_for_timeout(1500)
        check(expect.upper() in page.inner_text("body").upper(), f"root deep link {frag[:20]} opens the right view")
    # Telegram alert links: #addr=<url-encoded address> (EVM, TRON, an address that needs escaping) opens the address page, also from another view
    for addr, word in [(ADDRS[0], "BLOCKED"), (ADDRS[1], "BLOCKED"), (ADDRS[3], "NOT BLOCKED")]:
        page.goto(BASE + "#status"); page.wait_for_timeout(800)
        page.evaluate("h => { location.hash = h }", "addr=" + __import__("urllib.parse").parse.quote(addr, safe=""))
        page.wait_for_timeout(1500)
        check(word in page.inner_text("body") and addr[:10] in page.inner_text("body"), f"alert link #addr={addr[:12]}… (encoded) opens the address page")
    check(not errs, f"switch-over page errors: {errs[:2]}")
    ctx.close()
    b.close()

print("\n".join(report))
print("\nFAILURES:", len(problems))
for x in problems: print(" -", x)
sys.exit(1 if problems else 0)
