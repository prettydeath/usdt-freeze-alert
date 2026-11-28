"""Constants: addresses, selectors, topic hashes (keccak computed at development time)."""
from pathlib import Path
import os

# Pre-proposal flight watch keeps running, but is hidden from the site and Telegram unless FLIGHT_PUBLIC=1
FLIGHT_PUBLIC = os.environ.get("FLIGHT_PUBLIC", "0") == "1"
ROOT = Path(os.environ.get("FREEZEWATCH_HOME") or Path(__file__).resolve().parent.parent)
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"
DB_PATH = DATA_DIR / "freezewatch.sqlite"

ETH_USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ETH_MULTISIG = "0xc6cde7c39eb2f0f0095f41570af89efc2c1ea828"
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRON_MULTISIG = "TBPxhVAsuzoFnKyXtc1o2UySEydPHgATto"
TRON_DUMMY_CALLER = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"

USDT_DECIMALS = 6

# USDT selectors: selector -> (name, argument type)
USDT_SELECTORS = {
    "0ecb93c0": ("addBlackList", "address"),
    "e4997dc5": ("removeBlackList", "address"),
    "f3bdc228": ("destroyBlackFunds", "address"),
    "cc872b66": ("issue", "amount"),
    "db006a75": ("redeem", "amount"),
    "f2fde38b": ("transferOwnership", "address"),
    "8456cb59": ("pause", "none"),
    "3f4ba83a": ("unpause", "none"),
    "0753c30c": ("deprecate", "address"),
    "c0324c77": ("setParams", "raw"),
    # new-generation TetherToken / USDT0 (WithBlockedList)
    "40c10f19": ("mint", "address_amount"),
    "9dc29fac": ("burn", "address_amount"),
    "8040a2c7": ("setOFTContract", "address"),
    "f32a12ac": ("setTrusted", "address"),
    "3c7c9b90": ("addToBlockedList", "address"),
    "1a14f449": ("removeFromBlockedList", "address"),
    "0e27a385": ("destroyBlockedFunds", "address"),
}
SIG = {
    "addBlackList": "addBlackList(address)",
    "removeBlackList": "removeBlackList(address)",
    "destroyBlackFunds": "destroyBlackFunds(address)",
    "issue": "issue(uint256)",
    "redeem": "redeem(uint256)",
    "transferOwnership": "transferOwnership(address)",
    "pause": "pause()",
    "unpause": "unpause()",
    "deprecate": "deprecate(address)",
    "setParams": "setParams(uint256,uint256)",
    "mint": "mint(address,uint256)",
    "burn": "burn(address,uint256)",
    "setOFTContract": "setOFTContract(address)",
    "setTrusted": "setTrusted(address,bool)",
    "addToBlockedList": "addToBlockedList(address)",
    "removeFromBlockedList": "removeFromBlockedList(address)",
    "destroyBlockedFunds": "destroyBlockedFunds(address)",
    "isBlackListed": "isBlackListed(address)",
    "balanceOf": "balanceOf(address)",
    "transactions": "transactions(uint256)",
    "getConfirmationCount": "getConfirmationCount(uint256)",
    "required": "required()",
    "transactionCount": "transactionCount()",
    "submitTransaction": "submitTransaction(address,uint256,bytes)",
    "confirmTransaction": "confirmTransaction(uint256)",
    "revokeConfirmation": "revokeConfirmation(uint256)",
    "executeTransaction": "executeTransaction(uint256)",
}
SEL_IS_BLACKLISTED = "e47d6060"
SEL_BALANCE_OF = "70a08231"
SEL_TRANSACTIONS = "9ace38c2"
SEL_CONF_COUNT = "8b51d13f"
SEL_REQUIRED = "dc8452cd"
SEL_TX_COUNT = "b77bf600"
SEL_SUBMIT = "c6427474"
SEL_CONFIRM = "c01a8c84"
SEL_REVOKE = "20ea8d86"
SEL_EXECUTE = "ee22610b"

# topic0 of Gnosis MultiSigWallet events
TOPIC_SUBMISSION = "0xc0ba8fe4b176c1714197d43b9cc6bcf797a4a7461c5fe8d0ef6e184ae7601e51"
TOPIC_CONFIRMATION = "0x4a504a94899432a9846e1aa406dceb1bcfd538bb839071d49d1e5e23f5be30ef"
TOPIC_REVOCATION = "0xf6a317157440607f36269043eb55f1287a5a19ba2216afeab88cd46cbcfb88e9"
TOPIC_EXECUTION = "0x33e13ecb54c3076d8e8bb8c2881800a4d972b792045ffae98fdf46df365fed75"
TOPIC_EXEC_FAILURE = "0x526441bb6c1aba3c9a4a6ca1d6545da9c2333c8c48343ef398eb858d72b79236"
TOPIC_SIGS = {
    TOPIC_SUBMISSION: "Submission(uint256)",
    TOPIC_CONFIRMATION: "Confirmation(address,uint256)",
    TOPIC_REVOCATION: "Revocation(address,uint256)",
    TOPIC_EXECUTION: "Execution(uint256)",
    TOPIC_EXEC_FAILURE: "ExecutionFailure(uint256)",
}

# functions with a target address for which the USDT balance is fetched
BALANCE_FUNCS = ("addBlackList", "destroyBlackFunds", "addToBlockedList", "destroyBlockedFunds")
# action classes for the cross-chain view
FREEZE_FUNCS = ("addBlackList", "addToBlockedList", "freeze_accounts", "freeze_wallet")
UNFREEZE_FUNCS = ("removeBlackList", "removeFromBlockedList", "unfreeze_accounts", "unfreeze_wallet")
DESTROY_FUNCS = ("destroyBlackFunds", "destroyBlockedFunds", "burn_from")
SUPPLY_FUNCS = ("issue", "mint", "redeem", "burn")  # treasury supply operations, unrelated to freezes


def category_of(function):
    """freeze | unfreeze | destroy | supply | admin (any other call)."""
    if function in FREEZE_FUNCS:
        return "freeze"
    if function in UNFREEZE_FUNCS:
        return "unfreeze"
    if function in DESTROY_FUNCS:
        return "destroy"
    if function in SUPPLY_FUNCS:
        return "supply"
    return "admin"
SEL_IS_BLOCKED = "fbac3951"
TOPIC_BLOCK_PLACED = "0x406bbf2d8d145125adf1198d2cf8a67c66cc4bb0ab01c37dccd4f7c0aae1e7c7"
TOPIC_BLOCK_RELEASED = "0x665918c9e02eb2fd85acca3969cb054fc84c138e60ec4af22ab6ef2fd4c93c27"
TOPIC_DESTROYED_BLOCKED = "0x6a2859ae7902313752498feb80a014e6e7275fe964c79aa965db815db1c7f1e9"

# ---- USDC (Ethereum) ----
USDC_ETH = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
TOPIC_USDC_BLACKLISTED = "0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855"
TOPIC_USDC_UNBLACKLISTED = "0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e"
SEL_USDC_BLACKLIST = "f9f92be4"
SEL_USDC_UNBLACKLIST = "1a895266"
SEL_USDC_IS_BLACKLISTED = "fe575a87"
USDC_SIGS = {"blacklist": "blacklist(address)", "unBlacklist": "unBlacklist(address)",
             "isBlacklisted": "isBlacklisted(address)"}
TOPIC_USDC_SIGS = {TOPIC_USDC_BLACKLISTED: "Blacklisted(address)", TOPIC_USDC_UNBLACKLISTED: "UnBlacklisted(address)"}

# ---- Solana ----
SOLANA_SOURCES = {
    "sol_usdc": {"label": "USDC - Solana", "token": "USDC",
                 "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                 "authority": "7dGbd2QZcCKcTndnHcTL8q7SMVXAkp688NTQYwrRCrar"},
    "sol_usdt": {"label": "USDT - Solana", "token": "USDT",
                 "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                 "authority": "Q6XprfkF8RQQKoQVG33xT88H7wi8Uk1B1CC7YAs69Gi"},
}
SOURCE_LABELS = {"usdc_eth": "USDC - Ethereum", **{k: v["label"] for k, v in SOLANA_SOURCES.items()}}
