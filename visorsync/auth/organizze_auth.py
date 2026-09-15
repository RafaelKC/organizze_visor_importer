"""Autenticação com o MCP do Organizze (leitura)."""
from __future__ import annotations

from visorsync.auth.oauth_flow import OAuth2ClientFlow, TokenSet
from visorsync.config import Settings


def flow(settings: Settings) -> OAuth2ClientFlow:
    return OAuth2ClientFlow(
        service_name="organizze",
        mcp_url=settings.organizze_mcp_url,
        token_path=settings.organizze_token_path,
        callback_port=settings.oauth_callback_port,
        client_id=settings.organizze_client_id,
    )


def login(settings: Settings) -> TokenSet:
    return flow(settings).login()


def get_valid_token(settings: Settings) -> TokenSet:
    return flow(settings).get_valid_token()
