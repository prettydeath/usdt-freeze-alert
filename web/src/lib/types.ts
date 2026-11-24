// Shapes of site/data.json (the object freezewatch/render.py build_payload() produces), analytics.json and the address shards.

export interface ChainMeta {
  label: string
  mech: "onchain" | "safe_api" | "executed_only" | string
  addr?: string
  tx?: string
  threshold?: string
  token?: string
  family?: string
}

export interface Proposal {
  chain: string
  tx_id: number
  function: string
  target: string | null
  amount: number | null
  submitted_at: string | null
  executed_at: string | null
  exec_tx: string | null
  confirmations: number
  required: number | null
  status: string
  delay_seconds: number | null
  balance_now: number | null
  category?: string
}

export interface Recent {
  chain: string
  target: string | null
  owner?: string | null
  function: string
  at: string
  delay: number | null
  tx: string | null
  id?: number
  amount?: number | null
}

/** [chain, exec epoch, delay seconds or -1, class: 1 freeze / 2 destroy / 3 unfreeze] */
export type Ex = [string, number, number, number]

export type Label = [entity: string | null, name: string | null, category: string | null, source: string, url: string | null, confidence: string | null]
/** [category, title, url, origin (curated|label), ofac] */
export type Reason = [string, string, string | null, string, boolean | number]

export interface Signal {
  frozen_on: string
  show: string
  stage: string
  frozen_at: string
  at_risk: [string, string, any, number][]
  resolutions: { chain: string; lag_s: number | null; moved_before: boolean; balance_before: number; balance_at_block: number }[]
}
export interface Signals {
  open: Signal[]
  history: Signal[]
  stats: { signals: number; open: number; resolved_chains: number; median_lag_s: number | null; moved_before_pct: number | null; moved_balance_pct: number | null; open_usd: number; pending_checks?: number }
}

export interface Cluster {
  id: string
  kind: string
  size: number
  chains: string[]
  usd: number
  first: number | null
  last: number | null
  members: [string, string, (Label | null)?][]
}

export interface Outflows {
  checked: number
  with_out: number
  truncated: number
  moved_12m: number
  internal_12m: number
  service_12m: number
  service_n: number
  window_12m: number
  frozen_12m: number
  top_entities: { entity: string; category: string; usd: number; n: number; url: string | null }[]
  frontrun: { chain: string; address: string; freeze_ts: string; window_out: number; frozen_usd: number; share: number; label: string | null }[]
  n_frontrun: number
  /** exit classes: cex | bridge | dex | mixer | watchlist | unlabelled */
  exits?: Record<string, { n: number; usd: number }>
  /** outcome classes: clean | racing | partial | weak */
  classes?: Record<string, { n: number; escaped: number }>
  lead?: { n: number; median_after_submit_s: number | null; median_before_execute_s: number | null; first_after_submit_s: number | null } | null
  third_party?: { n: number; usd: number; looked_up: number }
  sweepers?: { contract: string; victims: number; callers: string[] }[]
  repeat?: { chain: string; recipient: string; sources: number; usd: number }[]
  fee_collectors?: number
  fee_flagged?: number
}

/** one row of data.flight.recent (pre-proposal flights, last 24 h, moves of at least $1,000) */
export interface FlightRow {
  ts: number
  chain: string
  address: string
  amount: number
  recipient: string
  label: Label | null
  source: string
  hop: number
  freeze_ts: number | null
  /** watching | confirmed (its own proposal appeared) | frozen | proposal existed */
  status: string
  tx: string | null
  alerted?: boolean
}

export interface Flight { watched: number; hop1: number; hop2: number; moves: number; moved_usd: number; confirmed: number; confirmed_usd: number; unfrozen_yet?: number; moves_24h?: number; moved_24h_usd?: number; alerts_24h?: number; recent?: FlightRow[] }

export interface Reasons {
  freezes: number; known: number; unknown: number; known_usd: number; unknown_usd: number; curated: number
  unique: number; known_unique: number; known_pct: number | null
  by_category: Record<string, { n: number; usd: number }>
  ofac: { n: number; usd: number; unique: number; top: { chain: string; address: string; usd: number; entity?: string; url?: string | null }[] }
  sanctioned_other: number
}

export interface PulseSigner { signer: string; n: number; months: Record<string, number>; median_confirm_s: number | null; last_n: number; last_share: number | null }
export interface Pulse { signers: Record<string, PulseSigner[]>; speed: Record<string, Record<string, number>>; queue: [number, number][] }

export interface SourceStatus {
  source: string; label: string; status: "ok" | "degraded" | "failing" | string
  last_ok: string | null; last_error: string | null; last_error_at: string | null; fails: number
  rows_added: number; coverage: string; partial: boolean; gap: number | null; note: string | null
}
export interface Status {
  detect?: { median_s: number; p90_s: number; n: number } | null
  run?: { at: string; run_s: number; deadline_s: number; steps: [string, number, string, (string | number)?][]; tron_keys?: string }
  overall: string
  bad: string[]
  sources: SourceStatus[]
}

export interface Signer { signer: string; submits: number; confirms: number; revokes: number; avg_confirm_latency: number | null; median_confirm_latency: number | null }

export interface CrossRow { address: string; n_chains: number; first: string; last: string; items: { chain: string; class: string; status: string; tx_id: number; function: string; ts: string }[] }

export interface LiveEvm { id: string; chain: string; label: string; contract: string; sel: string; rpcs: string[] }
export interface LiveCfg {
  evm: LiveEvm[]
  tron?: { id: string; chain: string; label: string; endpoint: string; contract: string; sel: string; balance: string }
  solana?: { endpoint: string; mints: Record<string, string> }
  near?: { endpoint: string; contract: string; method: string; label: string }
  aptos?: { endpoint: string; function: string; label: string }
  ton?: { endpoint: string; master: string; label: string }
  timeout: number
  providers: string[]
}

export interface Data {
  signals: Signals | null
  clusters: { top: Cluster[]; counts: Record<string, number> } | null
  outflows: Outflows | null
  reasons: Reasons | null
  pulse: Pulse | null
  flight?: Flight | null
  hints: Record<string, Reason>
  status: Status
  generated_at: string
  stale_days: number
  live: LiveCfg
  stale_total: number
  chains: Record<string, ChainMeta>
  pending: Proposal[]
  supply: Proposal[]
  stale: Proposal[]
  stale_by_chain: Record<string, [number, number]>
  ex: Ex[]
  recent: Recent[]
  signers: Record<string, Signer[]>
  coverage: Record<string, { proposals: number; executed: number; freezes: number; no_public: number }>
  others: Record<string, { freezes: number; coverage?: { complete: boolean } }>
  health: Record<string, { gap: number; max_id: number; onchain_count: number; stale: boolean }> | null
  authority: unknown[]
  cross: CrossRow[]
  cross_total: number
  analytics: Analytics | null
  analytics_url: string
  sources: string[]
}

export interface Analytics {
  idx: string[]
  monthly: Record<string, Record<string, number[]>>
  tokens: Record<string, string>
  top: [string, string, number, number][]
  unique?: { freezes: number; addresses: number }
  coverage: { checked: number; total: number; rows: number }
  still_open: number
  inflow?: {
    periods: Record<string, Record<string, number[]>>
    senders: Record<string, [string, string, number, number][]>
    receivers: Record<string, [string, string, number, number, number][]>
    coverage?: [number, number]
    excluded?: { usd: number; n: number }
  }
  supply?: Record<string, Record<string, number[]>>
  generated_at: string
}

/** compact per-address record from the address index (index key -> record) */
export interface AddrRecord {
  /** executed: [chain, kind f|u|b, ts, tx, amount, delay, tokenAccount, proposalId, sigs, req, submittedTs, hadProposal] */
  e?: any[][]
  b?: Record<string, number>
  bc?: Record<string, string>
  i?: Record<string, [number, number, [string, number, number][], number?, number?]>
  o?: Record<string, any[][]>
  c?: string[]
  s?: Record<string, "frozen" | "unfrozen" | "burned">
  /** pending/stale proposals: [chain, id, function, sigs, required, submittedTs, status, balance] */
  p?: any[][]
  x?: any[][]
  /** exposure at proposal time: [kind allowance|eip7702, spender, amount, note] */
  ex?: any[][]
  /** hop-ahead watch: [hop, source frozen address, USD received, USD moved on] */
  w?: any[]
  l?: Record<string, Label>
  r?: Record<string, Reason>
}
