"""Wrapper fino sobre o MCP do Organizze — só leitura."""
from __future__ import annotations

from typing import Any, Optional

from visorsync.auth.organizze_auth import flow as organizze_flow
from visorsync.config import Settings
from visorsync.mcp_clients.base import BaseMcpClient


class OrganizzeClient(BaseMcpClient):
    def __init__(self, settings: Settings):
        auth_flow = organizze_flow(settings)
        super().__init__(
            mcp_url=settings.organizze_mcp_url,
            token_provider=lambda: auth_flow.get_valid_token().access_token,
        )

    async def get_account_context(self) -> Any:
        return await self.call_tool("get_account_context", {})

    async def get_balances(self) -> Any:
        return await self.call_tool("get_balances", {})

    async def list_recurrences(self) -> Any:
        return await self.call_tool("list_recurrences", {})

    async def find_installments(self, start_date: str, end_date: str) -> Any:
        return await self.call_tool(
            "find_installments", {"start_date": start_date, "end_date": end_date}
        )

    async def list_transactions(
        self,
        start_date: str,
        end_date: str,
        *,
        list_mode: str = "all_transactions",
        account_id: Optional[str] = None,
        category_id: Optional[str] = None,
        page: int = 1,
        per_page: int = 80,
    ) -> Any:
        args: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "list_mode": list_mode,
            "page": page,
            "per_page": per_page,
        }
        if account_id is not None:
            args["account_id"] = account_id
        if category_id is not None:
            args["category_id"] = category_id
        return await self.call_tool("list_transactions", args)

    async def list_all_transactions(
        self, start_date: str, end_date: str, **kwargs: Any
    ) -> list[dict]:
        """Segue a paginação até o fim; nunca soma páginas truncadas por conta própria."""
        page = 1
        per_page = kwargs.pop("per_page", 80)
        all_rows: list[dict] = []
        while True:
            result = await self.list_transactions(
                start_date, end_date, page=page, per_page=per_page, **kwargs
            )
            rows = result.get("transactions") or result.get("results") or []
            all_rows.extend(rows)
            truncated = result.get("truncated", False)
            if not truncated or not rows:
                break
            page += 1
        return all_rows

    async def get_credit_card_invoices(self, **kwargs: Any) -> Any:
        return await self.call_tool("get_credit_card_invoices", kwargs)

    async def get_monthly_overview(self, **kwargs: Any) -> Any:
        return await self.call_tool("get_monthly_overview", kwargs)
