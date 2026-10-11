from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from db.comic_novel_gateway import ComicNovelGateway, NovelGatewayError, AUDIENCE
from db.comic_novel_channel_store import ComicNovelChannelStore

ENV = {"SUPABASE_URL": "https://ccomoimhhttaklfadaos.supabase.co", "SUPABASE_KEY": "anon-secret",
       "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com/token?api-version=2.0",
       "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runtime-secret"}


class Response:
    def __init__(self, payload, code=200):
        self.payload, self.status_code = payload, code
    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, post=None, get=None):
        self.calls = []
        self.get_response = get or Response({"value": "oidc-secret"})
        self.post_response = post or Response({"data": {"status": "ok"}})
    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response
    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response


def test_token_audience_existing_key_and_headers():
    session = Session()
    gateway = ComicNovelGateway(ENV, session)
    assert gateway.call("novel_fb_claim", {"p_source_date": "2026-04-05"}) == {"status": "ok"}
    get, post = session.calls
    assert parse_qs(urlsplit(get[1]).query)["audience"] == [AUDIENCE]
    assert get[2]["headers"] == {"Authorization": "Bearer runtime-secret"}
    assert post[2]["headers"] == {"apikey": "anon-secret", "Authorization": "Bearer oidc-secret"}
    assert get[2]["allow_redirects"] is False and post[2]["allow_redirects"] is False
    assert post[2]["json"] == {"operation": "novel_fb_claim", "params": {"p_source_date": "2026-04-05"}}


@pytest.mark.parametrize("key,value", [
    ("SUPABASE_URL", "http://ccomoimhhttaklfadaos.supabase.co"),
    ("SUPABASE_URL", "https://evil.example"),
    ("SUPABASE_URL", "https://ccomoimhhttaklfadaos.supabase.co/other"),
    ("SUPABASE_URL", "https://secret@ccomoimhhttaklfadaos.supabase.co"),
    ("SUPABASE_URL", "https://ccomoimhhttaklfadaos.supabase.co?secret=x"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://evil.example/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://actions.githubusercontent.com/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://evilactions.githubusercontent.com/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://run-actions.actions.githubusercontent.com.evil.example/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://run_actions.actions.githubusercontent.com/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://.actions.githubusercontent.com/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "http://token.actions.githubusercontent.com/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://secret@token.actions.githubusercontent.com/token"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.actions.githubusercontent.com:443/token"),
    ("SUPABASE_URL", "https://ccomoimhhttaklfadaos.supabase.co:secret"),
    ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.actions.githubusercontent.com:secret/token"),
    ("SUPABASE_KEY", ""), ("SUPABASE_KEY", "secret\nvalue"),
    ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
])
def test_credentials_and_endpoint_validation(key, value):
    session = Session()
    with pytest.raises(NovelGatewayError):
        ComicNovelGateway(dict(ENV, **{key: value}), session)
    assert session.calls == []


@pytest.mark.parametrize("post", [RuntimeError("oidc-secret runtime-secret anon-secret"),
                                   Response({"secret": "anon-secret"}, 401),
                                   Response(ValueError("oidc-secret")), Response({"error": "anon-secret"})])
def test_write_once_and_error_redaction(post):
    session = Session(post=post)
    with pytest.raises(NovelGatewayError) as exc:
        ComicNovelGateway(ENV, session).call("novel_fb_finalize", {"p_claim_id": 1})
    assert len([call for call in session.calls if call[0] == "post"]) == 1
    assert "secret" not in str(exc.value)
    assert exc.value.__cause__ is None


@pytest.mark.parametrize("get", [RuntimeError("runtime-secret"), Response({}, 403),
                                  Response({"value": ""}), Response(ValueError("runtime-secret"))])
def test_oidc_failure_prevents_post_and_redacts(get):
    session = Session(get=get)
    with pytest.raises(NovelGatewayError) as exc:
        ComicNovelGateway(ENV, session).call("novel_fb_claim", {})
    assert len(session.calls) == 1 and session.calls[0][0] == "get"
    assert "secret" not in str(exc.value)


def test_unknown_operation_does_not_fetch_token():
    session = Session()
    gateway = ComicNovelGateway(ENV, session)
    with pytest.raises(NovelGatewayError):
        gateway.call("drop_table", {})
    assert session.calls == []


def test_gateway_store_routes_reads_writes_and_verify(monkeypatch):
    calls = []
    class FakeGateway:
        def call(self, operation, params):
            calls.append((operation, params))
            return {"status": "ok"}
    monkeypatch.setenv("NOVEL_USE_GITHUB_OIDC", "true")
    monkeypatch.setattr("db.comic_novel_gateway.ComicNovelGateway", FakeGateway)
    store = ComicNovelChannelStore()
    store.select_oldest_pending("2026-10-12")
    store.get_x_evidence("2026-04-05")
    store.get_week_state("page", "2026-10-12")
    store.verify_history_access()
    store.claim_slot("2026-04-05", "page", "2026-10-12", "run", "content", "source")
    assert [op for op, _ in calls] == ["select_oldest_pending", "get_x_evidence", "get_week_state",
                                      "verify_history_access", "novel_fb_claim"]
    assert calls[-1][1]["p_source_hash"] == "source"
    assert store.client is None


def test_explicit_client_retains_existing_path(monkeypatch):
    monkeypatch.setenv("NOVEL_USE_GITHUB_OIDC", "true")
    client = SimpleNamespace()
    store = ComicNovelChannelStore(client)
    assert store.gateway is None and store.client is client


@pytest.mark.parametrize("host", ["run-actions-3-azure-eastus.actions.githubusercontent.com",
                                 "pipelinesghubeus6.actions.githubusercontent.com",
                                 "token.actions.githubusercontent.com"])
def test_github_regional_request_endpoints_are_accepted(host):
    session = Session()
    env = dict(ENV, ACTIONS_ID_TOKEN_REQUEST_URL=f"https://{host}/152//idtoken/plan/job?api-version=2.0&audience=old")
    gateway = ComicNovelGateway(env, session)
    gateway.call("verify_history_access", {})
    get = session.calls[0]
    assert urlsplit(get[1]).hostname == host
    assert parse_qs(urlsplit(get[1]).query)["audience"] == [AUDIENCE]
    assert get[2]["allow_redirects"] is False
