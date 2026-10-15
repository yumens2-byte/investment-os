"""statestore 미러링 결함 회귀 테스트 (D-5 / D-7 / D-9).

2026-10-15 실측:
  D-7  `--overwrite true` 는 gsk 1.11.2 가 --local_file 업로드에서 무시 → 기본 rename →
       publish_state(1).json 중복 생성, 정본 미갱신 → 다음 실행에서 옛 상태 복원 위험.
  D-5  파일이 존재하면 정본이 더 새로워도 복원을 건너뜀 → stale 로컬이 정본을 덮어쓸 위험.
  D-9  AI Drive 자동 복원 중 HTTP 520 1회 — 재시도로 성공.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gen.hero_shorts import statestore


def test_sync_states_uses_on_conflict_overwrite(monkeypatch, tmp_path):
    """D-7 — 로컬파일 업로드는 --on-conflict overwrite 를 써야 한다."""
    f = tmp_path / "publish_state.json"
    f.write_text("{}", encoding="utf-8")
    calls = []

    def fake_gsk(args, retries=3):
        calls.append(args)
        return "{}"

    monkeypatch.setattr(statestore, "_gsk", fake_gsk)
    monkeypatch.setenv("HERO_STATE_SYNC", "1")
    statestore.sync_states([f])

    assert len(calls) == 1, calls
    args = calls[0]
    assert "--on-conflict" in args and "overwrite" in args
    assert "--overwrite" not in args


def test_max_ep_reads_ledger(tmp_path):
    f = tmp_path / "publish_state.json"
    f.write_text(json.dumps({"ep87": {}, "ep88": {}, "_ledger": {}}), encoding="utf-8")
    assert statestore._max_ep(f) == 88


def test_remote_is_newer_adopts_newer_master(monkeypatch, tmp_path):
    """D-5 — 정본에 더 높은 ep 가 있으면 정본 채택."""
    local = tmp_path / "publish_state.json"
    local.write_text(json.dumps({"ep87": {}}), encoding="utf-8")

    def fake_gsk(args, retries=3):
        if args[0] == "aidrive" and args[1] == "download":
            Path(args[-1]).write_text(json.dumps({"ep87": {}, "ep88": {}}), encoding="utf-8")
        return "{}"

    monkeypatch.setattr(statestore, "_gsk", fake_gsk)
    assert statestore._remote_is_newer(local) is True


def test_remote_is_newer_false_when_local_ahead(monkeypatch, tmp_path):
    local = tmp_path / "publish_state.json"
    local.write_text(json.dumps({"ep88": {}}), encoding="utf-8")

    def fake_gsk(args, retries=3):
        if args[0] == "aidrive" and args[1] == "download":
            Path(args[-1]).write_text(json.dumps({"ep87": {}}), encoding="utf-8")
        return "{}"

    monkeypatch.setattr(statestore, "_gsk", fake_gsk)
    assert statestore._remote_is_newer(local) is False


def test_restore_skips_fresh_local(monkeypatch, tmp_path):
    """정본이 뒤처지면 복원하지 않는다 — 불필요한 덮어쓰기 방지."""
    local = tmp_path / "publish_state.json"
    local.write_text(json.dumps({"ep88": {}}), encoding="utf-8")

    def fake_gsk(args, retries=3):
        if args[0] == "aidrive" and args[1] == "download":
            Path(args[-1]).write_text(json.dumps({"ep87": {}}), encoding="utf-8")
        return "{}"

    monkeypatch.setattr(statestore, "_gsk", fake_gsk)
    monkeypatch.setenv("HERO_STATE_SYNC", "1")
    assert statestore.restore_if_missing([local]) == []


def test_gsk_retries_transient_520(monkeypatch):
    """D-9 — HTTP 520 등 일시 장애는 자동 재시도로 흡수한다."""
    calls = {"n": 0}

    class CP:
        def __init__(self, rc, out, err):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return CP(1, "HTTP 520", "server error")
        return CP(0, '{"ok":true}', "")

    monkeypatch.setattr(statestore.subprocess, "run", fake_run)
    monkeypatch.setattr(statestore.time, "sleep", lambda *_: None)
    out = statestore._gsk(["aidrive", "download", "/a", "/b"])
    assert calls["n"] == 2 and "ok" in out


def test_gsk_does_not_retry_permanent_error(monkeypatch):
    """영구 오류는 재시도하지 않고 즉시 실패한다."""
    calls = {"n": 0}

    class CP:
        def __init__(self, rc, out, err):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return CP(1, "not found", "404 no such file")

    monkeypatch.setattr(statestore.subprocess, "run", fake_run)
    try:
        statestore._gsk(["aidrive", "download", "/a", "/b"])
        raise AssertionError("예외가 발생해야 한다")
    except RuntimeError:
        pass
    assert calls["n"] == 1
