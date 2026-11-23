"""Configuration and reference calldata helpers for the dashboard's opt-in "Check live on-chain" feature.

The check runs in the visitor's browser (JSON-RPC eth_call / TronGrid / Solana RPC) and sends the address to the
providers listed below - the index lookup does not. Every RPC listed was tested headlessly for browser CORS access
(a POST from an https/http page origin returns a valid eth_call result); chains that could not be made browser-safe
would simply be left out (none at the time of writing). The Python helpers mirror the JS builders so both sides are
unit-tested against each other (tests/test_livecheck.py)."""
from .constants import ETH_USDT, SOLANA_SOURCES, TRON_USDT, USDC_ETH
from .registry import EVENT_CHAINS, MSW_CHAINS, SAFE_CHAINS

SEL_IS_BLACKLISTED = "e47d6060"   # legacy USDT: isBlackListed(address)
SEL_IS_BLACKLISTED_USDC = "fe575a87"  # USDC: isBlacklisted(address)
SEL_IS_BLOCKED = "fbac3951"       # USDT / USDT0 (new generation): isBlocked(address)
SEL_BALANCE_OF = "70a08231"
TIMEOUT_MS = 6000
TRONGRID = "https://api.trongrid.io"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# browser-safe (CORS-enabled) public RPCs, primary first; verified headlessly
BROWSER_RPCS = {
    "eth": ["https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"],
    "avax": ["https://api.avax.network/ext/bc/C/rpc", "https://avalanche-c-chain-rpc.publicnode.com"],
    "celo": ["https://forno.celo.org", "https://celo-rpc.publicnode.com"],
    "kaia": ["https://public-en.node.kaia.io"],
    "kava": ["https://kava-evm-rpc.publicnode.com", "https://rpc.ankr.com/kava_evm"],
    "arb1": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"],
    "pol": ["https://polygon-bor-rpc.publicnode.com"],
    "plasma": ["https://rpc.plasma.to"],
    "mantle": ["https://rpc.mantle.xyz", "https://mantle-rpc.publicnode.com"],
    "oeth": ["https://mainnet.optimism.io", "https://optimism-rpc.publicnode.com"],
    "monad": ["https://rpc.monad.xyz"],
    "unichain": ["https://mainnet.unichain.org", "https://unichain-rpc.publicnode.com"],
    "stable": ["https://rpc.stable.xyz"],
    "morph": ["https://rpc.morphl2.io", "https://rpc-quicknode.morphl2.io"],
    "ink": ["https://rpc-gel.inkonchain.com", "https://rpc-qnd.inkonchain.com"],
    "hyper": ["https://rpc.hyperliquid.xyz/evm", "https://hyperliquid-json-rpc.stakely.io"],
    "berachain": ["https://rpc.berachain.com", "https://berachain-rpc.publicnode.com"],
    "xlayer": ["https://rpc.xlayer.tech", "https://xlayerrpc.okx.com"],
    "flare": ["https://flare-api.flare.network/ext/C/rpc", "https://rpc.ankr.com/flare"],
    "conflux": ["https://evm.confluxrpc.com"],
    "sei": ["https://evm-rpc.sei-apis.com", "https://sei-evm-rpc.publicnode.com"],
    "megaeth": ["https://mainnet.megaeth.com/rpc"],
    "rootstock": ["https://public-node.rsk.co"],
}


def evm_calldata(selector, addr20):
    """selector (8 hex, no 0x) + address left-padded to 32 bytes."""
    a = addr20.lower().removeprefix("0x")
    assert len(a) == 40
    return "0x" + selector + "0" * 24 + a


def tron_param(addr20):
    return "0" * 24 + addr20.lower().removeprefix("0x")


def decode_bool(result):
    return int(result, 16) != 0 if isinstance(result, str) and len(result) == 66 else None


def decode_units(result, decimals=6):
    return int(result, 16) / 10 ** decimals


def targets():
    """The live checks the dashboard offers: EVM eth_call targets, TRON and Solana."""
    out = [
        {"id": "eth_usdt", "chain": "eth", "label": "Ethereum USDT", "contract": ETH_USDT, "sel": SEL_IS_BLACKLISTED,
         "rpcs": BROWSER_RPCS["eth"]},
        {"id": "eth_usdc", "chain": "usdc_eth", "label": "Ethereum USDC", "contract": USDC_ETH,
         "sel": SEL_IS_BLACKLISTED_USDC, "rpcs": BROWSER_RPCS["eth"]},
    ]
    for chain, cfg in {**MSW_CHAINS, **SAFE_CHAINS, **EVENT_CHAINS}.items():
        out.append({"id": chain, "chain": chain, "label": cfg["label"].replace(" - ", " "), "contract": cfg["token"],
                    "sel": SEL_IS_BLOCKED, "rpcs": BROWSER_RPCS[chain]})
    return {
        "evm": out,
        "tron": {"id": "tron", "chain": "tron", "label": "TRON USDT", "endpoint": TRONGRID, "contract": TRON_USDT,
                 "sel": "isBlackListed(address)", "balance": "balanceOf(address)"},
        "solana": {"endpoint": SOLANA_RPC, "mints": {"sol_usdc": SOLANA_SOURCES["sol_usdc"]["mint"],
                                                    "sol_usdt": SOLANA_SOURCES["sol_usdt"]["mint"]}},
        "timeout": TIMEOUT_MS,
        "providers": sorted({u.split("//")[1].split("/")[0] for t in out for u in t["rpcs"]} |
                            {TRONGRID.split("//")[1], SOLANA_RPC.split("//")[1]}),
    }
