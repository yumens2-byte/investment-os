"""GitHub OIDC transport for the restricted comic-novel Edge gateway.

The existing Supabase anon key is retained. No credentials are logged, and
ambiguous writes are never retried by this transport.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

PROJECT_HOST = "ccomoimhhttaklfadaos.supabase.co"
OIDC_HOST_SUFFIX = ".actions.githubusercontent.com"
AUDIENCE = "comic-novel:ccomoimhhttaklfadaos"
READ_OPERATIONS = frozenset({"select_oldest_pending", "get_x_evidence", "get_week_state", "verify_history_access"})
WRITE_OPERATIONS = frozenset({"novel_fb_claim", "novel_fb_mark_sending", "novel_fb_finalize",
                              "novel_fb_mark_result", "novel_fb_release", "novel_x_confirm"})


class NovelGatewayError(RuntimeError):
    """Credential-safe gateway failure; response bodies are intentionally omitted."""


class ComicNovelGateway:
    def __init__(self, environ=None, session=None):
        env = os.environ if environ is None else environ
        base = self._parse_url(env.get("SUPABASE_URL", ""), "gateway project")
        if (base.scheme != "https" or base.hostname != PROJECT_HOST or base.port is not None
                or base.username or base.password or base.query or base.fragment
                or base.path not in ("", "/")):
            raise NovelGatewayError("gateway project URL is invalid")
        self.endpoint = f"https://{PROJECT_HOST}/functions/v1/comic-novel-gateway"
        self.api_key = self._credential(env.get("SUPABASE_KEY"), "Supabase key")
        self.request_token = self._credential(env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN"), "OIDC request token")
        parsed = self._parse_url(env.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""), "OIDC request")
        if (parsed.scheme != "https" or not self._oidc_host_allowed(parsed.hostname) or parsed.port is not None
                or parsed.username or parsed.password or parsed.fragment or not parsed.path):
            raise NovelGatewayError("OIDC request URL is invalid")
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if key != "audience"]
        query.append(("audience", AUDIENCE))
        self.token_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
        self.session = session or requests.Session()
        # Only direct HTTPS requests: environment proxies may contain credentials.
        if session is None:
            self.session.trust_env = False

    @staticmethod
    def _oidc_host_allowed(host):
        # GitHub provides regional run-actions/pipelinesghub request hosts.
        # The token issuer is a different endpoint and must not be hardcoded here.
        if not host or not host.endswith(OIDC_HOST_SUFFIX):
            return False
        return all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in host.split("."))

    @staticmethod
    def _parse_url(value, label):
        try:
            parsed = urlsplit(value)
            parsed.port  # malformed ports must not leak URL values in exceptions
            return parsed
        except (ValueError, TypeError, AttributeError):
            raise NovelGatewayError(f"{label} URL is invalid") from None

    @staticmethod
    def _credential(value, label):
        if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
            raise NovelGatewayError(f"{label} is unavailable")
        return value

    def _oidc_token(self):
        try:
            response = self.session.get(self.token_url,
                                        headers={"Authorization": f"Bearer {self.request_token}"},
                                        timeout=20, allow_redirects=False)
            if response.status_code != 200:
                raise NovelGatewayError("OIDC token request failed")
            payload = response.json()
            value = payload.get("value") if isinstance(payload, dict) else None
            return self._credential(value, "OIDC token")
        except NovelGatewayError:
            raise
        except Exception:
            raise NovelGatewayError("OIDC token request failed") from None

    def call(self, operation, params):
        if operation not in READ_OPERATIONS | WRITE_OPERATIONS or not isinstance(params, dict):
            raise NovelGatewayError("gateway operation is invalid")
        token = self._oidc_token()
        try:
            # Exactly one HTTP attempt, including for mutation operations.
            response = self.session.post(self.endpoint,
                headers={"apikey": self.api_key, "Authorization": f"Bearer {token}"},
                json={"operation": operation, "params": params}, timeout=45, allow_redirects=False)
            if response.status_code != 200:
                raise NovelGatewayError(f"gateway request failed (HTTP {response.status_code})")
            payload = response.json()
            if not isinstance(payload, dict) or "data" not in payload:
                raise NovelGatewayError("gateway response is invalid")
            return payload["data"]
        except NovelGatewayError:
            raise
        except Exception:
            raise NovelGatewayError("gateway request failed") from None
