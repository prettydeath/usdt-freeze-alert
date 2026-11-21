"""Network registry: freeze mechanism, multisigs, sources. The single place for configuration."""
from .constants import ETH_USDT, TRON_USDT

# mechanisms (dashboard badges)
MECH_ONCHAIN = "onchain"        # proposal is visible on-chain (multisig contract)
MECH_SAFE = "safe_api"          # proposal is visible in Safe Transaction Service (off-chain, not guaranteed)
MECH_EXECUTED = "executed_only"  # executed actions only
MECH_LABELS = {MECH_ONCHAIN: "on-chain pending", MECH_SAFE: "Safe API pending", MECH_EXECUTED: "executed only"}

# Gnosis MultiSigWallet networks (Tier A, EVM): reuses ETH logic
MSW_CHAINS = {
    "avax": {"label": "USDt - Avalanche", "rpc": "https://api.avax.network/ext/bc/C/rpc",
             "token": "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",
             "msw": "0xd83d5c96bfb9e5f890e8be48165b13ddb0ecd2aa",
             "logs": {"kind": "compat", "url": "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api"},
             "addr": "https://snowscan.xyz/address/", "tx": "https://snowscan.xyz/tx/", "threshold": "3 of 6"},
    "celo": {"label": "USDT - Celo", "rpc": "https://forno.celo.org",
             "token": "0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e",
             "msw": "0xba2a995bd4ab9e605454ccef88169352cd5f75a6",
             "logs": {"kind": "etherscan_v2", "chainid": 42220},
             "addr": "https://celoscan.io/address/", "tx": "https://celoscan.io/tx/", "threshold": "3 of 6"},
    "kaia": {"label": "USDT - Kaia", "rpc": "https://public-en.node.kaia.io",
             "token": "0xd077a400968890eacc75cdc901f0356c943e4fdb",
             "msw": "0xba2a995bd4ab9e605454ccef88169352cd5f75a6",
             "logs": {"kind": "rpc", "chunk": 2_000_000},
             "addr": "https://kaiascan.io/account/", "tx": "https://kaiascan.io/tx/", "threshold": "3 of 6"},
    "kava": {"label": "USDT - Kava EVM", "rpc": "https://kava-evm-rpc.publicnode.com",
             "token": "0x919c1c267bc06a7039e03fcc2ef738525769109c",
             "msw": "0x10b4af8e4efb44a953081c36a3f6ea2c0e9b4e8a",
             "logs": {"kind": "rpc", "url": "https://rpc.ankr.com/kava_evm", "chunk": 10_000, "parallel": 4},
             "addr": "https://kavascan.com/address/", "tx": "https://kavascan.com/tx/", "threshold": "3 of 6"},
}

# Safe 3/5 (Tier B, USDT0): net -> (safe, token, explorer)
MAIN_SAFE = "0x4DFF9b5b0143E642a3F63a5bcf2d1C328e600bf8"
SAFE_CHAINS = {
    "arb1": {"label": "USDT0 - Arbitrum", "safe": MAIN_SAFE, "token": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
             "addr": "https://arbiscan.io/address/", "tx": "https://arbiscan.io/tx/"},
    "pol": {"label": "USDT0 - Polygon", "safe": MAIN_SAFE, "token": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            "addr": "https://polygonscan.com/address/", "tx": "https://polygonscan.com/tx/"},
    "plasma": {"label": "USDT0 - Plasma", "safe": MAIN_SAFE, "token": "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb",
               "addr": "https://plasmascan.to/address/", "tx": "https://plasmascan.to/tx/"},
    "mantle": {"label": "USDT0 - Mantle", "safe": MAIN_SAFE, "token": "0x779Ded0c9e1022225f8E0630b35a9b54bE713736",
               "addr": "https://mantlescan.xyz/address/", "tx": "https://mantlescan.xyz/tx/"},
    "oeth": {"label": "USDT0 - Optimism", "safe": MAIN_SAFE, "token": "0x01bFF41798a0BcF287b996046Ca68b395DbC1071",
             "addr": "https://optimistic.etherscan.io/address/", "tx": "https://optimistic.etherscan.io/tx/"},
    "monad": {"label": "USDT0 - Monad", "safe": MAIN_SAFE, "token": "0xe7cd86e13AC4309349F30B3435a9d337750fC82D",
              "addr": "https://monadscan.com/address/", "tx": "https://monadscan.com/tx/"},
    "unichain": {"label": "USDT0 - Unichain", "safe": MAIN_SAFE, "token": "0x9151434b16b9763660705744891fA906F660EcC5",
                 "addr": "https://uniscan.xyz/address/", "tx": "https://uniscan.xyz/tx/"},
    "stable": {"label": "USDT0 - Stable", "safe": MAIN_SAFE, "token": "0x779Ded0c9e1022225f8E0630b35a9b54bE713736",
               "addr": "https://stablescan.xyz/address/", "tx": "https://stablescan.xyz/tx/"},
    "morph": {"label": "USDT0 - Morph", "safe": MAIN_SAFE, "token": "0xe7cd86e13AC4309349F30B3435a9d337750fC82D",
              "addr": "https://explorer.morphl2.io/address/", "tx": "https://explorer.morphl2.io/tx/"},
    "ink": {"label": "USDT0 - Ink", "safe": "0xc95de55ce5e93f788A1Faab2A9c9503F51a5dAE2",
            "token": "0x0200C29006150606B650577BBE7B6248F58470c1",
            "addr": "https://explorer.inkonchain.com/address/", "tx": "https://explorer.inkonchain.com/tx/"},
    "hyper": {"label": "USDT0 - HyperEVM", "safe": "0xB64A89AD247a2D691A728Bb6822a85EeDD7Fc541",
              "token": "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb",
              "addr": "https://hyperevmscan.io/address/", "tx": "https://hyperevmscan.io/tx/"},
    "berachain": {"label": "USDT0 - Berachain", "safe": "0x425d1D17C33bdc0615eA18D1b18CCA7e14bEeb58",
                  "token": "0x779Ded0c9e1022225f8E0630b35a9b54bE713736",
                  "addr": "https://berascan.com/address/", "tx": "https://berascan.com/tx/"},
}
for _k, _u in {'arb1': 'https://arb1.arbitrum.io/rpc', 'pol': 'https://polygon-bor-rpc.publicnode.com', 'plasma': 'https://rpc.plasma.to', 'mantle': 'https://rpc.mantle.xyz', 'oeth': 'https://mainnet.optimism.io', 'monad': 'https://rpc.monad.xyz', 'unichain': 'https://mainnet.unichain.org', 'stable': 'https://rpc.stable.xyz', 'morph': 'https://rpc.morphl2.io', 'ink': 'https://rpc-gel.inkonchain.com', 'hyper': 'https://rpc.hyperliquid.xyz/evm', 'berachain': 'https://rpc.berachain.com'}.items():
    SAFE_CHAINS[_k]["rpc"] = _u

# non-EVM
NON_EVM = {
    "near": {"label": "USDt - NEAR", "addr": "https://nearblocks.io/address/", "tx": "https://nearblocks.io/txns/",
             "threshold": "2 of 3"},
    "aptos": {"label": "USDt - Aptos", "addr": "https://explorer.aptoslabs.com/account/",
              "tx": "https://explorer.aptoslabs.com/txn/", "threshold": "2 of 3"},
    "ton": {"label": "USDT - TON", "addr": "https://tonviewer.com/", "tx": "https://tonviewer.com/transaction/",
            "threshold": "2 of 3"},
}


def chain_meta():
    """{chain_key: {label, mech, addr, tx, threshold}} for all networks in the proposals table."""
    m = {
        "eth": {"label": "USDT - Ethereum", "mech": MECH_ONCHAIN, "addr": "https://etherscan.io/address/",
                "tx": "https://etherscan.io/tx/", "threshold": "3 of 6", "token": ETH_USDT},
        "tron": {"label": "USDT - TRON", "mech": MECH_ONCHAIN, "addr": "https://tronscan.org/#/address/",
                 "tx": "https://tronscan.org/#/transaction/", "threshold": "2 of 3", "token": TRON_USDT},
    }
    for k, c in MSW_CHAINS.items():
        m[k] = {"label": c["label"], "mech": MECH_ONCHAIN, "addr": c["addr"], "tx": c["tx"],
                "threshold": c["threshold"], "token": c["token"]}
    for k, c in SAFE_CHAINS.items():
        m[k] = {"label": c["label"], "mech": MECH_SAFE, "addr": c["addr"], "tx": c["tx"], "threshold": "3 of 5",
                "token": c["token"]}
    for k, c in NON_EVM.items():
        m[k] = {"label": c["label"], "mech": MECH_ONCHAIN, "addr": c["addr"], "tx": c["tx"],
                "threshold": c["threshold"]}
    return m


# networks where the target address is a 20-byte EVM-compatible one (for cross-chain matching); TRON is converted separately
EVM_CHAINS = ["eth", *MSW_CHAINS, *SAFE_CHAINS]
