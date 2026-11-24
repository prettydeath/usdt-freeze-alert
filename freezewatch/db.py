"""SQLite storage."""
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
    public_proposal INTEGER NOT NULL DEFAULT 1,   -- 0 = executed action seen only as a token event
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

-- raw successful TRON multisig calls (Tronscan), from which events are derived
CREATE TABLE IF NOT EXISTS tron_calls (
    tx_hash TEXT PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    block INTEGER,
    signer TEXT NOT NULL,
    method TEXT NOT NULL,          -- submit|confirm|revoke|execute
    tx_id INTEGER,                 -- for confirm/revoke/execute
    destination TEXT,              -- for submit
    data TEXT                      -- for submit (hex without 0x)
);
CREATE INDEX IF NOT EXISTS ix_tc_method ON tron_calls(method, ts_ms);

-- executed freezes without an on-chain queue (USDC on Ethereum, USDC/USDT on Solana)
CREATE TABLE IF NOT EXISTS freezes (
    source TEXT NOT NULL,          -- usdc_eth | sol_usdc | sol_usdt
    subject TEXT NOT NULL,         -- blocked address (ETH) or token account (Solana)
    owner TEXT,                    -- owner wallet (Solana) / the address itself (ETH)
    action TEXT NOT NULL,          -- freeze | unfreeze
    ts TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block INTEGER,
    amount REAL,                   -- balance: Solana - at tx time; ETH - current
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

CREATE TABLE IF NOT EXISTS balances (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    token TEXT,
    balance REAL,                  -- latest balance (USDT/USDT0/USDC)
    checked_at TEXT,               -- date of the last check
    frozen_balance REAL,           -- last balance observed while the address was blocked
    frozen_checked_at TEXT,
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS inflows_after_freeze (
    chain TEXT NOT NULL,
    token TEXT,
    frozen_address TEXT NOT NULL,
    sender TEXT NOT NULL,
    amount REAL NOT NULL,
    ts TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (chain, tx_hash, log_index, frozen_address)
);
CREATE INDEX IF NOT EXISTS ix_inflow_ts ON inflows_after_freeze(ts);

CREATE TABLE IF NOT EXISTS inflow_cursor (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    cursor TEXT,                   -- last scanned block (EVM) / timestamp ms (TRON)
    checked_at TEXT,
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS token_events (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- placed | released | destroyed
    address TEXT NOT NULL,
    amount REAL,
    ts TEXT NOT NULL,
    block INTEGER,
    linked INTEGER,                -- 1 = matched to a Safe transaction, 0 = no public proposal, NULL = not processed
    PRIMARY KEY (chain, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS alerts (
    chain TEXT NOT NULL,
    tx_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (chain, tx_id, kind)
);

CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    last_ok TEXT,
    last_error TEXT,
    last_error_at TEXT,
    consec_fail INTEGER NOT NULL DEFAULT 0,
    rows_added INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS near_args (
    tx TEXT PRIMARY KEY,
    args TEXT
);

CREATE TABLE IF NOT EXISTS outflows (
    chain TEXT NOT NULL,
    frozen_address TEXT NOT NULL,
    recipient TEXT NOT NULL,
    amount REAL NOT NULL,
    ts TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL DEFAULT 0,
    in_window INTEGER NOT NULL DEFAULT 0,   -- 1 = between proposal submission and execution
    freeze_ts TEXT NOT NULL,
    PRIMARY KEY (chain, tx_hash, log_index, frozen_address)
);
CREATE INDEX IF NOT EXISTS ix_outflow_addr ON outflows(chain, frozen_address);

CREATE TABLE IF NOT EXISTS outflow_done (
    chain TEXT NOT NULL,
    frozen_address TEXT NOT NULL,
    freeze_ts TEXT NOT NULL,
    checked_at TEXT,
    total_out REAL,
    window_out REAL,
    n_transfers INTEGER,
    truncated INTEGER,           -- 1 = the transfer list hit a cap; NULL = scanned before this was tracked
    PRIMARY KEY (chain, frozen_address, freeze_ts)
);

CREATE TABLE IF NOT EXISTS outflow_cursor (
    chain TEXT NOT NULL,
    frozen_address TEXT NOT NULL,
    freeze_ts TEXT NOT NULL,
    state TEXT NOT NULL,           -- JSON: pending block ranges / TronGrid fingerprint, queries and transfers so far
    updated_at TEXT,
    PRIMARY KEY (chain, frozen_address, freeze_ts)
);

CREATE TABLE IF NOT EXISTS outflow_tx (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    caller TEXT,                   -- who sent the transaction (ETH tx.from / TRON owner_address)
    to_addr TEXT,                  -- contract / address the caller called
    block INTEGER,
    tx_index INTEGER,
    tip_gwei REAL, median_gwei REAL, tip_ratio REAL, flagged INTEGER,   -- priority-fee anomaly (Ethereum, in-window transfers)
    checked_at TEXT,
    PRIMARY KEY (chain, tx_hash)
);

CREATE TABLE IF NOT EXISTS watched (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,         -- a recipient of funds that left a frozen address (hop 1) or of those (hop 2)
    source TEXT NOT NULL,          -- the frozen address it came from
    hop INTEGER NOT NULL,
    received_usd REAL,
    since_ts TEXT NOT NULL,
    scanned_at TEXT,
    PRIMARY KEY (chain, address)
);
CREATE TABLE IF NOT EXISTS watch_moves (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,         -- the watched address that sent the funds
    recipient TEXT NOT NULL,
    amount REAL NOT NULL,
    ts TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL DEFAULT 0,
    own_proposal TEXT,             -- 'none' | 'before' (a proposal on this address already existed) | 'after'
    PRIMARY KEY (chain, tx_hash, log_index, address)
);

CREATE TABLE IF NOT EXISTS exposure (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    kind TEXT NOT NULL,            -- allowance | eip7702
    spender TEXT NOT NULL DEFAULT '',   -- spender / delegate address
    amount REAL,
    note TEXT,
    checked_at TEXT,
    PRIMARY KEY (chain, address, kind, spender)
);

CREATE TABLE IF NOT EXISTS address_labels (
    network TEXT NOT NULL,
    key TEXT NOT NULL,
    entity TEXT, label TEXT, category TEXT, categories TEXT, source TEXT, source_url TEXT, confidence TEXT,
    PRIMARY KEY (network, key)
);
CREATE TABLE IF NOT EXISTS label_checked (
    network TEXT NOT NULL,
    key TEXT NOT NULL,
    checked_at TEXT,
    PRIMARY KEY (network, key)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    frozen_on TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    at_risk TEXT NOT NULL,        -- JSON [[chain, token, balance_at_signal, balance_last_seen]]
    status TEXT NOT NULL,         -- open | resolved | expired
    stage TEXT NOT NULL DEFAULT 'frozen',  -- proposed | frozen
    resolutions TEXT,             -- JSON [{chain, blocked_at, lag_s, moved_before}]
    updated_at TEXT,
    UNIQUE (address, frozen_on)
);
CREATE TABLE IF NOT EXISTS signal_checked (
    address TEXT NOT NULL,
    trigger_ref TEXT NOT NULL,
    checked_at TEXT,
    pending TEXT,                 -- JSON list of chains whose check failed and is retried next run ('' = done)
    risk TEXT,                    -- JSON at-risk entries found so far
    PRIMARY KEY (address, trigger_ref)
);

CREATE TABLE IF NOT EXISTS clusters (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    chains TEXT,
    total_usd REAL,
    first_ts TEXT,
    last_ts TEXT,
    members TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=120, check_same_thread=False)  # optional steps run in guard threads
    conn.row_factory = sqlite3.Row
    sc = {r["name"] for r in conn.execute("PRAGMA table_info(signals)")}
    if sc and "stage" not in sc:  # pre-release shape of the signals table (never populated)
        conn.execute("DROP TABLE signals")
    conn.executescript(SCHEMA)
    sc2 = {r["name"] for r in conn.execute("PRAGMA table_info(signal_checked)")}
    if sc2 and "pending" not in sc2:  # migration: retry failed chain checks instead of skipping them
        conn.execute("ALTER TABLE signal_checked ADD COLUMN pending TEXT")
        conn.execute("ALTER TABLE signal_checked ADD COLUMN risk TEXT")
        conn.commit()
    od = {r["name"] for r in conn.execute("PRAGMA table_info(outflow_done)")}
    if "truncated" not in od:  # migration: truncation tracking
        conn.execute("ALTER TABLE outflow_done ADD COLUMN n_transfers INTEGER")
        conn.execute("ALTER TABLE outflow_done ADD COLUMN truncated INTEGER")
        conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(proposals)")}
    if "public_proposal" not in cols:  # migration of old DBs
        conn.execute("ALTER TABLE proposals ADD COLUMN public_proposal INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    if "chain_verified" not in cols:  # migration of old DBs
        conn.execute("ALTER TABLE proposals ADD COLUMN chain_verified INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE proposals SET chain_verified=1 WHERE chain='eth' AND destination IS NOT NULL")
        conn.commit()
    return conn


def meta_get(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(conn, key, value):
    if meta_get(conn, key) == (None if value is None else str(value)):
        return  # unchanged - leave the DB file alone
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
        return  # unchanged: leave updated_at alone so the DB file is not needlessly "dirtied"
    vals["updated_at"] = iso(now_ts())
    sets = ",".join(f"{k}=?" for k in vals)
    conn.execute(f"UPDATE proposals SET {sets} WHERE chain=? AND tx_id=?", (*vals.values(), chain, tx_id))


def add_event(conn, chain, tx_id, signer, kind, ts_iso, tx_hash):
    conn.execute(
        "INSERT OR IGNORE INTO confirmations(chain,tx_id,signer,kind,ts,tx_hash) VALUES(?,?,?,?,?,?)",
        (chain, tx_id, signer or "", kind, ts_iso, tx_hash),
    )


def events_grouped(conn, chain, ids=None):
    """{tx_id: [event dict, ...]} ordered by time."""
    out: dict[int, list[dict]] = {}
    q = "SELECT tx_id,signer,kind,ts,tx_hash FROM confirmations WHERE chain=? ORDER BY ts, rowid"
    for r in conn.execute(q, (chain,)):
        if ids is not None and r["tx_id"] not in ids:
            continue
        out.setdefault(r["tx_id"], []).append(dict(r))
    return out
