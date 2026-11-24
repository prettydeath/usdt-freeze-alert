"""Regenerate every README image from a fresh render: screenshots (desktop dark/light, mobile), the demo GIF and the social
preview. Developer tool - needs `pip install playwright pillow` and `playwright install chromium`.

The screenshots show the React app (web/): build it first, it is emitted into site/ next to the data files.

    python -m freezewatch render        # data files in site/   (or: python web/scripts/fixture.py, published data)
    (cd web && npm ci && npm run build)  # site/index.html + site/assets
    python tools/make_assets.py

Animations are switched off (prefers-reduced-motion) so every frame shows the final state.

Only public example addresses are used. PNGs are palette-quantised (Pillow) to keep the repository small."""
import functools
import http.server
import json
import re
import socketserver
import sys
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = ROOT / "assets"
EXAMPLES = ["0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c", "0x8d53903625b8e839b9df4bd28b0efb1cd6b8639a",
            "TC5ZTuCLYcV1hQimnBcyp88ctEX2vX2jyy"]
CLEAN = "0x" + "e1" * 20  # a valid address nobody froze: shows the "not blocked" state


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a, **k: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def payload():
    return json.loads((SITE / "data.json").read_text(encoding="utf-8"))


def save_png(path, src, colors=256, width=None):
    """PNG file from a PNG path/bytes, optionally resized, palette-quantised."""
    im = Image.open(src).convert("RGB")
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
    return path


def examples(D):
    """Public examples plus one pending and one unfrozen address from the current data (for the four watchlist states)."""
    pend = next((p["target"] for p in D["pending"] if p.get("target") and p["chain"] in ("eth", "tron")), None)
    man = json.loads((SITE / "addresses.json").read_text(encoding="utf-8"))
    if man.get("sharded"):   # the index is split into site/addr/<shard>.json
        records = {}
        for f in sorted((SITE / "addr").glob("*.json")):
            records.update(json.loads(f.read_text(encoding="utf-8"))["a"])
    else:
        records = man["a"]
    def unfrozen(with_balance):
        for k, r in records.items():
            for c, s in (r.get("s") or {}).items():
                if s == "unfrozen" and (not with_balance or (r.get("b") or {}).get(c, 0) > 1000):
                    return k
    unf = unfrozen(True) or unfrozen(False)   # prefer one whose balance is movable again: shows the green "Unfrozen" tile
    return [EXAMPLES[0], pend or EXAMPLES[1], unf or EXAMPLES[2], CLEAN]


def new_page(browser, scheme, w, h, dpr=1, mobile=False):
    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=dpr, is_mobile=mobile,
                              has_touch=mobile, reduced_motion="reduce")
    ctx.add_init_script("try{localStorage.setItem('ufa.theme','%s')}catch(e){}" % scheme)
    return ctx, ctx.new_page()


def goto(pg, base, frag, wait=1800):
    pg.goto(base + "/")
    pg.evaluate("location.hash = %s" % json.dumps(frag))
    pg.reload()
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(wait)


def section_shot(pg, heading, path, height, width):
    """Screenshot starting at the h2 with this text."""
    loc = pg.locator("h2", has_text=heading).first
    loc.scroll_into_view_if_needed()
    pg.evaluate("window.scrollBy(0, -70)")
    pg.wait_for_timeout(600)
    box = loc.bounding_box()
    y = max(0, box["y"] + pg.evaluate("window.scrollY") - 8)   # page coordinates: the clip may reach below the viewport
    pg.screenshot(path=str(path), clip={"x": 0, "y": y, "width": width, "height": height}, full_page=True)


def desktop(browser, base, D):
    ex = examples(D)
    for scheme in ("dark", "light"):
        ctx, pg = new_page(browser, scheme, 1360, 1000)
        goto(pg, base, "live")
        pg.screenshot(path=str(OUT / f"dashboard-{scheme}.png"))
        goto(pg, base, "analytics", 2500)
        pg.screenshot(path=str(OUT / f"analytics-{scheme}.png"))
        section_shot(pg, "Moved out before the freeze", OUT / f"outflows-{scheme}.png", 900, 1360)
        section_shot(pg, "Pulse", OUT / f"pulse-{scheme}.png", 1000, 1360)
        section_shot(pg, "Clusters", OUT / f"clusters-{scheme}.png", 760, 1360)
        goto(pg, base, "watch=" + ",".join(ex), 2500)
        pg.get_by_role("button", name="Check all live on-chain").click()
        pg.wait_for_timeout(15000)
        pg.screenshot(path=str(OUT / f"watchlist-states-{scheme}.png"), full_page=True)
        goto(pg, base, "addr=" + EXAMPLES[1], 2500)
        pg.screenshot(path=str(OUT / f"address-{scheme}.png"), full_page=False)
        goto(pg, base, "status", 1500)
        pg.screenshot(path=str(OUT / f"status-{scheme}.png"))
        ctx.close()


def mobile(browser, base):
    shots = {}
    ctx, pg = new_page(browser, "dark", 375, 812, dpr=2, mobile=True)
    for name, frag in (("live", "live"), ("analytics", "analytics"), ("watchlist", "watch=" + EXAMPLES[2]), ("status", "status"),
                       ("address", "addr=" + EXAMPLES[1])):
        goto(pg, base, frag, 2200)
        p = OUT / f"mobile-{name}.png"
        pg.screenshot(path=str(p))
        shots[name] = p
    ctx.close()
    for p in shots.values():
        save_png(p, p, width=560)
    # three phones side by side
    names = ["live", "watchlist", "address"]
    ims = [Image.open(shots[n]).convert("RGB") for n in names]
    gap, pad = 36, 40
    w = sum(i.width for i in ims) + gap * (len(ims) - 1) + 2 * pad
    h = max(i.height for i in ims) + 2 * pad
    canvas = Image.new("RGB", (w, h), (15, 22, 35))
    from PIL import ImageDraw
    d = ImageDraw.Draw(canvas)
    x = pad
    for im in ims:
        d.rounded_rectangle([x - 8, pad - 8, x + im.width + 8, pad + im.height + 8], radius=34, fill=(40, 52, 72))
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=28, fill=255)
        canvas.paste(im, (x, pad), mask)
        x += im.width + gap
    canvas.save(OUT / "mobile-strip.png")
    save_png(OUT / "mobile-strip.png", OUT / "mobile-strip.png", width=1500)


def demo(browser, base, D):
    """Short animated demo: live tab -> paste addresses -> BLOCKED -> live check -> address page."""
    frames, dur = [], []
    ctx, pg = new_page(browser, "dark", 1100, 680)

    def snap(ms):
        pg.wait_for_timeout(250)
        frames.append(Image.open(__import__("io").BytesIO(pg.screenshot())).convert("RGB"))
        dur.append(ms)
    goto(pg, base, "live")
    snap(2200)
    goto(pg, base, "watchlist", 900)
    snap(1200)
    pg.fill("textarea", "\n".join(EXAMPLES))
    snap(1800)
    pg.get_by_role("button", name="Check", exact=True).click()
    pg.wait_for_timeout(1500)
    snap(2200)
    pg.evaluate("window.scrollBy(0, 330)")
    snap(2200)
    pg.evaluate("window.scrollTo(0, 0)")
    pg.get_by_role("button", name="Check all live on-chain").click()
    pg.wait_for_timeout(14000)
    pg.evaluate("window.scrollBy(0, 330)")
    snap(2800)
    pg.locator("a:has-text('Open page')").first.click()
    pg.wait_for_timeout(2200)
    snap(2400)
    pg.evaluate("window.scrollBy(0, 420)")
    snap(2600)
    ctx.close()
    w = 900
    small = [f.resize((w, round(f.height * w / f.width)), Image.LANCZOS).quantize(colors=96, method=Image.Quantize.MEDIANCUT,
                                                                                  dither=Image.Dither.NONE) for f in frames]
    small[0].save(OUT / "demo.gif", save_all=True, append_images=small[1:], duration=dur, loop=0, optimize=True, disposal=1)


def social(browser, D):
    n = len(D["chains"])
    svg = (OUT / "social-preview.svg").read_text(encoding="utf-8")
    svg = re.sub(r"live Tether multisig proposals[^<]*", f"freeze proposals · cross-chain signals · front-run detection · {n} chains and tokens", svg)
    (OUT / "social-preview.svg").write_text(svg, encoding="utf-8")
    ctx = browser.new_context(viewport={"width": 1280, "height": 640})
    pg = ctx.new_page()
    pg.set_content('<body style="margin:0">' + svg + "</body>")
    pg.screenshot(path=str(OUT / "social-preview.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 640})
    ctx.close()


def main():
    srv, base = serve()
    D = payload()
    with sync_playwright() as p:
        b = p.chromium.launch()
        social(b, D)
        desktop(b, base, D)
        mobile(b, base)
        demo(b, base, D)
        b.close()
    srv.shutdown()
    for f in sorted(OUT.glob("*.png")):
        if not f.name.startswith("mobile-") and f.name != "avatar.png":
            save_png(f, f, colors=256, width=1360 if f.name != "social-preview.png" else None)
    for f in sorted(OUT.iterdir()):
        print(f"{f.stat().st_size / 1024:8.0f} KB  {f.name}")


if __name__ == "__main__":
    sys.exit(main())
