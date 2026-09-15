"""Phase 1: accounts, cards and categories -- idempotent.

Does not create accounts/cards from scratch (that was done manually);
resolves them against the live Visor account/card list by name (see
`mapping/accounts.py`) and registers the match in state.db. Creates the
custom categories that don't exist yet and hides the listed system
categories, both idempotently (checks `get_categories` before acting).
"""
from __future__ import annotations

import hashlib
import json

from rich.console import Console

from visorsync.config import Settings
from visorsync.mapping.accounts import load_name_overrides, resolve_accounts
from visorsync.mapping.categories import load_category_config
from visorsync.mcp_clients.organizze_client import OrganizzeClient
from visorsync.mcp_clients.visor_client import VisorClient
from visorsync.state.store import StateStore

console = Console()


def _idempotency_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


async def run(settings: Settings, store: StateStore, *, dry_run: bool = False) -> None:
    category_config = load_category_config(settings.config_dir)

    async with OrganizzeClient(settings) as organizze, VisorClient(settings) as visor:
        context = await organizze.get_account_context()
        organizze_accounts = [a["name"] for a in context.get("accounts", []) if isinstance(a, dict)]
        organizze_cards = [c["name"] for c in context.get("credit_cards", []) if isinstance(c, dict)]

        visor_accounts_resp = await visor.get_accounts()
        visor_cards_resp = await visor.get_cards()
        visor_accounts = [a for a in visor_accounts_resp.get("accounts", visor_accounts_resp) if isinstance(a, dict)]
        visor_cards = [c for c in visor_cards_resp.get("cards", visor_cards_resp) if isinstance(c, dict)]

        name_overrides = load_name_overrides(settings.config_dir)
        resolved, unresolved = resolve_accounts(
            organizze_accounts, organizze_cards, visor_accounts, visor_cards, name_overrides
        )

        for name in unresolved:
            console.print(
                f"[yellow]warning[/yellow]: no Visor account/card named '{name}' found -- "
                "expected it to already exist (created manually); skipping."
            )

        for account in resolved:
            content_hash = _idempotency_key(
                account.kind, str(account.closing_day), str(account.due_day), str(account.limit_cents)
            )
            if store.needs_resync("account", account.organizze_name, content_hash):
                console.print(f"registering account/card: {account.organizze_name} -> {account.visor_name}")
                if not dry_run:
                    store.upsert(
                        "account",
                        account.visor_id,
                        organizze_id=account.organizze_name,
                        content_hash=content_hash,
                        created_by_tool=False,  # already existed manually
                        extra_json=json.dumps({"kind": account.kind}),
                    )

        # -- custom categories: create if missing --
        # Visor's create_category takes a name (+ emoji), not a slug -- it
        # assigns the slug itself. So existing custom categories are matched
        # by name here, not by the `visor_slug` hint from categories.yaml,
        # and the real assigned slug is cached in state.db (extra_json) for
        # migrate.py to use instead of the hint.
        categories = await visor.get_categories(include_hidden=True)
        category_list = categories.get("categories", categories) if isinstance(categories, dict) else categories
        by_name = {c["name"].strip().casefold(): c for c in category_list if isinstance(c, dict)}

        for mapping in category_config.mappings:
            if not mapping.is_custom:
                continue
            existing = by_name.get(mapping.organizze_name.strip().casefold())
            if existing is not None:
                store.upsert(
                    "category",
                    existing["id"],
                    organizze_id=mapping.organizze_name,
                    content_hash=existing["slug"],
                    created_by_tool=False,
                    extra_json=json.dumps({"resolved_slug": existing["slug"]}),
                )
                continue
            console.print(f"creating custom category: {mapping.organizze_name} ({mapping.emoji})")
            if dry_run:
                continue
            key = _idempotency_key("create_category", mapping.organizze_name)
            extra_fields: dict[str, str] = {}
            if mapping.parent_slug:
                extra_fields["parent_slug"] = mapping.parent_slug
            elif mapping.category_type:
                extra_fields["type"] = mapping.category_type
            else:
                # Required by Visor for a top-level category; default to
                # "expense" rather than failing when categories.yaml doesn't
                # set one explicitly.
                extra_fields["type"] = "expense"
            await visor.create_category(
                idempotency_key=key,
                name=mapping.organizze_name,
                emoji=mapping.emoji,
                **extra_fields,
            )
            # create_category's own response shape isn't documented in its
            # tool schema (only its inputs are) -- rather than guess at
            # result["id"]/result["slug"], re-read get_categories (whose
            # shape IS documented: id/slug/name/emoji/type/parent) and find
            # the category we just created by name, the same way existing
            # ones are matched above.
            refreshed = await visor.get_categories(include_hidden=True)
            refreshed_list = refreshed.get("categories", refreshed) if isinstance(refreshed, dict) else refreshed
            created = next(
                (
                    c
                    for c in refreshed_list
                    if isinstance(c, dict) and c.get("name", "").strip().casefold() == mapping.organizze_name.strip().casefold()
                ),
                None,
            )
            if created is None:
                console.print(
                    f"[red]created '{mapping.organizze_name}' but couldn't find it again in "
                    "get_categories -- check the app and re-run sync-structure.[/red]"
                )
                continue
            store.upsert(
                "category",
                created["id"],
                organizze_id=mapping.organizze_name,
                content_hash=created["slug"],
                extra_json=json.dumps({"resolved_slug": created["slug"]}),
                created_by_tool=True,
            )

        # -- hide system categories --
        categories = await visor.get_categories(include_hidden=True)
        by_slug = {c["slug"]: c for c in categories.get("categories", categories) if isinstance(c, dict)}
        for slug in category_config.hidden_system_category_slugs:
            cat = by_slug.get(slug)
            if cat is None:
                continue
            if cat.get("hidden"):
                continue
            console.print(f"hiding system category: {slug}")
            if dry_run:
                continue
            key = _idempotency_key("hide_category", slug)
            await visor.hide_category(cat["id"], idempotency_key=key)
            store.upsert(
                "hidden_category",
                cat["id"],
                organizze_id=slug,
                content_hash="hidden",
                created_by_tool=True,
            )

    console.print("[green]sync-structure done.[/green]")
