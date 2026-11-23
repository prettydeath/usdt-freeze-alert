"""Why was it frozen? Reasons and sanctions flags, never guessed.

Two sources, both public and cited:
1. config/reasons.yaml - curated entries {title, category, date, source_url, chain?, addresses[]}. An entry without a public
   https source_url, an unknown category or no address is rejected. The file ships empty on purpose: it only gets entries that
   can be verified from a public, citable source.
2. Hints from the wallet-attribution dataset (MIT): categories hack / exploit / ransomware / scam / phishing / darknet /
   terrorism / mixer / sanctioned. Shown as "label from wallet-attribution" with the upstream source link - never as our own
   claim. OFAC SDN = a wallet-attribution label whose source list contains `ofac` (current OFAC SDN digital-currency addresses).
The YAML reader is a tiny subset parser (list of mappings, scalars, one nested list) so the project stays dependency-free."""
import logging
import re

from .constants import ROOT

log = logging.getLogger("freezewatch.reasons")
REASONS_FILE = ROOT / "config" / "reasons.yaml"  # tracked on main (data/ is ignored: it lives on the data branch)
CATEGORIES = ("hack", "sanctions", "scam", "ransomware", "law-enforcement", "other")
# wallet-attribution category -> our reason category
HINT_CATEGORY = {"hack": "hack", "exploit": "hack", "theft": "hack", "ransomware": "ransomware", "scam": "scam", "phishing": "scam",
                 "fraud": "scam", "darknet": "other", "terrorism": "sanctions", "mixer": "other", "sanctioned": "sanctions"}


def _scalar(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def _kv(cur, body):
    """`key: value` / `key:` / `key: [a, b]` into the current mapping; returns the key whose nested list follows (or None)."""
    k, _, v = body.partition(":")
    k, v = k.strip(), v.strip()
    if not v:
        cur[k] = []
        return k
    cur[k] = [_scalar(x) for x in v[1:-1].split(",") if x.strip()] if (v.startswith("[") and v.endswith("]")) else _scalar(v)
    return None


def parse_yaml(text):
    """Subset: a top-level list of mappings (`- key: value`, then indented `key: value` lines); a key may hold a nested
    `- item` list or an inline [a, b]; # comments; quoted scalars."""
    items, cur, list_key = [], None, None
    for raw in text.splitlines():
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        line = re.sub(r"\s+#.*$", "", raw).rstrip()
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if body.startswith("-") and indent == 0:           # a new entry
            cur = {}
            items.append(cur)
            rest = body[1:].strip()
            list_key = _kv(cur, rest) if rest else None
        elif body.startswith("-") and cur is not None and list_key:   # nested list item
            cur[list_key].append(_scalar(body[1:]))
        elif cur is not None and ":" in body:
            list_key = _kv(cur, body)
    return items


def validate(entries):
    """-> (valid entries, [problem text]). Only cited, categorised, address-bearing entries survive."""
    ok, bad = [], []
    for i, e in enumerate(entries):
        addrs = e.get("addresses") or ([e["address"]] if e.get("address") else [])
        if isinstance(addrs, str):
            addrs = [addrs]
        why = None
        if not str(e.get("source_url", "")).startswith("https://"):
            why = "no public https source_url"
        elif e.get("category") not in CATEGORIES:
            why = f"category must be one of {', '.join(CATEGORIES)}"
        elif not e.get("title"):
            why = "no title"
        elif not addrs:
            why = "no address"
        if why:
            bad.append(f"entry {i + 1}: {why}")
            continue
        ok.append({**e, "addresses": addrs})
    return ok, bad


def load(path=None):
    """-> {(chain|'*', key): entry}; a missing or empty file is fine."""
    from .addresses import canon
    p = path or REASONS_FILE
    if not p.exists():
        return {}
    entries, bad = validate(parse_yaml(p.read_text(encoding="utf-8")))
    for b in bad:
        log.warning("reasons.yaml %s - skipped", b)
    idx = {}
    for e in entries:
        for a in e["addresses"]:
            k = canon(a)
            if k:
                idx[(e.get("chain") or "*", k)] = e
    return idx


def is_ofac(label):
    return bool(label) and "ofac" in (label.get("source") or "").lower()


def reason_of(labels, curated, chain, key, net_key):
    """Reason for one address: curated entry first, then a wallet-attribution hint. -> dict or None.
    `net_key` = (network, normalised key) for the label lookup; `key` = canonical address key for the curated lookup."""
    e = curated.get((chain, key)) or curated.get(("*", key))
    if e:
        return {"category": e["category"], "title": e["title"], "date": e.get("date"), "url": e["source_url"], "origin": "curated"}
    lb = labels.get(net_key)
    if lb and lb.get("category") in HINT_CATEGORY:
        return {"category": HINT_CATEGORY[lb["category"]], "title": lb.get("entity") or lb.get("label") or lb["category"],
                "date": None, "url": lb.get("source_url"), "origin": "wallet-attribution", "raw": lb["category"],
                "ofac": is_ofac(lb), "source": lb.get("source")}
    return None


def analyze(conn, labels, curated=None, now=None):
    """Reasons coverage and OFAC numbers over all executed freezes (unique address per chain)."""
    from . import analytics
    from .labels import NETWORK_OF, key_of
    from .addresses import canon_for
    curated = curated if curated is not None else load()
    usd = {}
    for (c, a, _f), v in analytics.frozen_usd_map(conn).items():
        usd[(c, a)] = max(usd.get((c, a)) or 0, v or 0)
    cats, known, unknown = {}, [0, 0.0], [0, 0.0]
    ofac, sanc_other = {"n": 0, "usd": 0.0, "top": []}, 0
    uniq_all, uniq_known, uniq_ofac = set(), set(), set()   # the same address frozen on several chains counts once
    for (chain, addr), v in usd.items():
        net = NETWORK_OF.get(chain)
        nk = (net, key_of(net, addr)) if net else None
        ck = canon_for(chain, addr)
        uniq_all.add(ck)
        r = reason_of(labels, curated, chain, ck, nk)
        if r:
            uniq_known.add(ck)
            known[0] += 1
            known[1] += v
            d = cats.setdefault(r["category"], {"n": 0, "usd": 0.0})
            d["n"] += 1
            d["usd"] += v
            if r.get("ofac"):
                uniq_ofac.add(ck)
                ofac["n"] += 1
                ofac["usd"] += v
                ofac["top"].append({"chain": chain, "address": addr, "usd": round(v, 2), "entity": r["title"], "url": r["url"]})
            elif r.get("raw") == "sanctioned":
                sanc_other += 1
        else:
            unknown[0] += 1
            unknown[1] += v
    ofac["top"].sort(key=lambda x: -x["usd"])
    ofac["top"] = ofac["top"][:15]
    ofac["usd"] = round(ofac["usd"], 2)
    ofac["unique"] = len(uniq_ofac)
    total = known[0] + unknown[0]
    return {"freezes": total, "known": known[0], "unknown": unknown[0], "known_usd": round(known[1], 2),
            "unknown_usd": round(unknown[1], 2), "curated": len(curated), "unique": len(uniq_all), "known_unique": len(uniq_known),
            "known_pct": round(100 * known[0] / total, 1) if total else None,
            "by_category": {k: {"n": v["n"], "usd": round(v["usd"], 2)} for k, v in sorted(cats.items(), key=lambda kv: -kv[1]["n"])},
            "ofac": ofac, "sanctioned_other": sanc_other}


def hints_for(rows, labels, curated=None):
    """{'chain:key': [category, title, url, origin, ofac]} for addresses shown in the live tables (pending / recent)."""
    from .addresses import canon_for
    from .labels import NETWORK_OF, key_of
    curated = curated if curated is not None else load()
    out = {}
    for chain, addr in rows:
        if not addr:
            continue
        net = NETWORK_OF.get(chain)
        key = canon_for(chain, addr)
        r = reason_of(labels, curated, chain, key, (net, key_of(net, addr)) if net else None)
        if r:
            out[f"{chain}:{key}"] = [r["category"], r["title"], r["url"], r["origin"], int(bool(r.get("ofac")))]
    return out


def seed_comment():
    return re.sub(r"^ {4}", "", """    # config/reasons.yaml - curated reasons, ONLY with a public source (leave empty when unsure).
    # - title: Short description of the incident
    #   category: hack            # hack | sanctions | scam | ransomware | law-enforcement | other
    #   date: 2025-01-31
    #   source_url: https://...   # required, public https
    #   chain: eth                # optional: restrict to one chain key
    #   addresses:
    #     - 0x0000000000000000000000000000000000000000
""", flags=re.M)
