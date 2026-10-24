"""Actions Secret 주입 → DB 요청 헤더·권한 오류·읽기 전용 검사 계약."""
import json
import re
from pathlib import Path

import pytest

from gen.nuna_weekly import db, pipeline


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status
        self.text = json.dumps(body)

    def json(self):
        return self.body


class Session:
    def __init__(self, deny=False):
        self.calls = []
        self.deny = deny

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.deny:
            return Response({"code": "42501", "message": "permission denied for table nuna_episodes"}, 401)
        return Response([])


@pytest.mark.parametrize("workflow", ["nuna_weekly_gen.yml", "nuna_weekly_publish.yml"])
def test_workflow_secret_reaches_live_and_dry_db_reader(monkeypatch, workflow):
    root = Path(__file__).resolve().parents[3]
    text = (root / ".github" / "workflows" / workflow).read_text()
    match = re.search(r"^\s+SUPABASE_KEY: \$\{\{ secrets\.(\w+) \}\}", text, re.MULTILINE)
    assert match and match[1] == db.SUPABASE_KEY_ENV
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv(match[1], "sb_secret_configured0123456789")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    live = db.make_store(False)
    live.s = Session()
    assert live.get_episode("nuna-2026-W43") is None
    assert live.s.calls[0][2]["headers"]["apikey"] == "sb_secret_configured0123456789"
    assert db.make_store(True)._reader is not None


@pytest.mark.parametrize(("key", "bearer"), [
    ("sb_secret_configured0123456789", False),
    ("sb_publishable_configured0123456789", False),
    ("eyJheader.eyJpayload.signature", True),
])
def test_api_key_header_contract(key, bearer):
    store = db.RestStore(url="https://example.supabase.co", key=key, session=Session())
    headers = store._headers()
    assert headers["apikey"] == key
    assert ("Authorization" in headers) is bearer


def test_read_only_dbcheck_does_not_write_or_read_rows():
    session = Session()
    store = db.RestStore(url="https://example.supabase.co", key="sb_secret_configured0123456789", session=session)
    result = store.check_access()
    assert set(result["readable_tables"]) == db.READ_TABLES
    assert result["write_permission"] == "not_tested"
    assert len(session.calls) == 7
    assert all(method == "GET" and kwargs["params"]["limit"] == "0" for method, _, kwargs in session.calls)


def test_permission_error_identifies_binding_without_key_leak():
    key = "sb_secret_do_not_disclose_0123456789"
    store = db.RestStore(url="https://example.supabase.co", key=key, session=Session(deny=True))
    with pytest.raises(db.StoreError) as caught:
        store.check_access()
    error = str(caught.value)
    assert "DB_PERMISSION_DENIED" in error and "key_source=SUPABASE_KEY" in error
    assert "key_type=secret" in error and key not in error


@pytest.mark.parametrize("command", ["gen", "publish"])
def test_cli_dbcheck_never_initializes_paid_or_publish_context(monkeypatch, command):
    class Store:
        def check_access(self):
            return {"write_permission": "not_tested"}

    calls = []

    def factory(dry):
        calls.append(dry)
        return Store()

    monkeypatch.setattr(db, "make_store", factory)
    monkeypatch.setattr(pipeline, "Ctx", lambda *a, **k: pytest.fail("유료/발행 컨텍스트 초기화"))
    assert pipeline.main([command, "--step", "dbcheck"]) == 0
    assert calls == [False]
