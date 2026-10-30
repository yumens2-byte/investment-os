"""DN2026_0079 생성→발행 연결 — gen 출력(chain) 계약과 두 워크플로 구조 회귀.

배경: 독립 cron 두 개(gen 06:47·publish 08:12 KST)는 서로 다른 시간만큼 지연된다(W44 실측 gen +3h29m,
publish +3h11m). 발행이 '알림+62분' 이후 시작되면 예약 시각 경과로 영구 차단되므로, 생성 실행이 이번
실행에서 live 로 NOTIFIED 에 도달했을 때만 같은 실행의 publish job 이 발행한다.
job output 은 secret 값을 포함하면 가려지므로(GitHub 문서) 'true'/'false' 값을 output 으로 쓰지 않는다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gen.nuna_weekly import db, pipeline
from gen.nuna_weekly.backends import make_backends
from gen.nuna_weekly.tests.test_pipeline import RUN_DATE, _daily, _NoTelegram, env  # noqa: F401 (fixture)
from gen.nuna_weekly.zernio import DummyZernioClient

ROOT = Path(__file__).resolve().parents[3]
GEN_YML = ROOT / ".github" / "workflows" / "nuna_weekly_gen.yml"
PUB_YML = ROOT / ".github" / "workflows" / "nuna_weekly_publish.yml"
GEN_ONLY_KEYS = {"FAL_AI_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_SUB_PAY_KEY"}
LIVE, NONE = pipeline.CHAIN_PUBLISH_LIVE, pipeline.CHAIN_NONE


# --------------------------------------------------------------------------- #
# gen_outputs — 연결 판정
# --------------------------------------------------------------------------- #
def _fake_ctx(status: str | None, dry: bool) -> SimpleNamespace:
    store = db.MemoryStore()
    if status is not None:
        store.create_episode({"episode_id": "nuna-2026-W45", "publish_date": "2026-11-05", "us_days": [],
                              "duration_target_sec": 60, "pipeline_version": "1.0.0"})
        store.episodes["nuna-2026-W45"]["status"] = status
    return SimpleNamespace(store=store, episode_id="nuna-2026-W45", dry=dry)


@pytest.mark.parametrize(("before", "after", "dry", "rc", "expected"), [
    (None, "NOTIFIED", False, 0, LIVE),            # 새 회차를 이번 실행에서 끝까지 생성
    ("IMAGED", "NOTIFIED", False, 0, LIVE),        # 중간 재개 후 이번 실행에서 알림
    ("STAGED", "NOTIFIED", False, 0, LIVE),        # step=notify 단독 실행
    (None, "NOTIFIED", True, 0, NONE),             # dry 는 운영 원장이 없어 발행 불가
    ("NOTIFIED", "NOTIFIED", False, 0, NONE),      # 이미 알림된 회차 재실행 — 중복 발행 연결 금지
    ("SCHEDULED", "SCHEDULED", False, 0, NONE),
    (None, "SKIPPED", False, 0, NONE),             # 주말·휴장 스킵
    ("IMAGED", "IMAGED", False, 1, NONE),          # 단계 실패
    ("FAILED", "FAILED", False, 0, NONE),          # 종결 회차
    (None, None, False, 1, NONE),                  # 원장 기록 없음
])
def test_gen_outputs_chain_matrix(before, after, dry, rc, expected):
    ctx = _fake_ctx(after, dry)
    out = pipeline.gen_outputs(ctx, RUN_DATE, before, rc)
    assert out["chain"] == expected
    assert out["episode_id"] == "nuna-2026-W45"
    assert out["run_date"] == RUN_DATE.isoformat()
    assert out["episode_status"] == (after or "")


def test_gen_outputs_never_emit_boolean_strings():
    """secrets.DRY_RUN·FORCE_RUN 같은 'true'/'false' 값과 같으면 runner 가 output 을 가린다."""
    for status, dry in (("NOTIFIED", False), ("NOTIFIED", True), ("SKIPPED", False), (None, False)):
        out = pipeline.gen_outputs(_fake_ctx(status, dry), RUN_DATE, None, 0)
        assert not {v.lower() for v in out.values()} & {"true", "false", "1", "0", "yes", "no"}


def test_write_gh_outputs_appends_and_skips_without_path(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"
    target.write_text("prev=1\n", encoding="utf-8")
    assert pipeline.write_gh_outputs({"a": "1", "b": "x"}, str(target)) is True
    assert target.read_text(encoding="utf-8") == "prev=1\na=1\nb=x\n"
    monkeypatch.delenv(pipeline.GH_OUTPUT_ENV, raising=False)
    assert pipeline.write_gh_outputs({"a": "1"}) is False


# --------------------------------------------------------------------------- #
# main() → GITHUB_OUTPUT → 같은 회차 발행 (통합, dummy 백엔드·메모리 원장)
# --------------------------------------------------------------------------- #
def _patch_ctx(monkeypatch, store, tg):
    real_ctx = pipeline.Ctx

    def factory(run_date, dry):
        zc = {a: DummyZernioClient(a) for a in ("main", "sub")}
        return real_ctx(run_date, dry, store=store, backends=make_backends(True), zernio_clients=zc, telegram=tg)

    monkeypatch.setattr(pipeline, "Ctx", factory)


def _fast_gen(monkeypatch, status: str = "NOTIFIED") -> None:
    """main 의 출력 경로만 보는 테스트용 — 실제 생성(조립 약 60초) 대신 원장 상태만 맞춘다(CI 15분 상한 보호)."""
    def fake_run_gen(ctx, only=None):
        ctx.store.create_episode({"episode_id": ctx.episode_id, "publish_date": ctx.gate.publish_date.isoformat(),
                                  "us_days": [], "duration_target_sec": 60, "pipeline_version": "test"})
        ctx.store.episodes[ctx.episode_id]["status"] = status
        return 0

    monkeypatch.setattr(pipeline, "run_gen", fake_run_gen)


def _read_outputs(path: Path) -> dict[str, str]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    return dict(ln.split("=", 1) for ln in lines)


def test_main_live_gen_emits_chain_then_chained_publish_schedules(env, monkeypatch):  # noqa: F811
    store = db.MemoryStore(daily=_daily())
    _patch_ctx(monkeypatch, store, _NoTelegram())
    out_file = env / "gh_output.txt"
    monkeypatch.setenv(pipeline.GH_OUTPUT_ENV, str(out_file))
    monkeypatch.setenv("DRY_RUN", "false")

    assert pipeline.main(["gen", "--step", "run", "--date", RUN_DATE.isoformat()]) == 0
    outputs = _read_outputs(out_file)
    assert outputs == {"episode_id": "nuna-2026-W43", "run_date": "2026-10-22",
                       "episode_status": "NOTIFIED", "chain": LIVE}

    # publish job 과 같은 입력(DRY_RUN=false 고정, --date run_date)으로 같은 회차를 발행 — 예약까지 진행돼야 한다
    ctx = pipeline.Ctx(RUN_DATE, False)
    assert ctx.episode_id == outputs["episode_id"]
    assert pipeline.run_publish(ctx, "publish", sleep=pipeline.datetime.advance) == 0
    assert store.get_episode(ctx.episode_id)["status"] == "SCHEDULED"

    # 같은 회차 gen 재실행은 발행을 다시 연결하지 않는다
    out_file.write_text("", encoding="utf-8")
    assert pipeline.main(["gen", "--step", "run", "--date", RUN_DATE.isoformat()]) == 0
    assert _read_outputs(out_file)["chain"] == NONE


def test_main_dry_gen_never_chains_publish(env, monkeypatch):  # noqa: F811
    store = db.MemoryStore(daily=_daily())
    _patch_ctx(monkeypatch, store, _NoTelegram())
    _fast_gen(monkeypatch, "NOTIFIED")
    out_file = env / "gh_output.txt"
    monkeypatch.setenv(pipeline.GH_OUTPUT_ENV, str(out_file))
    monkeypatch.setenv("DRY_RUN", "true")

    assert pipeline.main(["gen", "--step", "run", "--date", RUN_DATE.isoformat()]) == 0
    outputs = _read_outputs(out_file)
    assert outputs["episode_status"] == "NOTIFIED"
    assert outputs["chain"] == NONE


def test_main_weekend_skip_never_chains_publish(env, monkeypatch):  # noqa: F811
    store = db.MemoryStore(daily=_daily())
    _patch_ctx(monkeypatch, store, _NoTelegram())
    out_file = env / "gh_output.txt"
    monkeypatch.setenv(pipeline.GH_OUTPUT_ENV, str(out_file))
    monkeypatch.setenv("DRY_RUN", "false")

    assert pipeline.main(["gen", "--step", "run", "--date", "2026-10-24"]) == 0   # 토요일
    outputs = _read_outputs(out_file)
    assert outputs["episode_status"] == "SKIPPED"
    assert outputs["chain"] == NONE


def test_main_output_failure_fails_job_with_manual_publish_guidance(env, monkeypatch, caplog):  # noqa: F811
    """출력 기록이 실패하면 rc=1(publish job skipped) + 수동 발행 안내 로그. 출력 파일은 남기지 않는다."""
    store = db.MemoryStore(daily=_daily())
    _patch_ctx(monkeypatch, store, _NoTelegram())
    _fast_gen(monkeypatch, "NOTIFIED")
    out_file = env / "gh_output.txt"
    monkeypatch.setenv(pipeline.GH_OUTPUT_ENV, str(out_file))
    monkeypatch.setenv("DRY_RUN", "false")

    def boom(*a, **k):
        raise RuntimeError("supabase 일시 오류")

    monkeypatch.setattr(pipeline, "gen_outputs", boom)
    with caplog.at_level(logging.ERROR, logger="nuna_weekly.pipeline"):
        assert pipeline.main(["gen", "--step", "run", "--date", RUN_DATE.isoformat()]) == 1
    assert not out_file.exists()
    assert store.get_episode("nuna-2026-W43")["status"] == "NOTIFIED"
    assert "발행 연결 출력 실패" in caplog.text and "nuna_weekly_publish.yml" in caplog.text


# --------------------------------------------------------------------------- #
# 워크플로 구조 (PyYAML 없이 정규식 — nuna CI job 의존성 그대로)
# --------------------------------------------------------------------------- #
def _job_block(text: str, job: str) -> str:
    body = text.split("\njobs:\n", 1)[1]
    starts = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.MULTILINE))
    for i, m in enumerate(starts):
        if m.group(1) == job:
            end = starts[i + 1].start() if i + 1 < len(starts) else len(body)
            return body[m.end():end]
    raise AssertionError(f"job 없음: {job}")


def _env_keys(block: str) -> set[str]:
    return set(re.findall(r"^          ([A-Z][A-Z0-9_]+):", block, re.MULTILINE))


def test_publish_workflow_has_no_independent_schedule():
    """재현: 수정 전에는 publish 가 독립 cron("12 23 * * 3")을 가져 생성 지연과 무관하게 먼저·늦게 실행됐다."""
    text = PUB_YML.read_text(encoding="utf-8")
    assert "schedule:" not in text
    assert "cron:" not in text
    assert "workflow_dispatch:" in text
    assert re.search(r"^    if: github\.event_name == 'workflow_dispatch'\s*$", text, re.MULTILINE)
    assert re.search(r"^    timeout-minutes: 120\s*$", text, re.MULTILINE)


def test_gen_workflow_chains_publish_job_on_chain_token():
    text = GEN_YML.read_text(encoding="utf-8")
    gen = _job_block(text, "gen")
    pub = _job_block(text, "publish")
    assert "        id: gen_run" in gen
    # 아티팩트 업로드 실패가 gen job 을 실패시켜 publish job 을 skipped 시키지 않는다
    upload = gen.split("- name: QC 결과 업로드", 1)[1]
    assert re.search(r"^        continue-on-error: true\s*$", upload, re.MULTILINE)
    assert "      chain: ${{ steps.gen_run.outputs.chain }}" in gen
    assert "      run_date: ${{ steps.gen_run.outputs.run_date }}" in gen
    assert "      episode_id: ${{ steps.gen_run.outputs.episode_id }}" in gen
    assert re.search(r"^    needs: gen\s*$", pub, re.MULTILINE)
    assert re.search(r"^    if: needs\.gen\.outputs\.chain == 'publish-live' && vars\.NUNA_ENABLED == '1'\s*$",
                     pub, re.MULTILINE)
    assert re.search(r"^    timeout-minutes: 120\s*$", pub, re.MULTILINE)
    assert re.search(r'^          DRY_RUN: "false"\s*$', pub, re.MULTILINE)
    assert "needs.gen.outputs.dry_run" not in text                 # 불리언 output 전달 금지(마스킹)
    assert 'if [ -z "$IN_DATE" ] || [ -z "$IN_EPISODE" ]; then' in pub   # fail-closed
    assert 'python -m gen.nuna_weekly publish --step publish --date "$IN_DATE"' in pub
    assert text.count("python -m gen.nuna_weekly publish") == 1   # 발행 호출 지점 1개 (STD-SCHED-04)


def test_chained_publish_has_no_generation_keys_and_matches_manual_publish_keys():
    """15.2 최소 권한 — 연결 발행 job 에 생성 키 없음, 두 발행 경로의 secret·vars 키 집합 동일."""
    pub_job = _job_block(GEN_YML.read_text(encoding="utf-8"), "publish")
    manual = _job_block(PUB_YML.read_text(encoding="utf-8"), "publish")
    chained_keys = _env_keys(pub_job)
    manual_keys = _env_keys(manual)
    assert not chained_keys & GEN_ONLY_KEYS
    runtime_only = {"IN_STEP", "IN_DATE", "IN_EPISODE"}
    assert chained_keys - runtime_only == manual_keys - runtime_only
