"""visorsync entrypoint.

A CLI that talks directly to the Organizze (read) and Visor (write) MCP
servers to migrate financial data without going through a chat session.
Designed to run unattended on a homelab (cron/Docker): idempotent and
resumable via a local SQLite state store.
"""
from __future__ import annotations

import asyncio
from datetime import date

import typer

from visorsync.auth import organizze_auth, visor_auth
from visorsync.commands import migrate as migrate_cmd
from visorsync.commands import reset_visor as reset_visor_cmd
from visorsync.commands import sync_structure as sync_structure_cmd
from visorsync.config import settings
from visorsync.state.store import StateStore

app = typer.Typer(
    name="visorsync",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    help=(
        "Sync Organizze (read) into Visor (write) over MCP, without a chat session.\n\n"
        "Typical flow:\n\n"
        "  1. visorsync auth organizze\n"
        "  2. visorsync auth visor\n"
        "  3. visorsync sync-structure\n"
        "  4. visorsync migrate --since 2026-07-01\n\n"
        "Every write command is idempotent: re-running never duplicates data, "
        "and a run that dies midway can simply be re-run to resume from the "
        "local state.db checkpoint."
    ),
    epilog=(
        "Run 'visorsync COMMAND --help' for details on a specific command. "
        "State and tokens are stored under $VISORSYNC_HOME (default: ~/.visorsync)."
    ),
)

auth_app = typer.Typer(
    name="auth",
    no_args_is_help=True,
    help=(
        "Authenticate against the Organizze and Visor MCP servers.\n\n"
        "Both servers require an interactive OAuth2 login via browser on first "
        "use. The resulting token is cached locally and refreshed automatically, "
        "so you should only need to run these once per server (or after a "
        "token is revoked)."
    ),
)
app.add_typer(auth_app, name="auth")


@auth_app.command("organizze", help="Log into the Organizze MCP server (read source) and cache the token.")
def auth_organizze() -> None:
    """Open a browser for an interactive OAuth2 login against the Organizze
    MCP server (`ORGANIZZE_MCP_URL`) and persist the resulting token under
    $VISORSYNC_HOME so future commands don't prompt again.
    """
    organizze_auth.login(settings)


@auth_app.command("visor", help="Log into the Visor MCP server (write destination) and cache the token.")
def auth_visor() -> None:
    """Open a browser for an interactive OAuth2 login against the Visor MCP
    server (`VISOR_MCP_URL`) and persist the resulting token under
    $VISORSYNC_HOME so future commands don't prompt again.
    """
    visor_auth.login(settings)


@app.command(
    "sync-structure",
    help="Sync accounts, cards and categories from Organizze to Visor (idempotent, phase 1).",
)
def sync_structure(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would be created/updated in Visor without writing anything.",
    ),
) -> None:
    """Phase 1 of the migration.

    Ensures the manual accounts, credit cards and categories that mirror
    Organizze exist in Visor, using the local state.db to detect what has
    already been created (by this tool or manually) so nothing is
    duplicated. Safe to re-run at any time.
    """
    store = StateStore(settings.state_db_path)
    asyncio.run(sync_structure_cmd.run(settings, store, dry_run=dry_run))


@app.command(
    "reset-visor",
    help="Delete everything this tool has created in Visor, tracked via state.db.",
)
def reset_visor(
    confirm: bool = typer.Option(
        False, "--confirm", help="Required flag to actually perform the reset (no prompt otherwise)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List everything that would be deleted, without touching anything."
    ),
    only: str = typer.Option(
        None,
        "--only",
        help=(
            "Restrict the reset to one entity type, e.g. 'transactions', "
            "'installments', 'recurring', 'accounts', 'categories'."
        ),
    ),
    include_manual: bool = typer.Option(
        False,
        "--include-manual",
        help=(
            "Also review accounts/cards, recurring patterns, custom categories and "
            "(with --manual-since/--manual-until) transactions that exist in Visor "
            "but are NOT tracked in state.db -- typically leftovers from an earlier "
            "manual migration done through the app/chat. Lists each group and asks "
            "you to pick, one group at a time, which items to delete; nothing here "
            "is ever deleted in an automatic bulk pass."
        ),
    ),
    manual_since: str = typer.Option(
        None, "--manual-since", help="Start date YYYY-MM-DD for the untracked-transactions review (requires --include-manual)."
    ),
    manual_until: str = typer.Option(
        None, "--manual-until", help="End date YYYY-MM-DD for the untracked-transactions review (requires --include-manual)."
    ),
) -> None:
    """Wipe Visor data created by this tool, and only that data.

    Every automatic deletion is scoped to rows registered in the local
    state.db as created by this tool (matched by visor_id) -- anything
    created manually in the Visor app is left untouched by default. If
    state.db is missing or unreadable, this command refuses to run
    automatically and asks for manual, entity-by-entity confirmation
    instead of guessing.

    Use --include-manual when an earlier *manual* migration (done directly
    through the app or a chat session, without this tool) needs cleaning up
    too: it lists untracked accounts/cards/recurring patterns/categories
    (and transactions, if you pass --manual-since/--manual-until) and lets
    you pick exactly which ones to remove -- one confirmed selection per
    group, never an automatic bulk delete of everything untracked.
    """
    if not settings.state_db_path.exists():
        typer.secho(
            "state.db is missing -- refusing to run an automatic reset without it. "
            "Review and remove entities manually in the Visor app instead.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    store = StateStore(settings.state_db_path)
    asyncio.run(
        reset_visor_cmd.run(
            settings,
            store,
            confirm=confirm,
            dry_run=dry_run,
            only=only,
            include_manual=include_manual,
            manual_since=manual_since,
            manual_until=manual_until,
        )
    )


@app.command(
    "migrate",
    help="Migrate transactions, installments, recurrences and retroactive balances (phase 2).",
)
def migrate(
    since: str = typer.Option(
        None,
        "--since",
        help=(
            "Start date YYYY-MM-DD. Never defaults silently -- if omitted you "
            "will be prompted interactively, since this date drives which "
            "transactions/installments are migrated and the retroactive "
            "balance calculation."
        ),
    ),
    until: str = typer.Option(
        None,
        "--until",
        help="End date YYYY-MM-DD (default: December 31st of the current year).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would be migrated (transactions, installments, balances) without writing anything.",
    ),
) -> None:
    """Phase 2 of the migration: the actual transaction history.

    For the [since, until] window this command migrates one-off
    transactions day by day, reconstructs and creates fragmented
    installment plans (see visorsync.mapping.installments), and computes
    the retroactive account/card balance as of `--since` by walking
    backwards from today's known balance. Recurring patterns created
    manually in Visor are matched by name/amount and left alone.

    Resumable: progress is checkpointed in state.db, so a run that is
    interrupted can simply be re-run and will pick up where it left off
    without creating duplicates.
    """
    if since is None:
        since = typer.prompt("Start date to sync from? (YYYY-MM-DD)")
    if until is None:
        until = date(date.today().year, 12, 31).isoformat()

    store = StateStore(settings.state_db_path)
    asyncio.run(migrate_cmd.run(settings, store, since=since, until=until, dry_run=dry_run))


if __name__ == "__main__":
    app()
