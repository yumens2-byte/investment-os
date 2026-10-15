"""P1/P2 회귀 테스트 — 원장 복원(D-1) · 발행 결과 회수(D-2/D-3).

2026-10-15 실측 결함을 고정한다. 네트워크·gsk 호출은 전부 스텁한다(결정론).
"""
import json
from pathlib import Path

import pytest

from gen.hero_shorts import statestore
from gen.hero_shorts.tiktok import recover, runner


# ── P1 · D-1: 복원 전 부모 디렉터리 보장 ────────────────────────────

def test_restore_creates_missing_parent_dir(tmp_path, monkeypatch):
    """부모 디렉터리가 없어도 복원이 성공해야 한다(클린 러너 재현)."""
    target = tmp_path / "gen" / "data" / "publish_state.json"
    assert not target.parent.exists()

    def fake_gsk(args, retries=3):
        if args[:2] == ["aidrive", "download"]:
            dest = Path(args[3])
            dest.write_text(json.dumps({"ep88": {"tiktok": {"status": "published"}}}),
                            encoding="utf-8")
            return "ok"
        raise AssertionError(f"예상 밖 호출: {args}")

    monkeypatch.setattr(statestore, "_gsk", fake_gsk)
    monkeypatch.setenv("HERO_STATE_SYNC", "1")

    restored = statestore.restore_if_missing([target])

    assert restored == [str(target)]
    assert target.exists()
    assert statestore._max_ep(target) == 88


def test_restore_without_parent_guard_would_fail(tmp_path, monkeypatch):
    """가드 회귀 감지 — 부모 부재 시 download 가 예외를 내면 복원이 비어야 한다."""
    target = tmp_path / "nope" / "publish_state.json"

    def failing_gsk(args, retries=3):
        raise RuntimeError("ENOENT: no such file or directory")

    monkeypatch.setattr(statestore, "_gsk", failing_gsk)
    assert statestore.restore_if_missing([target]) == []


# ── P2 · D-2/D-3: Zernio 회수 ──────────────────────────────────────

def test_post_id_from_url():
    assert recover.tiktok_post_id_from_url(
        "https://www.tiktok.com/@tiger182725/video/7696702914986773780"
    ) == "7696702914986773780"
    assert recover.tiktok_post_id_from_url("https://x/y/") is None
    assert recover.tiktok_post_id_from_url(None) is None


_POSTS = [{
    "_id": "6ad02e8459a0b5ccb84a4ba5",
    "platform": "tiktok",
    "status": "published",
    "scheduledFor": "2026-10-15T01:38:12.839Z",
    "publishedAt": "2026-10-15T01:38:23.101Z",
    "platformPostUrl": "https://www.tiktok.com/@tiger182725/video/7696702914986773780",
}]


def recover_normalized():
    """recover.resolve_published_post 의 반환 규약(정규화 키) 재현."""
    return {
        "post_id": "6ad02e8459a0b5ccb84a4ba5",
        "platform_post_id": "7696702914986773780",
        "platform_post_url": "https://www.tiktok.com/@tiger182725/video/7696702914986773780",
        "published_at": "2026-10-15T01:38:23.101Z",
        "status": "published",
        "matched_by": "post_url",
    }


def test_resolve_by_url(monkeypatch):
    monkeypatch.setattr(recover, "list_tiktok_posts", lambda **k: _POSTS)
    got = recover.resolve_published_post(
        post_url="https://www.tiktok.com/@tiger182725/video/7696702914986773780")
    assert got["post_id"] == "6ad02e8459a0b5ccb84a4ba5"
    assert got["platform_post_id"] == "7696702914986773780"
    assert got["published_at"] == "2026-10-15T01:38:23.101Z"
    assert got["matched_by"] == "post_url"


def test_resolve_by_id(monkeypatch):
    monkeypatch.setattr(recover, "list_tiktok_posts", lambda **k: _POSTS)
    got = recover.resolve_published_post(post_id="6ad02e8459a0b5ccb84a4ba5")
    assert got["matched_by"] == "post_id"


def test_resolve_fails_closed_on_mismatch(monkeypatch):
    """매칭 실패 시 추측하지 않는다 — 엉뚱한 회차를 기록하면 멱등성이 깨진다."""
    monkeypatch.setattr(recover, "list_tiktok_posts", lambda **k: _POSTS)
    assert recover.resolve_published_post(post_url="https://www.tiktok.com/@x/video/123") is None


def test_resolve_never_uses_latest_by_default(monkeypatch):
    monkeypatch.setattr(recover, "list_tiktok_posts", lambda **k: _POSTS)
    assert recover.resolve_published_post() is None
    assert recover.resolve_published_post(allow_latest=True)["matched_by"] == "latest"


def test_record_recovers_platform_truth(tmp_path, monkeypatch):
    """원장에 로컬 now 대신 플랫폼 publishedAt·post_id 가 박혀야 한다(D-2/D-3)."""
    state_path = tmp_path / "publish_state.json"
    state_path.write_text(json.dumps({
        "ep88": {"title": "Ep88", "approval": {"status": "APPROVED"},
                 "combined": {"title": "Ep88", "duration_sec": 30.433,
                              "media_url": "https://www.genspark.ai/api/files/s/ioJNCDQU"}}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(recover, "resolve_published_post",
                        lambda **k: recover_normalized())
    monkeypatch.setattr(statestore, "sync_states", lambda paths: [])
    monkeypatch.setenv("HERO_TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE")

    rc = runner.main(["record", "--ep", "88", "--status", "published",
                      "--post-url", _POSTS[0]["platformPostUrl"],
                      "--state", str(state_path)])
    assert rc == 0
    tk = json.loads(state_path.read_text(encoding="utf-8"))["ep88"]["tiktok"]
    assert tk["post_id"] == "6ad02e8459a0b5ccb84a4ba5"
    assert tk["platform_post_id"] == "7696702914986773780"
    assert tk["published_at"] == "2026-10-15T01:38:23.101Z"
    assert tk["published_at_source"] == "zernio_platform"
    assert tk["mode"] == "combined"
    assert tk["segments"][0]["platform_post_id"] == "7696702914986773780"
    assert tk["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_record_marks_local_now_when_unrecoverable(tmp_path, monkeypatch):
    """회수 실패 시에도 출처를 숨기지 않는다(감사 정직성)."""
    state_path = tmp_path / "publish_state.json"
    state_path.write_text(json.dumps({"ep90": {}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(statestore, "sync_states", lambda paths: [])

    rc = runner.main(["record", "--ep", "90", "--status", "published",
                      "--post-url", "https://www.tiktok.com/@x/video/999",
                      "--no-recover", "--state", str(state_path)])
    assert rc == 0
    tk = json.loads(state_path.read_text(encoding="utf-8"))["ep90"]["tiktok"]
    assert tk["published_at_source"] == "local_now"


def test_record_published_requires_post_url(tmp_path, monkeypatch):
    state_path = tmp_path / "publish_state.json"
    state_path.write_text(json.dumps({"ep91": {}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(statestore, "sync_states", lambda paths: [])
    rc = runner.main(["record", "--ep", "91", "--status", "published",
                      "--no-recover", "--state", str(state_path)])
    assert rc == 2


def test_record_is_idempotent(tmp_path, monkeypatch):
    state_path = tmp_path / "publish_state.json"
    state_path.write_text(json.dumps({
        "ep88": {"tiktok": {"status": "published", "post_url": "https://x/1"}}
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(statestore, "sync_states", lambda paths: [])
    rc = runner.main(["record", "--ep", "88", "--status", "published",
                      "--post-url", "https://x/2", "--state", str(state_path)])
    assert rc == 0
    tk = json.loads(state_path.read_text(encoding="utf-8"))["ep88"]["tiktok"]
    assert tk["post_url"] == "https://x/1"      # 재기록 거부(멱등)
