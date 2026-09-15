"""Configuração central do visorsync: paths, URLs dos MCPs, env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _home() -> Path:
    raw = os.environ.get("VISORSYNC_HOME", "~/.visorsync")
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Settings:
    home_dir: Path
    organizze_mcp_url: str
    visor_mcp_url: str
    oauth_callback_port: int
    organizze_client_id: str | None
    visor_client_id: str | None
    state_db_path: Path
    ambiguous_report_path: Path
    config_dir: Path

    @property
    def organizze_token_path(self) -> Path:
        return self.home_dir / "organizze_token.json"

    @property
    def visor_token_path(self) -> Path:
        return self.home_dir / "visor_token.json"


def load_settings() -> Settings:
    home = _home()
    return Settings(
        home_dir=home,
        organizze_mcp_url=os.environ.get(
            "ORGANIZZE_MCP_URL", "https://mcp.organizze.com.br/mcp"
        ),
        visor_mcp_url=os.environ.get("VISOR_MCP_URL", "https://mcp.visorfinance.app"),
        oauth_callback_port=int(os.environ.get("OAUTH_CALLBACK_PORT", "53682")),
        organizze_client_id=os.environ.get("ORGANIZZE_OAUTH_CLIENT_ID") or None,
        visor_client_id=os.environ.get("VISOR_OAUTH_CLIENT_ID") or None,
        state_db_path=home / "state.db",
        ambiguous_report_path=home / "ambiguous_installments.json",
        config_dir=home / "config",
    )


settings = load_settings()
