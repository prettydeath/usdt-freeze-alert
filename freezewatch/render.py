"""Static dashboard site/index.html, CSV/JSON export and the statistics block in the README."""
import csv
import json
import re
from pathlib import Path

from .constants import DATA_DIR, ROOT, SITE_DIR
from .util import iso, now_ts, parse_iso

NO_DELAY: set = set()  # chains without reliable submit/execute timestamps (none right now)
MARK_A, MARK_B = "<!-- STATS:START -->", "<!-- STATS:END -->"
COLS = ["chain", "tx_id", "category", "destination", "function", "target", "amount", "raw_data", "submitted_at", "submit_tx",
        "submitter", "executed", "executed_at", "exec_tx", "executor", "confirmations", "required", "status",
        "delay_seconds", "balance_at_detect", "balance_now", "first_seen_at", "updated_at"]


def _strip(s):
    return re.sub(r'"generated_at":\s*"[^"]*"', '"generated_at":""', s)


def write_if_changed(path, text):
    if path.exists() and _strip(path.read_text(encoding="utf-8")) == _strip(text):
        return
    path.write_text(text, encoding="utf-8")


def export_files(conn):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from .constants import category_of
    rows = [dict(r) for r in conn.execute("SELECT * FROM proposals ORDER BY chain, tx_id")]
    for r in rows:
        r["category"] = category_of(r["function"])
    with open(DATA_DIR / "proposals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    inf = [dict(r) for r in conn.execute("SELECT chain, token, frozen_address, sender, amount, ts, tx_hash FROM "
                                         "inflows_after_freeze ORDER BY ts")]
    with open(DATA_DIR / "inflows.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["chain", "token", "frozen_address", "sender", "amount", "ts", "tx_hash"])
        w.writeheader()
        w.writerows(inf)
    bl = [dict(r) for r in conn.execute("SELECT * FROM blacklist ORDER BY chain, address")]
    with open(DATA_DIR / "blacklist.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["chain", "address", "status", "first_blocked_at", "last_change_at",
                                          "proposal_tx_id", "funds_destroyed"])
        w.writeheader()
        w.writerows(bl)
    recent = [r for r in rows if r["executed"] and r["executed_at"]]
    recent.sort(key=lambda r: r["executed_at"], reverse=True)
    pend = [r for r in rows if r["status"] in ("pending", "stale")]
    write_if_changed(DATA_DIR / "recent.json",
                     json.dumps({"generated_at": iso(now_ts()), "pending": pend, "executed_recent": recent[:300]},
                                ensure_ascii=False, indent=1))


def build_payload(conn, stats, stale_days):
    """Dashboard data. ex - compact executed actions over ~400 days: [chain, exec_epoch, delay|-1, class(1 freeze,2 destroy,
    3 unfreeze)] - the browser computes KPIs, the histogram and weekly series from them (respecting the network filter)."""
    from . import crosschain
    from .constants import SOURCE_LABELS
    from .registry import chain_meta
    now = now_ts()
    horizon = now - 480 * 86400
    cls_code = {"freeze": 1, "destroy": 2, "unfreeze": 3}
    meta = chain_meta()
    rows = [dict(r) for r in conn.execute("SELECT * FROM proposals WHERE destination IS NOT NULL")]
    keep = ["chain", "tx_id", "function", "target", "amount", "submitted_at", "executed_at", "exec_tx",
            "confirmations", "required", "status", "delay_seconds", "balance_now"]
    from .constants import category_of
    freezish = ("freeze", "unfreeze", "destroy")
    pend = [{k: r[k] for k in keep} for r in rows if r["status"] == "pending" and category_of(r["function"]) in freezish]
    pend.sort(key=lambda r: -(r["tx_id"]))
    # treasury / supply and other admin operations are kept out of the freeze queue
    supply = [{**{k: r[k] for k in keep}, "category": category_of(r["function"])} for r in rows
              if r["status"] == "pending" and category_of(r["function"]) in ("supply", "admin")]
    supply += sorted(({**{k: r[k] for k in keep}, "category": "supply"} for r in rows
                      if r["executed"] and r["executed_at"] and category_of(r["function"]) == "supply"),
                     key=lambda r: r["executed_at"], reverse=True)[:80]
    stale_all = [{k: r[k] for k in keep} for r in rows if r["status"] == "stale" and category_of(r["function"]) in freezish]
    stale_all.sort(key=lambda r: (-(r["balance_now"] or 0), -(r["tx_id"])))
    stale_by_chain = {}
    for r in stale_all:
        d = stale_by_chain.setdefault(r["chain"], [0, 0.0])
        d[0] += 1
        d[1] += r["balance_now"] or 0
    stale_total = len(stale_all)
    kept = pend
    stale = stale_all[:150]
    ex, recent = [], []
    for r in rows:
        cls = crosschain.action_class(r["function"])
        if not (r["executed"] and r["executed_at"] and cls):
            continue
        t = parse_iso(r["executed_at"])
        if t >= horizon:
            ex.append([r["chain"], t, -1 if (r["chain"] in NO_DELAY or r["delay_seconds"] is None) else r["delay_seconds"],
                       cls_code[cls]])
        recent.append({"chain": r["chain"], "target": r["target"], "function": r["function"], "at": r["executed_at"],
                       "delay": None if r["chain"] in NO_DELAY else r["delay_seconds"], "tx": r["exec_tx"],
                       "id": r["tx_id"]})
    others = {}
    for r in conn.execute("SELECT source,subject,owner,action,ts,tx_hash,amount,amount_kind,signers FROM freezes "
                          "ORDER BY ts DESC"):
        r = dict(r)
        t = parse_iso(r["ts"])
        if r["action"] == "freeze" and t >= horizon:
            ex.append([r["source"], t, -1, 1])
        others.setdefault(r["source"], []).append(r)
        recent.append({"chain": r["source"], "target": r["subject"], "owner": r["owner"], "function": r["action"],
                       "at": r["ts"], "delay": None, "tx": r["tx_hash"], "amount": r["amount"]})
    recent.sort(key=lambda x: x["at"], reverse=True)
    chains = {}
    for c in {r["chain"] for r in rows}:
        chains[c] = meta.get(c, {"label": c, "mech": "onchain"})
    for src in SOURCE_LABELS:
        chains[src] = {"label": SOURCE_LABELS[src], "mech": "executed_only",
                       "addr": "https://etherscan.io/address/" if src == "usdc_eth" else "https://solscan.io/account/",
                       "tx": "https://etherscan.io/tx/" if src == "usdc_eth" else "https://solscan.io/tx/",
                       "threshold": {"usdc_eth": "EOA", "sol_usdc": "2 of 4", "sol_usdt": "2 of 3"}[src]}
    signers = {}
    for c, d in (stats.get("signers") or {}).items():
        lst = (d.get("all") or [])[:8]
        if lst:
            signers[c] = lst
    from . import analytics
    an = analytics.compute(conn)
    an_json = json.dumps(an, ensure_ascii=False, separators=(",", ":"))
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    write_if_changed(SITE_DIR / "analytics.json", an_json)
    xt, xn = crosschain.table(conn, 150)
    auth = [dict(r) for r in conn.execute("SELECT * FROM authority_state")]
    from . import livecheck, status
    from .registry import chain_meta
    labels = {k: v["label"] for k, v in chain_meta().items()}
    labels.update(SOURCE_LABELS)
    labels.update({"inflows": "Transfers to frozen addresses", "balances": "Balances of frozen addresses"})
    src_status = status.build(conn, stats.get("health"), labels)
    write_if_changed(SITE_DIR / "health.json", json.dumps(src_status, ensure_ascii=False, indent=1, sort_keys=True))
    return {"status": src_status, "generated_at": iso(now), "stale_days": stale_days, "live": livecheck.targets(), "stale_total": stale_total, "chains": chains,
            "pending": kept, "supply": supply, "stale": stale, "stale_by_chain": stale_by_chain, "ex": ex, "recent": recent[:200], "signers": signers, "coverage": stats.get("coverage", {}),
            "others": stats.get("others", {}), "health": stats.get("health"), "authority": auth, "cross": xt, "cross_total": xn,
            "analytics": an if len(an_json) < 1_200_000 else None, "analytics_url": "analytics.json",
            "sources": ["Etherscan V2", "public RPCs", "TronGrid/Tronscan", "Safe Transaction Service", "nearblocks",
                        "Aptos Indexer", "tonapi", "Solana RPC"]}


def render_site(conn, stats, stale_days=7):
    payload = build_payload(conn, stats, stale_days)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    out = SITE_DIR / "index.html"
    html = tpl.replace("__DATA__", blob)
    if out.exists() and _strip(out.read_text(encoding="utf-8")) == _strip(html):
        payload["_site_changed"] = False
        return payload  # only the generation time differs: do not rewrite
    out.write_text(html, encoding="utf-8")
    payload["_site_changed"] = True
    return payload


def _dur(sec):
    if sec is None:
        return "n/a"
    sec = int(round(sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


def write_badges(payload):
    """Shields.io endpoint JSONs (site/badges/*.json). They are published with the data branch, so README badges
    show live numbers without any bot commits to main."""
    import statistics
    now = now_ts()
    ex = payload["ex"]
    d30 = [e[2] for e in ex if e[2] >= 0 and now - e[1] <= 30 * 86400]
    f7 = sum(1 for e in ex if e[3] == 1 and now - e[1] <= 7 * 86400)
    pend = payload["pending"]
    add = sum(1 for p in pend if p["function"] in ("addBlackList", "addToBlockedList", "freeze_accounts"))
    items = {
        "pending": ("pending now", f"{len(pend)} ({add} freeze)", "red" if add else "brightgreen"),
        "median": ("median delay 30d", _dur(statistics.median(d30)) if d30 else "n/a", "blue"),
        "freezes7d": ("freezes 7d", str(f7), "orange"),
        "chains": ("chains", str(len(payload["chains"])), "informational"),
        "updated": ("last update", iso(now)[:16].replace("T", " ") + " UTC", "lightgrey"),
    }
    out = SITE_DIR / "badges"
    out.mkdir(parents=True, exist_ok=True)
    if not payload.get("_site_changed", True) and (out_dir_exists := (SITE_DIR / "badges" / "updated.json").exists()):
        items.pop("updated")  # keep the previous "last update" unless the dashboard data really changed
    for k, (label, msg, color) in items.items():
        write_if_changed(out / f"{k}.json", json.dumps({"schemaVersion": 1, "label": label, "message": msg, "color": color}))


def render_all(conn, stats, stale_days=7):
    export_files(conn)
    payload = render_site(conn, stats, stale_days)
    write_badges(payload)
    from . import addresses
    addresses.write(SITE_DIR, addresses.build(conn))
