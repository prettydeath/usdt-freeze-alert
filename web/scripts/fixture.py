"""Dev fixture: populate ../site with the data files the app reads, so `python scripts/verify.py` and `make_assets.py` can run.

    python scripts/fixture.py            # fetch the published data files into web/public/ (once) and copy them into ../site
    python scripts/fixture.py --offline  # only copy what is already in web/public/

web/public/ is git-ignored. With a local checkout that has data/freezewatch.sqlite, `python -m freezewatch run` writes the real files
into site/ directly and this script is not needed. classic.html is rendered from freezewatch/template.html and data.json.
"""
import argparse, json, shutil, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PUB, SITE, TPL = HERE / "public", HERE.parent / "site", HERE.parent / "freezewatch" / "template.html"
ap = argparse.ArgumentParser()
ap.add_argument("--offline", action="store_true")
ap.add_argument("--base", default="https://" + "prettydeath." + "git" + "hub.io/usdt-freeze-alert/")
a = ap.parse_args()

def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "dev-fixture"}), timeout=60).read()

if not a.offline:
    import re
    PUB.joinpath("addr").mkdir(parents=True, exist_ok=True)
    html = get(a.base).decode("utf-8")
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</")
    json.loads(blob)
    (PUB / "data.json").write_text(blob, encoding="utf-8")
    for f in ("analytics.json", "addresses.json", "health.json"):
        (PUB / f).write_bytes(get(a.base + f))
    idx = json.loads((PUB / "addresses.json").read_text(encoding="utf-8"))
    for s in idx.get("shards", []):
        (PUB / "addr" / (s + ".json")).write_bytes(get(a.base + "addr/" + s + ".json"))

SITE.mkdir(exist_ok=True)
for f in ("data.json", "analytics.json", "addresses.json", "health.json"):
    shutil.copy(PUB / f, SITE / f)
shutil.copytree(PUB / "addr", SITE / "addr", dirs_exist_ok=True)
blob = (PUB / "data.json").read_text(encoding="utf-8").replace("</", "<\\/")
(SITE / "classic.html").write_text(TPL.read_text(encoding="utf-8").replace("__DATA__", blob), encoding="utf-8")
print("site fixture ready:", SITE)
