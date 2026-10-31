"""DN2026_0084 읽기 전용 계정 진단 — 키별 /accounts/health 로 설정 계정 ID 위치·매핑 판정 (ID 미출력)."""
from __future__ import annotations

from gen.nuna_weekly import pipeline, publish

ACCTS = {"instagram": "ig-111", "threads": "th-222", "youtube": "yt-333", "facebook": "fb-444"}


class _Client:
    def __init__(self, health=None, fail=False):
        self.health, self.fail, self.calls = health, fail, []

    def accounts_health(self):
        self.calls.append("accounts_health")
        if self.fail:
            raise RuntimeError("HTTP 500")
        return self.health

    def create_post(self, *a, **k):  # 호출되면 안 된다
        raise AssertionError("POST 금지")


def _h(*rows):
    return {"accounts": [dict(r) for r in rows]}


def test_report_all_mapped_ok():
    health = {"main": _h({"platform": "instagram", "accountId": "ig-111", "canPost": True},
                         {"platform": "threads", "_id": "th-222", "canPost": True}),
              "sub": _h({"platform": "youtube", "id": "yt-333", "canPost": True},
                        {"platform": "facebook", "accountId": "fb-444", "canPost": True})}
    rep = publish.account_report(health, ACCTS)
    assert all(r["mapping_ok"] for r in rep)
    assert {r["platform"]: r["found_in"] for r in rep} == {"instagram": ["main"], "threads": ["main"],
                                                           "youtube": ["sub"], "facebook": ["sub"]}


def test_report_detects_swapped_key_and_missing_account():
    # W44 상황 재현: main 에 IG·Threads 0건, IG 는 sub 에 있음, Threads 는 어디에도 없음
    health = {"main": _h({"platform": "youtube", "accountId": "other-yt"}),
              "sub": _h({"platform": "instagram", "accountId": "ig-111", "canPost": True},
                        {"platform": "youtube", "accountId": "yt-333"}, {"platform": "facebook", "accountId": "fb-444"})}
    rep = {r["platform"]: r for r in publish.account_report(health, ACCTS)}
    assert rep["instagram"]["found_in"] == ["sub"] and not rep["instagram"]["mapping_ok"]
    assert rep["threads"]["found_in"] == [] and not rep["threads"]["mapping_ok"]
    assert rep["instagram"]["per_alias"]["main"]["platform_accounts"] == 0
    lines = publish.account_report_lines(list(rep.values()))
    joined = "\n".join(lines)
    assert "매핑 불일치 → sub" in joined and "어느 키에도 없음" in joined
    for v in ACCTS.values():                       # 계정 ID 값은 출력하지 않는다
        assert v not in joined


def test_report_marks_failed_lookup_and_unset_id():
    rep = {r["platform"]: r for r in publish.account_report({"main": None, "sub": _h()},
                                                             {**ACCTS, "threads": ""})}
    assert rep["threads"]["account_set"] is False
    assert rep["instagram"]["per_alias"]["main"] == {"ok": False}
    joined = "\n".join(publish.account_report_lines(list(rep.values())))
    assert "main=조회실패" in joined and "ID 미설정" in joined


def test_run_accounts_check_is_read_only_and_annotates(capsys):
    main = _Client(_h({"platform": "instagram", "accountId": "ig-111"}, {"platform": "threads", "accountId": "th-222"}))
    sub = _Client(fail=True)
    rc = pipeline.run_accounts_check({"main": main, "sub": sub}, ACCTS)
    out = capsys.readouterr().out
    assert rc == 1                                  # sub 조회 실패 → YT·FB 매핑 확인 불가
    assert main.calls == ["accounts_health"] and sub.calls == ["accounts_health"]
    assert out.count("::notice title=NUNA 계정 진단::") == 2
    assert out.count("::warning title=NUNA 계정 진단::") == 2
    for v in ACCTS.values():
        assert v not in out


def test_cli_accounts_step_allowed_only_for_publish(monkeypatch):
    called = []
    monkeypatch.setattr(pipeline, "run_accounts_check", lambda: called.append(1) or 0)
    assert pipeline.main(["publish", "--step", "accounts"]) == 0
    assert called == [1]
    assert pipeline.main(["gen", "--step", "accounts"]) == 1   # gen 에서는 허용값 아님
