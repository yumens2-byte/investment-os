"""verify — 발행 후 플랫폼 대조 검증 회귀 테스트(2026-10-15).

핵심 회귀 대상
    - 자기 보고가 아니라 list_posts 실조회로 판정한다.
    - D-2(식별자 누락) · D-3(발행시각 로컬 오기) 재발을 잡는다.
    - 매칭 실패는 추측하지 않고 not_found 로 보고한다.
"""
import pytest

from gen.hero_shorts.tiktok import verify as vf


def post(_id="z1", plat="https://www.tiktok.com/@tiger182725/video/7696702914986773780",
         at="2026-10-15T01:38:23.101Z", status="published"):
    return {"_id": _id, "platform": "tiktok", "platformPostUrl": plat,
            "publishedAt": at, "status": status}


def lister(*posts):
    return lambda account_id=None, limit=20: list(posts)


# ------------------------------------------------------------------ 판정 6종
def test_confirmed():
    v = vf.verify_published(post_id="z1", lister=lister(post()))
    assert v["ok"] is True and v["verdict"] == vf.CONFIRMED
    assert v["platform_post_id"] == "7696702914986773780"
    assert v["published_at_source"] == "platform" and v["matched_by"] == "post_id"


def test_match_by_platform_url_when_post_id_absent():
    v = vf.verify_published(post_url="https://www.tiktok.com/@tiger182725/video/7696702914986773780",
                            lister=lister(post()))
    assert v["ok"] is True and v["matched_by"] == "post_url"


def test_url_missing_is_d2_pattern():
    v = vf.verify_published(post_id="z1", lister=lister(post(plat=None)))
    assert v["ok"] is False and v["verdict"] == vf.URL_MISSING
    assert any("D-2" in p for p in v["problems"])


def test_time_missing():
    v = vf.verify_published(post_id="z1", lister=lister(post(at=None)))
    assert v["ok"] is False and v["verdict"] == vf.TIME_MISSING


def test_not_found_does_not_guess():
    v = vf.verify_published(post_id="nope", lister=lister(post()))
    assert v["ok"] is False and v["verdict"] == vf.NOT_FOUND
    assert v["platform_post_id"] is None and v["platform_post_url"] is None
    assert any("검증 범위 밖" in p for p in v["problems"])


def test_duplicate_detected():
    v = vf.verify_published(post_id="z1", lister=lister(post("z1"), post("z2")))
    assert v["verdict"] == vf.DUPLICATE and v["ok"] is False


def test_unknown_when_query_fails():
    def boom(account_id=None, limit=20):
        raise RuntimeError("gsk 없음")
    v = vf.verify_published(post_id="z1", lister=boom)
    assert v["verdict"] == vf.UNKNOWN and v["ok"] is False
    assert "gsk 없음" in v["problems"][0]


def test_allow_latest_is_flagged():
    v = vf.verify_published(lister=lister(post()), allow_latest=True)
    assert v["ok"] is True and v["matched_by"] == "latest"
    assert any("감사 전용" in p for p in v["problems"])


# ------------------------------------------------------------------ 원장 대조
def test_ledger_drift_46min_detected():
    entry = {"tiktok": {"post_id": "z1",
                        "platform_post_id": "7696702914986773780",
                        "post_url": "https://www.tiktok.com/@tiger182725/video/7696702914986773780",
                        "published_at": "2026-10-15T02:24:00.000Z"}}
    v = vf.verify_published(post_id="z1", lister=lister(post()))
    r = vf.check_ledger(entry, v)
    assert r["drift_min"] == pytest.approx(45.6, abs=0.2)
    assert any("드리프트" in p and "D-3" not in p for p in r["problems"])


def test_ledger_missing_id_detected():
    entry = {"tiktok": {"post_id": None, "published_at": "2026-10-15T01:38:23.101Z"}}
    v = vf.verify_published(post_id="z1", lister=lister(post()))
    r = vf.check_ledger(entry, v)
    assert any("누락" in p for p in r["problems"])


def test_ledger_clean_has_no_problems():
    entry = {"tiktok": {"post_id": "z1", "platform_post_id": "7696702914986773780",
                        "post_url": post()["platformPostUrl"],
                        "published_at": "2026-10-15T01:38:23.101Z"}}
    v = vf.verify_published(post_id="z1", lister=lister(post()))
    r = vf.check_ledger(entry, v)
    assert r["problems"] == [] and r["drift_min"] == 0.0


# ------------------------------------------------------------------ 기록·경보
def test_record_verification_keeps_status():
    entry = {"tiktok": {"status": "published"}}
    v = vf.verify_published(post_id="z1", lister=lister(post()))
    vf.record_verification(v, entry)
    assert entry["tiktok"]["status"] == "published"
    assert entry["tiktok"]["verification"]["verdict"] == vf.CONFIRMED


def test_alert_skipped_when_ok(monkeypatch):
    sent = []
    monkeypatch.setattr(vf, "alert", vf.alert)
    assert vf.alert({"ok": True, "verdict": vf.CONFIRMED}) == "(경보 없음 — 검증 통과)"
    assert sent == []


def test_format_verdict_lists_problems():
    v = vf.verify_published(post_id="z1", lister=lister(post(plat=None)))
    text = vf.format_verdict(v, ledger_problems=["원장 post_id 누락"], ep=88)
    assert "URL_MISSING" in text and "ep88" in text
    assert "D-2" in text and "원장 post_id 누락" in text
