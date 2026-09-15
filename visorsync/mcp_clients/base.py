"""Cliente MCP base: conecta via Streamable HTTP, injeta Bearer token OAuth2,
e chama tools com retry/backoff exponencial.

Organizze e Visor usam o mesmo transporte, só muda a URL e o provedor de
token — por isso a lógica de conexão/retry mora aqui e os dois clients
concretos (`organizze_client.py`, `visor_client.py`) só declaram os nomes
das tools que usam.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any, Callable, Optional

from mcp import ClientSession

try:
    # mcp>=1.10 renamed this helper.
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("visorsync.mcp")

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 1.5


class McpToolError(RuntimeError):
    """Erro retornado pela tool remota (não um erro de transporte)."""


class BaseMcpClient:
    """Mantém uma sessão MCP viva e expõe `call_tool` com backoff.

    Uso: `async with SomeClient(url, token_provider) as client: ...`
    `token_provider` é uma função síncrona que devolve um access_token válido
    (o `OAuth2ClientFlow.get_valid_token` já cuida do refresh).
    """

    def __init__(self, mcp_url: str, token_provider: Callable[[], str]):
        self.mcp_url = mcp_url
        self._token_provider = token_provider
        self._stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None

    async def __aenter__(self) -> "BaseMcpClient":
        self._stack = AsyncExitStack()
        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(self.mcp_url, headers=headers)
        )
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.session = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        assert self.session is not None, "cliente MCP usado fora do contexto `async with`"
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await self.session.call_tool(name, arguments)
                if getattr(result, "isError", False):
                    message = _extract_text(result)
                    raise McpToolError(f"{name}: {message}")
                return _extract_payload(result)
            except McpToolError as exc:
                # erros funcionais (ex.: "No approval received") merecem retry
                # com backoff, mas não devem travar o pipeline inteiro se
                # persistirem — o chamador decide o que fazer ao esgotar.
                last_error = exc
                logger.warning("tool %s falhou (tentativa %d/%d): %s", name, attempt, MAX_RETRIES, exc)
            except Exception as exc:  # erro de transporte/rede
                last_error = exc
                logger.warning("erro de transporte em %s (tentativa %d/%d): %s", name, attempt, MAX_RETRIES, exc)

            if attempt < MAX_RETRIES:
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))

        assert last_error is not None
        raise last_error


def _extract_text(result: Any) -> str:
    parts = getattr(result, "content", None) or []
    texts = [getattr(p, "text", "") for p in parts if getattr(p, "text", None)]
    return " ".join(texts) or "erro desconhecido"


def _extract_payload(result: Any) -> Any:
    """A maioria das tools MCP devolve JSON como texto num único content block."""
    import json

    parts = getattr(result, "content", None) or []
    texts = [getattr(p, "text", None) for p in parts if getattr(p, "text", None)]
    if not texts:
        return None
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined
