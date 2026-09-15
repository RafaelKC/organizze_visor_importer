"""`reset-visor`: zera tudo que este script criou no Visor, usando o
state.db como única fonte de verdade sobre o que pode ser apagado
automaticamente.

Nunca age sozinho sobre nada que não esteja registrado em state.db com
`created_by_tool=True` — isso é o que garante que algo criado manualmente
depois no app nunca seja apagado sem revisão. Se o state.db estiver ausente
ou corrompido, o comando se recusa a rodar (ver `cli.py`, que já verifica
isso antes de chamar `run`).

`--include-manual` cobre o caso de uma tentativa de migração manual (feita
direto pelo app/chat, sem passar por este script) ter ficado errada: ele
busca ao vivo no Visor as contas/cartões, recorrências e categorias
customizadas que existem mas NÃO estão em state.db, e pede confirmação
individual (nunca em lote silencioso) antes de apagar qualquer uma.
"""
from __future__ import annotations

import hashlib

from rich.console import Console
from rich.prompt import Prompt

from visorsync.config import Settings
from visorsync.mcp_clients.visor_client import VisorClient
from visorsync.state.store import EntityRecord, StateStore

console = Console()

# Ordem importa: transações/parcelas/recorrências antes de contas/categorias
# (evita referências pendentes ao apagar a entidade "pai").
RESET_ORDER = [
    "transaction",
    "installment_plan",
    "recurring_pattern",
    "manual_account",
    "custom_category",
    "hidden_category",
]


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


async def run(
    settings: Settings,
    store: StateStore,
    *,
    confirm: bool,
    dry_run: bool,
    only: str | None,
    include_manual: bool = False,
    manual_since: str | None = None,
    manual_until: str | None = None,
) -> None:
    entity_types = [only] if only else RESET_ORDER
    unknown = [t for t in entity_types if t not in RESET_ORDER]
    if unknown:
        raise ValueError(f"--only inválido: {unknown}. Válidos: {RESET_ORDER}")

    plan: dict[str, list[EntityRecord]] = {}
    for entity_type in entity_types:
        records = [r for r in store.list_by_type(entity_type) if r.created_by_tool]
        if records:
            plan[entity_type] = records

    if not plan:
        console.print("[green]Nada registrado como criado por este script — nada a resetar.[/green]")
    else:
        console.print("[bold]Plano de reset (rastreado no state.db):[/bold]")
        for entity_type, records in plan.items():
            console.print(f"  {entity_type}: {len(records)} item(ns)")
            for r in records:
                console.print(f"    - visor_id={r.visor_id} organizze_id={r.organizze_id}")

        if dry_run:
            console.print("[yellow]--dry-run: nada foi apagado.[/yellow]")
        elif not confirm:
            console.print("[red]Recusando executar sem --confirm.[/red]")
        else:
            async with VisorClient(settings) as visor:
                for entity_type, records in plan.items():
                    for record in records:
                        console.print(f"apagando {entity_type} {record.visor_id}...")
                        await _delete_one(visor, entity_type, record)
                        store.delete(entity_type, record.visor_id)

            if only is None:
                store.wipe()
                console.print("[green]state.db zerado — próxima sincronização parte limpa.[/green]")

            console.print("[green]reset-visor concluído.[/green]")

    if include_manual:
        async with VisorClient(settings) as visor:
            await _manual_cleanup(
                visor, store, dry_run=dry_run, confirm=confirm, since=manual_since, until=manual_until
            )


async def _delete_one(visor: VisorClient, entity_type: str, record: EntityRecord) -> None:
    key = _key("reset", entity_type, record.visor_id)
    if entity_type == "transaction":
        await visor.delete_transactions([record.visor_id], idempotency_key=key)
    elif entity_type == "installment_plan":
        # Não há `delete_installment_plan` documentado nas tools do Visor —
        # `edit_installment_plan` com cancelamento é o equivalente conhecido.
        await visor.edit_installment_plan(record.visor_id, idempotency_key=key, cancelled=True)
    elif entity_type == "recurring_pattern":
        await visor.deactivate_recurring_pattern(record.visor_id, idempotency_key=key)
    elif entity_type == "manual_account":
        await visor.delete_manual_account(record.visor_id, idempotency_key=key)
    elif entity_type == "custom_category":
        await visor.delete_category(record.visor_id, idempotency_key=key)
    elif entity_type == "hidden_category":
        await visor.unhide_category(record.visor_id, idempotency_key=key)
    else:
        raise ValueError(f"tipo de entidade desconhecido: {entity_type}")


def _tracked_visor_ids(store: StateStore) -> set[str]:
    return {r.visor_id for r in store.list_all()}


def _pick_indices(prompt: str, count: int) -> list[int]:
    """Pede ao usuário quais itens (por índice, 1-based) apagar.

    Aceita "all", vazio/"none" (nenhum) ou uma lista tipo "1,3,5". Nunca
    apaga nada sem essa escolha explícita, mesmo com --confirm.
    """
    raw = Prompt.ask(prompt, default="none").strip().lower()
    if raw in ("", "none", "n"):
        return []
    if raw == "all":
        return list(range(1, count + 1))
    indices: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= count:
            indices.append(int(part))
    return indices


async def _manual_cleanup(
    visor: VisorClient,
    store: StateStore,
    *,
    dry_run: bool,
    confirm: bool,
    since: str | None,
    until: str | None,
) -> None:
    """Limpa contas/cartões, recorrências e categorias customizadas que
    existem no Visor mas NÃO estão em state.db — tipicamente resíduo de uma
    migração feita manualmente pelo app/chat antes deste script existir.

    Cada grupo é listado e a escolha de o que apagar é sempre manual
    (`_pick_indices`); nada é apagado em lote automaticamente, mesmo com
    --confirm, porque não há garantia de que essas entidades foram criadas
    por este processo.
    """
    tracked = _tracked_visor_ids(store)
    console.print("\n[bold]Limpeza manual (entidades não rastreadas em state.db):[/bold]")

    # -- contas e cartões --
    accounts_resp = await visor.get_accounts()
    cards_resp = await visor.get_cards()
    accounts = [a for a in accounts_resp.get("accounts", accounts_resp) if isinstance(a, dict)]
    cards = [c for c in cards_resp.get("cards", cards_resp) if isinstance(c, dict)]
    candidates = [(a, "account") for a in accounts if a.get("id") not in tracked] + [
        (c, "card") for c in cards if c.get("id") not in tracked
    ]
    if candidates:
        console.print("\n[bold]Contas/cartões não rastreados:[/bold]")
        for i, (item, kind) in enumerate(candidates, start=1):
            console.print(f"  [{i}] ({kind}) {item.get('name')} — id={item.get('id')}")
        if not dry_run and confirm:
            for i in _pick_indices("Apagar quais? (números separados por vírgula, 'all' ou enter p/ nenhum)", len(candidates)):
                item, _kind = candidates[i - 1]
                key = _key("manual_cleanup", "account", item["id"])
                console.print(f"apagando conta/cartão {item.get('name')}...")
                await visor.delete_manual_account(item["id"], idempotency_key=key)
    else:
        console.print("  nenhuma conta/cartão não rastreado encontrado.")

    # -- recorrências --
    expenses_resp = await visor.get_recurring_expenses()
    incomes_resp = await visor.get_recurring_incomes()
    patterns = [
        p
        for p in [
            *(expenses_resp.get("patterns", expenses_resp) if isinstance(expenses_resp, dict) else expenses_resp),
            *(incomes_resp.get("patterns", incomes_resp) if isinstance(incomes_resp, dict) else incomes_resp),
        ]
        if isinstance(p, dict) and p.get("id") not in tracked
    ]
    if patterns:
        console.print("\n[bold]Recorrências não rastreadas:[/bold]")
        for i, p in enumerate(patterns, start=1):
            console.print(f"  [{i}] {p.get('name')} — R${p.get('amount', 0) / 100:.2f} — id={p.get('id')}")
        if not dry_run and confirm:
            for i in _pick_indices("Desativar quais? (números separados por vírgula, 'all' ou enter p/ nenhum)", len(patterns)):
                p = patterns[i - 1]
                key = _key("manual_cleanup", "recurring_pattern", p["id"])
                console.print(f"desativando recorrência {p.get('name')}...")
                await visor.deactivate_recurring_pattern(p["id"], idempotency_key=key)
    else:
        console.print("  nenhuma recorrência não rastreada encontrada.")

    # -- categorias customizadas --
    categories_resp = await visor.get_categories(include_hidden=True)
    categories = [c for c in categories_resp.get("categories", categories_resp) if isinstance(c, dict)]
    custom_categories = [c for c in categories if c.get("is_custom") and c.get("id") not in tracked]
    if custom_categories:
        console.print("\n[bold]Categorias customizadas não rastreadas:[/bold]")
        for i, c in enumerate(custom_categories, start=1):
            console.print(f"  [{i}] {c.get('name')} ({c.get('slug')}) — id={c.get('id')}")
        if not dry_run and confirm:
            for i in _pick_indices("Apagar quais? (números separados por vírgula, 'all' ou enter p/ nenhum)", len(custom_categories)):
                c = custom_categories[i - 1]
                key = _key("manual_cleanup", "category", c["id"])
                console.print(f"apagando categoria {c.get('name')}...")
                await visor.delete_category(c["id"], idempotency_key=key)
    else:
        console.print("  nenhuma categoria customizada não rastreada encontrada.")

    # -- transações manuais (só se --since/--until foi passado; range é
    # obrigatório aqui porque listar TODAS as transações pra revisão manual
    # não escala e o risco de apagar algo errado é maior) --
    if since or until:
        transactions_resp = await visor.get_transactions(start_date=since, end_date=until)
        transactions = [
            t
            for t in transactions_resp.get("transactions", transactions_resp)
            if isinstance(t, dict) and t.get("id") not in tracked
        ]
        if transactions:
            console.print(f"\n[bold]Transações não rastreadas entre {since} e {until}:[/bold]")
            for i, t in enumerate(transactions, start=1):
                console.print(
                    f"  [{i}] {t.get('date')} {t.get('description')} R${t.get('amount', 0) / 100:.2f} — id={t.get('id')}"
                )
            if not dry_run and confirm:
                to_delete = _pick_indices(
                    "Apagar quais? (números separados por vírgula, 'all' ou enter p/ nenhum)", len(transactions)
                )
                if to_delete:
                    ids = [transactions[i - 1]["id"] for i in to_delete]
                    key = _key("manual_cleanup", "transactions", ",".join(sorted(ids)))
                    console.print(f"apagando {len(ids)} transação(ões)...")
                    await visor.delete_transactions(ids, idempotency_key=key)
        else:
            console.print(f"  nenhuma transação não rastreada entre {since} e {until}.")
    else:
        console.print(
            "\n[dim]transações manuais não revisadas — passe --manual-since/--manual-until "
            "para incluí-las na limpeza.[/dim]"
        )

    if dry_run:
        console.print("\n[yellow]--dry-run: nada foi apagado na limpeza manual.[/yellow]")
    elif not confirm:
        console.print("\n[red]--confirm não passado — nada foi apagado na limpeza manual.[/red]")
