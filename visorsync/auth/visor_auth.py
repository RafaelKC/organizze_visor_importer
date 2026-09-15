"""Autenticação com o MCP do Visor (escrita) — mesmo fluxo usado no navegador."""
from __future__ import annotations

from visorsync.auth.oauth_flow import OAuth2ClientFlow, TokenSet
from visorsync.config import Settings


def flow(settings: Settings) -> OAuth2ClientFlow:
    return OAuth2ClientFlow(
        service_name="visor",
        mcp_url=settings.visor_mcp_url,
        token_path=settings.visor_token_path,
        callback_port=settings.oauth_callback_port,
        client_id=settings.visor_client_id,
    )


def login(settings: Settings) -> TokenSet:
    return flow(settings).login()


def get_valid_token(settings: Settings) -> TokenSet:
    return flow(settings).get_valid_token()
