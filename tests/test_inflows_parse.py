from freezewatch import inflows


def test_tron_approval_rows_are_ignored():
    approval = {"type": "Approval", "from": "TA", "value": str(2**256 - 1), "block_timestamp": 1780000000000,
                "transaction_id": "ab"}
    assert inflows.parse_tron_transfer(approval) is None


def test_tron_transfer_row_parsed():
    row = {"type": "Transfer", "from": "TA", "value": "2500000", "block_timestamp": 1780000000000, "transaction_id": "ab"}
    assert inflows.parse_tron_transfer(row) == ("TA", 2.5, 1780000000, "ab", 0)


def test_absurd_amount_rejected():
    row = {"type": "Transfer", "from": "TA", "value": str(10**30), "block_timestamp": 1780000000000, "transaction_id": "ab"}
    assert inflows.parse_tron_transfer(row) is None
