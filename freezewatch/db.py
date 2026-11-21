"""SQLite-сховище."""
import sqlite3
from pathlib import Path

from .constants import DB_PATH
from .util import iso, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    chain TEXT NOT NULL,
    tx_id INTEGER NOT NULL,
    destination TEXT,
    function TEXT,
    target TEXT,
    amount REAL,
    raw_data TEXT,
    submitted_at TEXT,
    submit_tx TEXT,
    submitter TEXT,
    executed INTEGER NOT NULL DEFAULT 0,
    executed_at TEXT,
    exec_tx TEXT,
    executor TEXT,
    confirmations INTEGER NOT NULL DEFAULT 0,
    required INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    delay_seconds INTEGER,
    chain_verified INTEGER NOT NULL DEFAULT 0,
    balance_at_detect REAL,
    balance_now REAL,
    balance_checked_at TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chain, tx_id)
);
CREATE INDEX IF NOT EXISTS ix_prop_status ON proposals(chain, status);
CREATE INDEX IF NOT EXISTS ix_prop_exec ON proposals(executed_at);

CREATE TABLE IF NOT EXISTS confirmations (
    chain TEXT NOT NULL,
    tx_id INTEGER NOT NULL,
    signer TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,            -- submit|confirm|revoke|execute|fail
    ts TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    PRIMARY KEY (chain, tx_id, signer, kind, tx_hash)
);
CREATE INDEX IF NOT EXISTS ix_conf_id ON confirmations(chain, tx_id);

CREATE TABLE IF NOT EXISTS blacklist (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    status TEXT NOT NULL,          -- blocked|unblocked
    first_blocked_at TEXT,
    last_change_at TEXT,
    proposal_tx_id INTEGER,
    funds_destroyed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chain, address)
);

-- сирі успішні виклики мультисига TRON (Tronscan), з яких виводяться події
CREATE TABLE IF NOT EXISTS tron_calls (
    tx_hash TEXT PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    block INTEGER,
    signer TEXT NOT NULL,
    method TEXT NOT NULL,          -- submit|confirm|revoke|execute
    tx_id INTEGER,                 -- для confirm/revoke/execute
    destination TEXT,              -- для submit
    data TEXT                      -- для submit (hex без 0x)
);
CREATE INDEX IF NOT EXISTS ix_tc_method ON tron_calls(method, ts_ms);

-- виконані заморожування без on-chain черги (USDC на Ethereum, USDC/USDT на Solana)
CREATE TABLE IF NOT EXISTS freezes (
    source TEXT NOT NULL,          -- usdc_eth | sol_usdc | sol_usdt
    subject TEXT NOT NULL,         -- заблокована адреса (ETH) або токен-акаунт (Solana)
    owner TEXT,                    -- гаманець-власник (Solana) / сама адреса (ETH)
    action TEXT NOT NULL,          -- freeze | unfreeze
    ts TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block INTEGER,
    amount REAL,                   -- баланс: Solana - у момент tx; ETH - поточний
    amount_kind TEXT,              -- at_tx | current
    signers TEXT,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, tx_hash, subject, action)
);
CREATE INDEX IF NOT EXISTS ix_fr_ts ON freezes(source, ts);

CREATE TABLE IF NOT EXISTS authority_state (
    source TEXT PRIMARY KEY,
    authority TEXT NOT NULL,
    m INTEGER, n INTEGER,
    signers TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    chain TEXT NOT NULL,
    tx_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (chain, tx_id, kind)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(proposals)")}
    if "chain_verified" not in cols:  # міграція старих БД
        conn.execute("ALTER TABLE proposals ADD COLUMN chain_verified INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE proposals SET chain_verified=1 WHERE chain='eth' AND destination IS NOT NULL")
        conn.commit()
    return conn


def meta_get(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(conn, key, value):
    if meta_get(conn, key) == (None if value is None else str(value)):
        return  # без змін - не чіпаємо файл БД
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)),
    )


def ensure_proposal(conn, chain, tx_id):
    conn.execute(
        "INSERT OR IGNORE INTO proposals(chain,tx_id,first_seen_at,updated_at) VALUES(?,?,?,?)",
        (chain, tx_id, iso(now_ts()), iso(now_ts())),
    )


def update_proposal(conn, chain, tx_id, **vals):
    if not vals:
        return
    cur = conn.execute(f"SELECT {','.join(vals)} FROM proposals WHERE chain=? AND tx_id=?", (chain, tx_id)).fetchone()
    if cur is not None and all(cur[k] == v for k, v in vals.items()):
        return  # без змін: не чіпаємо updated_at, щоб файл БД не "брудніл" без потреби
    vals["updated_at"] = iso(now_ts())
    sets = ",".join(f"{k}=?" for k in vals)
    conn.execute(f"UPDATE proposals SET {sets} WHERE chain=? AND tx_id=?", (*vals.values(), chain, tx_id))


def add_event(conn, chain, tx_id, signer, kind, ts_iso, tx_hash):
    conn.execute(
        "INSERT OR IGNORE INTO confirmations(chain,tx_id,signer,kind,ts,tx_hash) VALUES(?,?,?,?,?,?)",
        (chain, tx_id, signer or "", kind, ts_iso, tx_hash),
    )


def events_grouped(conn, chain, ids=None):
    """{tx_id: [event dict, ...]} впорядковано за часом."""
    out: dict[int, list[dict]] = {}
    q = "SELECT tx_id,signer,kind,ts,tx_hash FROM confirmations WHERE chain=? ORDER BY ts, rowid"
    for r in conn.execute(q, (chain,)):
        if ids is not None and r["tx_id"] not in ids:
            continue
        out.setdefault(r["tx_id"], []).append(dict(r))
    return out
