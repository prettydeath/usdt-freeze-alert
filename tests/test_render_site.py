"""site/data.json (read by the React app in web/), site/classic.html and the root-page rules of render.render_site."""
import json
import re

from freezewatch import render
from freezewatch.db import connect

REACT_ROOT = '<!doctype html><html><body><div id="root"></div></body></html>'


def setup(tmp_path, monkeypatch):
    site = tmp_path / "site"
    monkeypatch.setattr(render, "SITE_DIR", site)
    monkeypatch.setattr(render, "DATA_DIR", tmp_path / "data")
    return site, connect(tmp_path / "t.sqlite")


def embedded(html):
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
                      .replace("<\\/", "</"))


def test_data_json_is_exactly_the_embedded_blob(tmp_path, monkeypatch):
    site, conn = setup(tmp_path, monkeypatch)
    payload = render.render_site(conn, {}, stale_days=7, extras=None)
    data = json.loads((site / "data.json").read_text(encoding="utf-8"))
    assert data == embedded((site / "classic.html").read_text(encoding="utf-8"))
    for key in ("generated_at", "chains", "pending", "stale", "supply", "recent", "ex", "live", "status", "signers", "cross",
                "analytics_url", "sources", "flight", "hints"):
        assert key in data, key
    assert data["analytics_url"] == "analytics.json" and "evm" in data["live"]
    assert "_site_changed" not in data           # the private marker is never exported
    assert payload["_site_changed"] is True
    assert (site / "analytics.json").exists() and (site / "health.json").exists()


def test_plain_run_without_the_react_build_keeps_a_working_root_page(tmp_path, monkeypatch):
    site, conn = setup(tmp_path, monkeypatch)
    render.render_site(conn, {}, extras=None)
    root = (site / "index.html").read_text(encoding="utf-8")
    assert 'id="data"' in root and not render.is_react_page(site / "index.html")   # classic page doubles as the root


def test_the_react_root_page_is_never_overwritten(tmp_path, monkeypatch):
    site, conn = setup(tmp_path, monkeypatch)
    site.mkdir(parents=True)
    (site / "index.html").write_text(REACT_ROOT, encoding="utf-8")
    render.render_site(conn, {}, extras=None)
    assert (site / "index.html").read_text(encoding="utf-8") == REACT_ROOT
    assert 'id="data"' in (site / "classic.html").read_text(encoding="utf-8")
    assert render.is_react_page(site / "index.html")


def test_unchanged_data_does_not_rewrite_files_and_reports_no_change(tmp_path, monkeypatch):
    site, conn = setup(tmp_path, monkeypatch)
    render.render_site(conn, {}, extras=None)
    first = {p.name: p.stat().st_mtime_ns for p in site.iterdir() if p.is_file()}
    p2 = render.render_site(conn, {}, extras=None)     # only generated_at differs
    assert p2["_site_changed"] is False
    for name in ("data.json", "classic.html"):
        assert (site / name).stat().st_mtime_ns == first[name], name
