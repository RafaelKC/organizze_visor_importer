"""Fluxo OAuth2 (Authorization Code + PKCE) genérico para servidores MCP.

Organizze e Visor usam o mesmo tipo de fluxo (login interativo via browser,
loopback redirect, RFC 8252), então a lógica mora aqui uma única vez.
`organizze_auth.py` e `visor_auth.py` só configuram os endpoints/paths.

Descoberta de endpoints segue RFC 8414
(`<mcp_url>/.well-known/oauth-authorization-server`) com fallback para
registro dinâmico de client (RFC 7591) quando nenhum client_id é fornecido.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin, urlparse

import httpx


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # epoch seconds
    token_type: str = "Bearer"

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - skew_seconds)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "TokenSet":
        return cls(**json.loads(raw))


class _CallbackServer:
    """Servidor loopback mínimo pra capturar o redirect do OAuth2."""

    def __init__(self, port: int):
        self.port = port
        self.result: dict[str, str] = {}
        self._event = threading.Event()
        handler = self._make_handler()
        self._httpd = http.server.HTTPServer(("127.0.0.1", port), handler)

    def _make_handler(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (assinatura exigida pela stdlib)
                from urllib.parse import parse_qs

                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                outer.result = {k: v[0] for k, v in params.items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<html><body><h3>Login concluído. Pode fechar esta aba.</h3>"
                    "</body></html>".encode("utf-8")
                )
                outer._event.set()

            def log_message(self, *args):  # silencia logs padrão do http.server
                return

        return Handler

    def wait_for_callback(self, timeout: float = 300.0) -> dict[str, str]:
        thread = threading.Thread(target=self._httpd.handle_request, daemon=True)
        thread.start()
        if not self._event.wait(timeout=timeout):
            raise TimeoutError("Timeout aguardando o redirect do login OAuth2.")
        thread.join(timeout=5)
        self._httpd.server_close()
        return self.result


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OAuth2ClientFlow:
    """Login interativo + refresh automático + cache de token em disco."""

    def __init__(
        self,
        *,
        service_name: str,
        mcp_url: str,
        token_path: Path,
        callback_port: int,
        client_id: Optional[str] = None,
        scope: str = "mcp",
    ):
        self.service_name = service_name
        self.mcp_url = mcp_url
        self.token_path = token_path
        self.callback_port = callback_port
        self.client_id = client_id
        self.scope = scope
        self._metadata: Optional[dict] = None

    # -- descoberta -----------------------------------------------------

    def _discover(self) -> dict:
        if self._metadata is not None:
            return self._metadata
        base = f"{urlparse(self.mcp_url).scheme}://{urlparse(self.mcp_url).netloc}"
        well_known = urljoin(base + "/", ".well-known/oauth-authorization-server")
        resp = httpx.get(well_known, timeout=15)
        resp.raise_for_status()
        self._metadata = resp.json()
        return self._metadata

    def _ensure_client_id(self, metadata: dict) -> str:
        if self.client_id:
            return self.client_id
        registration_endpoint = metadata.get("registration_endpoint")
        if not registration_endpoint:
            raise RuntimeError(
                f"{self.service_name}: nenhum client_id configurado e o servidor não "
                "suporta registro dinâmico (RFC 7591). Configure "
                f"{self.service_name.upper()}_OAUTH_CLIENT_ID no .env."
            )
        redirect_uri = f"http://127.0.0.1:{self.callback_port}/callback"
        resp = httpx.post(
            registration_endpoint,
            json={
                "client_name": "visorsync",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            timeout=15,
        )
        resp.raise_for_status()
        client_id = resp.json()["client_id"]
        self.client_id = client_id
        return client_id

    # -- fluxo principal --------------------------------------------------

    def login(self) -> TokenSet:
        metadata = self._discover()
        client_id = self._ensure_client_id(metadata)
        redirect_uri = f"http://127.0.0.1:{self.callback_port}/callback"
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)

        auth_url = metadata["authorization_endpoint"] + "?" + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": self.scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

        print(f"[{self.service_name}] Abrindo o navegador para login...")
        print(f"Se não abrir automaticamente, acesse: {auth_url}")
        webbrowser.open(auth_url)

        server = _CallbackServer(self.callback_port)
        params = server.wait_for_callback()

        if params.get("state") != state:
            raise RuntimeError(f"{self.service_name}: state OAuth2 inválido (possível CSRF).")
        if "error" in params:
            raise RuntimeError(f"{self.service_name}: login falhou: {params.get('error_description', params['error'])}")

        code = params["code"]
        token_resp = httpx.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        tokens = self._parse_token_response(token_resp.json())
        self._save(tokens)
        print(f"[{self.service_name}] Login concluído e token salvo em {self.token_path}")
        return tokens

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise RuntimeError(f"{self.service_name}: token expirado e sem refresh_token; rode o login novamente.")
        metadata = self._discover()
        client_id = self._ensure_client_id(metadata)
        resp = httpx.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": client_id,
            },
            timeout=15,
        )
        resp.raise_for_status()
        new_tokens = self._parse_token_response(resp.json(), fallback_refresh=tokens.refresh_token)
        self._save(new_tokens)
        return new_tokens

    def get_valid_token(self) -> TokenSet:
        """Ponto de entrada usado pelos MCP clients: garante um access_token válido."""
        tokens = self._load()
        if tokens is None:
            return self.login()
        if tokens.is_expired():
            return self.refresh(tokens)
        return tokens

    # -- persistência -----------------------------------------------------

    def _load(self) -> Optional[TokenSet]:
        if not self.token_path.exists():
            return None
        return TokenSet.from_json(self.token_path.read_text())

    def _save(self, tokens: TokenSet) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(tokens.to_json())
        self.token_path.chmod(0o600)

    @staticmethod
    def _parse_token_response(data: dict, fallback_refresh: Optional[str] = None) -> TokenSet:
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", fallback_refresh),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            token_type=data.get("token_type", "Bearer"),
        )
