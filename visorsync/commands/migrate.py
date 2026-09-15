"""Phase 2: one-off transactions, installment plans, retroactive balances and
recurring-pattern category fixes -- idempotent via state.db.

Account/card resolution reads state.db (populated by `sync-structure`,
which resolves Organizze accounts to Visor accounts/cards by name) rather
than any hardcoded table, so no personal account IDs live in this repo.
Recurring-pattern names/amounts are fetched live from Visor -- never
hardcoded either.

Field names read from MCP responses (`description`, `amount`, `account`,
`invoice_due_date`, etc.) follow the shape observed during the manual chat
migration; since this CLI hasn't run against the real servers outside a
chat session yet, double check the exact schema on first real run and
adjust the `_extract_*` helpers below if a field name has changed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from rich.console import Console

from visorsync.config import Settings
from visorsync.mapping.balances import (
    TransactionForBalance,
    bank_balance_at_date,
    credit_card_balance_at_date,
)
from visorsync.mapping.categories import load_category_config
from visorsync.mapping.installments import (
    RawInstallmentOccurrence,
    resolve_installment_series,
    series_active_in_range,
)
from visorsync.mapping.money import parse_brl
from visorsync.mapping.recurring import index_by_name
from visorsync.mcp_clients.base import RateLimitError
from visorsync.mcp_clients.organizze_client import OrganizzeClient
from visorsync.mcp_clients.visor_client import VisorClient
from visorsync.state.store import EntityRecord, StateStore

console = Console()


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _month(d: str) -> str:
    return d[:7]


def _extract_id(result: object, *candidate_keys: str) -> str | None:
    """Best-effort id extraction from a write tool's result.

    Only *input* schemas were confirmed against the real Visor MCP tools;
    their result shapes aren't documented, and calling them just to see the
    shape would create real data in the user's account. Tries a few common
    key names and a one-level {"data": {...}}-style wrapper; returns None
    (never raises) so the caller can log and move on instead of crashing
    the whole batch over one unexpected response shape.
    """
    if not isinstance(result, dict):
        return None
    for key in candidate_keys:
        if key in result:
            return result[key]
    for wrapper_key in ("data", "result", "transaction", "installment_plan", "plan"):
        wrapped = result.get(wrapper_key)
        if isinstance(wrapped, dict):
            for key in candidate_keys:
                if key in wrapped:
                    return wrapped[key]
    return None


def _account_kind(record: EntityRecord) -> str:
    return json.loads(record.extra_json or "{}").get("kind", "bank")


async def run(
    settings: Settings,
    store: StateStore,
    *,
    since: str,
    until: str,
    dry_run: bool,
) -> None:
    category_config = load_category_config(settings.config_dir)

    async with OrganizzeClient(settings) as organizze, VisorClient(settings) as visor:
        recurring_names = await _fetch_recurring_names(visor)

        # Balances first, deliberately: update_account_settings has no
        # historical/dated-balance concept -- it just sets the account's
        # "current balance" field, which Visor's manual accounts then carry
        # forward as transactions are added on top (starting-balance +
        # forward ledger, like a real account). Setting it to the --since
        # anchor AFTER transactions already existed would just overwrite
        # away the effect of everything just added. Idempotent re-runs are
        # still safe: needs_resync-style balance tracking isn't done here,
        # so this always re-applies the same computed anchor for `since`.
        try:
            await _apply_retroactive_balances(organizze, visor, store, since=since, dry_run=dry_run)
            await _migrate_installments(organizze, visor, store, settings, since=since, until=until, dry_run=dry_run)
            await _migrate_loose_transactions(
                organizze, visor, store, category_config, recurring_names, since=since, until=until, dry_run=dry_run
            )
            await _fix_known_miscategorized_patterns(visor, category_config, dry_run=dry_run)
        except RateLimitError as exc:
            # Visor enforces a hard cap ("200 changes per hour through the
            # assistant") on write tools. Retrying immediately can't help,
            # and continuing would just burn through the rest of the batch
            # generating identical failures. Stop cleanly instead: nothing
            # that failed got written to state.db, so re-running `migrate`
            # later picks up exactly where this left off (idempotent), once
            # the hourly quota resets.
            console.print(
                f"[red]Stopped: Visor rate limit hit -- {exc}[/red]\n"
                "[yellow]Wait for the quota to reset, then re-run the same 'migrate' command -- "
                "already-created accounts/balances/installment plans/transactions won't be "
                "duplicated.[/yellow]"
            )
            return

    console.print("[green]migrate done.[/green]")


async def _fetch_recurring_names(visor: VisorClient) -> set[str]:
    expenses = await visor.get_recurring_expenses()
    incomes = await visor.get_recurring_incomes()
    by_name = index_by_name(
        expenses.get("patterns", expenses) if isinstance(expenses, dict) else expenses,
        incomes.get("patterns", incomes) if isinstance(incomes, dict) else incomes,
    )
    return set(by_name)


def _resolved_account(store: StateStore, organizze_account_name: str) -> EntityRecord | None:
    return store.get_by_organizze_id("account", organizze_account_name)


def _resolve_category_slug(store: StateStore, category_config, organizze_category_name: str) -> str:
    mapping = category_config.find_by_organizze_name(organizze_category_name)
    if mapping is None:
        return "other"
    if not mapping.is_custom:
        return mapping.visor_slug
    # Visor assigns the slug itself on create_category -- categories.yaml's
    # visor_slug is only a hint. Use the real slug sync-structure cached.
    record = store.get_by_organizze_id("category", mapping.organizze_name)
    if record is not None:
        return json.loads(record.extra_json or "{}").get("resolved_slug", mapping.visor_slug)
    return mapping.visor_slug


# -- installment plans --------------------------------------------------------


async def _migrate_installments(
    organizze: OrganizzeClient,
    visor: VisorClient,
    store: StateStore,
    settings: Settings,
    *,
    since: str,
    until: str,
    dry_run: bool,
) -> None:
    lookback_start = (date.fromisoformat(since) - timedelta(days=730)).isoformat()
    raw = await organizze.find_installments(lookback_start, until)
    occurrences = _parse_raw_installments(raw)
    if not occurrences:
        console.print("[yellow]find_installments returned no occurrences.[/yellow]")
        return

    earliest_available_month = min(_month(o.month + "-01") if len(o.month) == 7 else _month(o.month) for o in occurrences)
    resolved, ambiguous = resolve_installment_series(
        occurrences, earliest_available_month=earliest_available_month
    )

    if ambiguous:
        settings.ambiguous_report_path.write_text(
            json.dumps([a.__dict__ for a in ambiguous], indent=2, ensure_ascii=False, default=list)
        )
        console.print(
            f"[yellow]{len(ambiguous)} ambiguous installment series -- review "
            f"{settings.ambiguous_report_path} before re-running.[/yellow]"
        )

    since_month, until_month = _month(since), _month(until)
    for series in resolved:
        if not series_active_in_range(series, since_month, until_month):
            continue

        organizze_key = f"{series.description}|{series.account}|{series.amount_cents}|{series.installments_total}"
        content_hash = _key(organizze_key, series.first_month)
        if not store.needs_resync("installment_plan", organizze_key, content_hash):
            continue

        account_record = _resolved_account(store, series.account)
        if account_record is None:
            console.print(
                f"[yellow]skipping installment plan, no resolved account: {series.account} "
                "(run 'sync-structure' first)[/yellow]"
            )
            continue
        if _account_kind(account_record) != "credit":
            # create_installment_plan only accepts a credit-card account_id.
            # find_installments can surface "installments" on a bank account
            # too (e.g. a split bank loan/deposit) -- there's no Visor tool
            # for that, so it's routed to loose-transaction migration instead
            # by simply not creating a plan; the underlying transactions
            # still get migrated individually by _migrate_loose_transactions.
            console.print(
                f"[yellow]skipping installment plan on non-credit account: "
                f"{series.description} ({series.account}) -- Visor's create_installment_plan "
                "only supports credit cards.[/yellow]"
            )
            continue

        console.print(
            f"installment plan: {series.description} ({series.account}) -- "
            f"{series.installments_total}x of R${series.amount_cents / 100:.2f}, "
            f"starting {series.first_month}"
        )
        if dry_run:
            continue

        # current_installment: which installment is "now" for progress
        # tracking, per the real create_installment_plan schema (there's no
        # aggregate total-amount field -- only the per-installment amount).
        # Clamp today's month into the series range for plans that started
        # in the past or haven't started yet.
        today_month = _month(date.today().isoformat())
        current_installment = series.installment_number_for(today_month)
        if current_installment is None:
            current_installment = 1 if today_month < series.first_month else series.installments_total

        idem_key = _key("create_installment_plan", organizze_key)
        result = await visor.create_installment_plan(
            idempotency_key=idem_key,
            account_id=account_record.visor_id,
            description=series.description,
            installment_amount=f"{series.amount_cents / 100:.2f}",
            total_installments=series.installments_total,
            current_installment=current_installment,
            first_installment_date=f"{series.first_month}-01",
        )
        plan_id = _extract_id(result, "id", "plan_id")
        if plan_id is None:
            console.print(
                f"[red]created installment plan for '{series.description}' but couldn't read its id "
                f"back from the response ({result!r}) -- it won't be tracked in state.db and may be "
                "recreated on the next run. Check the app.[/red]"
            )
            continue
        store.upsert(
            "installment_plan",
            plan_id,
            organizze_id=organizze_key,
            content_hash=content_hash,
            created_by_tool=True,
        )


def _parse_raw_installments(raw: object) -> list[RawInstallmentOccurrence]:
    """Each group from find_installments already carries a `months` list
    (e.g. ["2026-03", "2026-04", ...]) for that (description, account,
    amount, installments_total) combination -- not one row per month. The
    reconstruction algorithm in mapping/installments.py still needs to see
    one occurrence per month (that's the unit it groups/merges fragments
    on), so each group is expanded here.
    """
    rows = raw.get("installments", raw) if isinstance(raw, dict) else raw
    occurrences: list[RawInstallmentOccurrence] = []
    for row in rows or []:
        amount_cents = round(parse_brl(row["amount"]) * 100)
        for month in row.get("months", []):
            occurrences.append(
                RawInstallmentOccurrence(
                    description=row["description"],
                    account=row["account"],
                    amount_cents=amount_cents,
                    installments_total=int(row["installments_total"]),
                    month=month,
                )
            )
    return occurrences


# -- one-off transactions ------------------------------------------------------


async def _migrate_loose_transactions(
    organizze: OrganizzeClient,
    visor: VisorClient,
    store: StateStore,
    category_config,
    recurring_names: set[str],
    *,
    since: str,
    until: str,
    dry_run: bool,
) -> None:
    transactions = await organizze.list_all_transactions(since, until)

    for tx in transactions:
        organizze_id = str(tx.get("id") or tx.get("transaction_id"))
        description = tx.get("description", "")
        if tx.get("installment_id") or tx.get("is_installment"):
            continue  # handled in _migrate_installments
        if description in recurring_names:
            continue  # already covered by the matching recurring pattern

        content_hash = _key(json.dumps(tx, sort_keys=True, default=str))
        if not store.needs_resync("transaction", organizze_id, content_hash):
            continue

        account_record = _resolved_account(store, tx.get("account", ""))
        if account_record is None:
            console.print(
                f"[yellow]skipping transaction, no resolved account: {description} "
                "(run 'sync-structure' first)[/yellow]"
            )
            continue
        visor_category_slug = _resolve_category_slug(store, category_config, tx.get("category", ""))

        # Organizze convention: positive amount = income/credit, negative =
        # expense/debit (see mapping/balances.py). Visor's create_manual_transaction
        # wants a separate, always-positive `amount` plus an explicit `type`.
        amount_cents = round(parse_brl(tx.get("amount", 0)) * 100)
        tx_type = "income" if amount_cents >= 0 else "expense"
        console.print(f"transaction: {tx.get('date')} {description} R${amount_cents / 100:.2f}")
        if dry_run:
            continue

        idem_key = _key("create_manual_transaction", organizze_id)
        result = await visor.create_manual_transaction(
            idempotency_key=idem_key,
            account_id=account_record.visor_id,
            description=description,
            type=tx_type,
            amount=f"{abs(amount_cents) / 100:.2f}",
            date=tx.get("date"),
            category_slug=visor_category_slug,
        )
        transaction_id = _extract_id(result, "id", "transaction_id")
        if transaction_id is None:
            console.print(
                f"[red]created transaction '{description}' but couldn't read its id back from "
                f"the response ({result!r}) -- it won't be tracked in state.db and may be "
                "recreated on the next run. Check the app.[/red]"
            )
            continue
        store.upsert(
            "transaction",
            transaction_id,
            organizze_id=organizze_id,
            content_hash=content_hash,
            created_by_tool=True,
        )


# -- known miscategorized recurring patterns (e.g. Disney+) --------------------


async def _fix_known_miscategorized_patterns(visor: VisorClient, category_config, *, dry_run: bool) -> None:
    if not category_config.known_miscategorized_patterns:
        return
    patterns = await visor.get_recurring_expenses()
    by_name = {p["name"]: p for p in patterns.get("patterns", patterns) if isinstance(p, dict)}

    for fix in category_config.known_miscategorized_patterns:
        pattern = by_name.get(fix.pattern_name)
        if pattern is None or pattern.get("category_slug") == fix.correct_slug:
            continue
        console.print(f"fixing category for '{fix.pattern_name}' -> {fix.correct_slug}")
        if dry_run:
            continue
        try:
            key = _key("update_recurring_pattern", pattern["id"], fix.correct_slug)
            await visor.update_recurring_pattern(
                pattern["id"], idempotency_key=key, category_slug=fix.correct_slug
            )
        except Exception as exc:  # retry/backoff already lives in BaseMcpClient
            console.print(
                f"[red]failed to fix '{fix.pattern_name}' after retries: {exc}. "
                "Logged for manual review in the app.[/red]"
            )


# -- retroactive balances -------------------------------------------------------


async def _apply_retroactive_balances(
    organizze: OrganizzeClient,
    visor: VisorClient,
    store: StateStore,
    *,
    since: str,
    dry_run: bool,
) -> None:
    # get_balances() returns {"accounts": [{"name", "balance_in_cents", ...}],
    # "credit_cards": [{"name", "open_invoice_in_cents", ...}], "totals": {...}}
    # -- not a flat name -> value map. Both cent fields are already signed
    # integers (negative = debt/debit), so no string parsing is needed.
    balances_resp = await organizze.get_balances()
    balance_by_name = {a["name"]: a["balance_in_cents"] for a in balances_resp.get("accounts", [])}
    balance_by_name.update(
        {c["name"]: c["open_invoice_in_cents"] for c in balances_resp.get("credit_cards", [])}
    )
    today = date.today().isoformat()

    for record in store.list_by_type("account"):
        organizze_name = record.organizze_id
        current_cents = balance_by_name.get(organizze_name)
        if current_cents is None:
            continue

        rows = await organizze.list_all_transactions(since, today, account_id=organizze_name)
        txs = [
            TransactionForBalance(
                account=organizze_name,
                amount_cents=round(parse_brl(r.get("amount", 0)) * 100),
                date=r.get("date", since),
                invoice_due_date=r.get("invoice_due_date"),
            )
            for r in rows
        ]

        is_bank = _account_kind(record) == "bank"
        if is_bank:
            retro_cents = bank_balance_at_date(
                current_balance_cents=current_cents, transactions=txs, since_date=since
            )
        else:
            retro_cents = credit_card_balance_at_date(
                current_open_invoice_cents=current_cents, transactions=txs, since_date=since
            )

        # Organizze represents an open invoice as negative (debt); Visor's
        # manual credit accounts most likely want the amount owed as a
        # positive number, matching how it's normally displayed. Unverified
        # (no CREDIT-type account exists to check against yet) -- check the
        # first card's balance in the app after this runs.
        display_cents = retro_cents if is_bank else abs(retro_cents)

        console.print(f"retroactive balance {organizze_name} on {since}: R${display_cents / 100:.2f}")
        if dry_run:
            continue

        # update_account_settings has no dated-balance concept -- `balance`
        # is just the account's current value, as a decimal string in BRL.
        key = _key("update_account_settings", record.visor_id, since)
        await visor.update_account_settings(
            record.visor_id, idempotency_key=key, balance=f"{display_cents / 100:.2f}"
        )
