"""Wrapper fino sobre o MCP do Visor — escrita.

Todas as tools de escrita exigem `confirmed: true` + `idempotency_key` únicos
por chamada real (reusar a key só em retry da mesma operação lógica) —
por isso todo método de escrita aqui recebe `idempotency_key` explicitamente
em vez de gerar um novo a cada chamada.

Every Visor tool also accepts an optional `space_id`: reads without one use
whatever space happens to be "active" (which can silently differ between
runs), and writes for a user with more than one space *require* it. To
avoid depending on an ambient "active space" that can drift, `space_id` is
injected into every call here automatically when `settings.visor_space_id`
is set (see `config.py` and `commands/sync_structure.py`'s space check).
"""
from __future__ import annotations

from typing import Any, Optional

from visorsync.auth.visor_auth import flow as visor_flow
from visorsync.config import Settings
from visorsync.mcp_clients.base import BaseMcpClient


class VisorClient(BaseMcpClient):
    def __init__(self, settings: Settings):
        auth_flow = visor_flow(settings)
        super().__init__(
            mcp_url=settings.visor_mcp_url,
            token_provider=lambda: auth_flow.get_valid_token().access_token,
        )
        self._space_id: Optional[str] = settings.visor_space_id

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._space_id and "space_id" not in arguments:
            arguments = {**arguments, "space_id": self._space_id}
        return await super().call_tool(name, arguments)

    # -- espaços ------------------------------------------------------

    async def list_spaces(self) -> Any:
        return await self.call_tool("list_spaces", {})

    # -- leitura ------------------------------------------------------

    async def get_categories(self, include_hidden: bool = True) -> Any:
        return await self.call_tool("get_categories", {"include_hidden": include_hidden})

    async def get_accounts(self) -> Any:
        return await self.call_tool("get_accounts", {})

    async def get_cards(self) -> Any:
        return await self.call_tool("get_cards", {})

    async def get_recurring_expenses(self) -> Any:
        return await self.call_tool("get_recurring_expenses", {})

    async def get_recurring_incomes(self) -> Any:
        return await self.call_tool("get_recurring_incomes", {})

    async def get_installment_plans(self) -> Any:
        return await self.call_tool("get_installment_plans", {})

    async def get_transactions(self, **kwargs: Any) -> Any:
        return await self.call_tool("get_transactions", kwargs)

    # -- categorias -----------------------------------------------------

    async def create_category(self, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "create_category", {"confirmed": True, "idempotency_key": idempotency_key, **fields}
        )

    async def hide_category(self, category_id: str, *, idempotency_key: str) -> Any:
        return await self.call_tool(
            "hide_category",
            {"category_id": category_id, "confirmed": True, "idempotency_key": idempotency_key},
        )

    async def unhide_category(self, category_id: str) -> Any:
        # Real schema has no confirmed/idempotency_key at all (unlike every
        # other write tool here) -- sending them is rejected as invalid params.
        return await self.call_tool("unhide_category", {"category_id": category_id})

    async def delete_category(self, category_id: str, *, idempotency_key: str) -> Any:
        return await self.call_tool(
            "delete_category",
            {"category_id": category_id, "confirmed": True, "idempotency_key": idempotency_key},
        )

    # -- contas/cartões ---------------------------------------------------

    async def create_manual_account(self, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "create_manual_account", {"confirmed": True, "idempotency_key": idempotency_key, **fields}
        )

    async def update_account_settings(self, account_id: str, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "update_account_settings",
            {"account_id": account_id, "confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    async def delete_manual_account(self, account_id: str, *, idempotency_key: str) -> Any:
        return await self.call_tool(
            "delete_manual_account",
            {"account_id": account_id, "confirmed": True, "idempotency_key": idempotency_key},
        )

    # -- recorrências -----------------------------------------------------

    async def create_recurring_pattern(self, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "create_recurring_pattern",
            {"confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    async def update_recurring_pattern(self, pattern_id: str, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "update_recurring_pattern",
            {"pattern_id": pattern_id, "confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    async def deactivate_recurring_pattern(self, pattern_id: str, *, idempotency_key: str) -> Any:
        return await self.call_tool(
            "deactivate_recurring_pattern",
            {"pattern_id": pattern_id, "confirmed": True, "idempotency_key": idempotency_key},
        )

    # -- parcelamentos ------------------------------------------------------

    async def create_installment_plan(self, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "create_installment_plan",
            {"confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    async def edit_installment_plan(self, plan_id: str, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "edit_installment_plan",
            {"plan_id": plan_id, "confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    # -- transações ---------------------------------------------------------

    async def create_manual_transaction(self, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "create_manual_transaction",
            {"confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    async def edit_transaction(self, transaction_id: str, *, idempotency_key: str, **fields: Any) -> Any:
        return await self.call_tool(
            "edit_transaction",
            {"transaction_id": transaction_id, "confirmed": True, "idempotency_key": idempotency_key, **fields},
        )

    async def delete_transactions(self, transaction_ids: list[str], *, idempotency_key: str) -> Any:
        return await self.call_tool(
            "delete_transactions",
            {
                "transaction_ids": transaction_ids,
                "confirmed": True,
                "idempotency_key": idempotency_key,
            },
        )
