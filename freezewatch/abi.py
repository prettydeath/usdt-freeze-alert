"""Декодування ABI: результат transactions(uint256) та calldata USDT."""
from .constants import USDT_SELECTORS, USDT_DECIMALS


def decode_transactions_result(h: str) -> dict:
    """(address destination, uint value, bytes data, bool executed).
    Слово 0 - dest, 1 - value, 2 - зміщення data, 3 - executed; data за зміщенням."""
    h = h.lower().removeprefix("0x")
    if len(h) < 64 * 5:
        raise ValueError(f"закоротка відповідь transactions(): {len(h)} hex")
    dest = h[24:64]
    value = int(h[64:128], 16)
    off = int(h[128:192], 16) * 2
    executed = int(h[192:256], 16) != 0
    ln = int(h[off:off + 64], 16) * 2
    data = h[off + 64:off + 64 + ln]
    if len(data) != ln:
        raise ValueError("data обрізана")
    return {"destination_hex": dest, "value": value, "data": data, "executed": executed}


def word_addr(word_hex: str) -> str:
    """Остання 20 байт слова (TRON-обгортка з 0x41 перед адресою теж відкидається)."""
    return word_hex[-40:].lower()


def decode_calldata(data_hex: str, is_usdt: bool, fmt_addr) -> dict:
    """{function, target, amount(USDT)}; fmt_addr(hex20) -> адреса потрібного ланцюга."""
    d = data_hex.lower().removeprefix("0x")
    if not is_usdt:
        return {"function": "other_contract", "target": None, "amount": None}
    if len(d) < 8:
        return {"function": "raw", "target": None, "amount": None}
    sel, args = d[:8], d[8:]
    meta = USDT_SELECTORS.get(sel)
    if not meta:
        return {"function": "0x" + sel, "target": None, "amount": None}
    name, kind = meta
    out = {"function": name, "target": None, "amount": None}
    if kind == "address" and len(args) >= 64:
        out["target"] = fmt_addr(word_addr(args[:64]))
    elif kind == "amount" and len(args) >= 64:
        out["amount"] = int(args[:64], 16) / 10 ** USDT_DECIMALS
    return out
