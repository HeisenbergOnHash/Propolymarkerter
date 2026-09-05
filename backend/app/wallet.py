"""Wallet service: accounts, balances and a double-entry ledger.

Every movement of funds records a ledger entry so the trail is auditable.
"""
from __future__ import annotations

from typing import Any

from . import config, db

LEDGER_KINDS = {"seed", "deposit", "withdraw", "buy", "sell", "resolved_win",
                "resolved_loss", "transfer_in", "transfer_out"}


class WalletError(ValueError):
    pass


def create_account(name: str, currency: str | None = None) -> dict[str, Any]:
    currency = currency or config.DEFAULT_CURRENCY
    account_id = db.new_id()
    db.execute(
        "INSERT INTO accounts (id, name, currency, cash, created_at) "
        "VALUES (?,?,?,?,?)",
        (account_id, name, currency, 0.0, db.utc_now()),
    )
    deposit(account_id, config.INITIAL_CASH, kind="seed",
            note="initial paper-trading wallet seed")
    acc = get_account(account_id)
    assert acc is not None
    return acc


def get_account(account_id: str) -> dict[str, Any] | None:
    return db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))


def get_balance(account_id: str) -> float:
    row = db.fetch_one("SELECT cash FROM accounts WHERE id = ?", (account_id,))
    if row is None:
        raise WalletError(f"account {account_id} not found")
    return float(row["cash"])


def _require_account(account_id: str) -> None:
    if get_account(account_id) is None:
        raise WalletError(f"account {account_id} not found")


def _apply(account_id: str, amount: float, kind: str, note: str | None,
           ref_id: str | None) -> dict[str, Any]:
    if kind not in LEDGER_KINDS:
        raise WalletError(f"invalid ledger kind {kind}")
    if amount == 0 and kind not in {"buy", "sell", "resolved_loss", "resolved_win"}:
        amount = 0.0
    conn = db.get_conn()
    entry_id = db.new_id()
    with conn:
        conn.execute("UPDATE accounts SET cash = cash + ? WHERE id = ?",
                     (amount, account_id))
        conn.execute(
            "INSERT INTO ledger (id, account_id, amount, kind, note, ref_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (entry_id, account_id, amount, kind, note, ref_id, db.utc_now()),
        )
    return {"id": entry_id, "account_id": account_id, "amount": amount,
            "kind": kind, "note": note, "ref_id": ref_id}


def deposit(account_id: str, amount: float, kind: str = "deposit",
            note: str | None = None, ref_id: str | None = None) -> dict[str, Any]:
    _require_account(account_id)
    if amount <= 0:
        raise WalletError("deposit amount must be positive")
    return _apply(account_id, amount, kind, note, ref_id)


def withdraw(account_id: str, amount: float, note: str | None = None) -> dict[str, Any]:
    _require_account(account_id)
    if amount <= 0:
        raise WalletError("withdraw amount must be positive")
    if get_balance(account_id) < amount:
        raise WalletError("insufficient funds")
    return _apply(account_id, -amount, "withdraw", note, None)


def transfer(from_account: str, to_account: str, amount: float) -> list[dict[str, Any]]:
    _require_account(from_account)
    _require_account(to_account)
    if from_account == to_account:
        raise WalletError("cannot transfer to the same account")
    if amount <= 0:
        raise WalletError("transfer amount must be positive")
    if get_balance(from_account) < amount:
        raise WalletError("insufficient funds in source account")
    return [
        _apply(from_account, -amount, "transfer_out", "outgoing transfer", to_account),
        _apply(to_account, amount, "transfer_in", "incoming transfer", from_account),
    ]


def settle(market_id: str, account_id: str, outcome_id: str, shares: float,
           avg_cost: float, won: bool) -> dict[str, Any]:
    if won:
        realized = shares * (1.0 - avg_cost)
        return _apply(account_id, shares, "resolved_win",
                      f"win settlement on {market_id}/{outcome_id}", market_id)
    realized = -shares * avg_cost
    return _apply(account_id, -realized, "resolved_loss",
                  f"loss settlement on {market_id}/{outcome_id}", market_id)


def ledger(account_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT * FROM ledger WHERE account_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (account_id, limit),
    )