"""
gen/nuna_weekly/pipeline.py — 오케스트레이터 (설계서 3장 처리 흐름, 7장 상태 전이)
====================================================================================
명령:
    python -m gen.nuna_weekly gen     [--step run|<단계>] [--date YYYY-MM-DD] [--scheduled]
    python -m gen.nuna_weekly publish [--step publish|verify] [--date ...] [--scheduled]

원칙
    - 재개: 실패해도 상태를 FAILED 로 덮지 않는다. 마지막 성공 상태에 머물고 last_error 만 기록 →
      재실행 시 다음 단계부터. 이미지·클립은 assets_json 의 원격 URL 로 재다운로드(재과금 없음).
    - 조건부 전이: store.transition(expect=이전 상태) — 0건이면 다른 실행이 선점한 것으로 보고 중단.
    - DRY_RUN(기본): dummy 백엔드·메모리 원장·Zernio POST 없음·Telegram 전송 없음.
    - schedule 실행은 vars NUNA_ENABLED=1 일 때만.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import VERSION as PKG_VERSION
from . import assemble, calendar_gate, collect, config, db, qc, secret_env
from . import budget as budget_mod
from . import clips as clips_mod
from . import images as images_mod
from . import publish as publish_mod
from . import script as script_mod
from . import tts as tts_mod
from .backends import make_backends
from .models import Asset, Facts, Script

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.pipeline")

GEN_STEPS: tuple[tuple[str, str, str], ...] = (
    # (step, 선행 상태, 완료 상태)
    ("collect", "PLANNED", "COLLECTED"),
    ("script", "COLLECTED", "SCRIPTED"),
    ("tts", "SCRIPTED", "VOICED"),
    ("images", "VOICED", "IMAGED"),
    ("clips", "IMAGED", "CLIPPED"),
    ("assemble", "CLIPPED", "ASSEMBLED"),
    ("qc", "ASSEMBLED", "QC_PASSED"),
    ("stage", "QC_PASSED", "STAGED"),
    ("notify", "STAGED", "NOTIFIED"),
)
DONE_GEN = ("NOTIFIED", "HELD", "SCHEDULED", "PUBLISHED", "PARTIAL")


class PipelineError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 실행 컨텍스트
# --------------------------------------------------------------------------- #
class Ctx:
    def __init__(self, run_date: date, dry: bool, store=None, backends=None,
                 zernio_clients: dict[str, Any] | None = None, telegram=None) -> None:
        self.run_date = run_date
        self.dry = dry
        self.store = store if store is not None else db.make_store(dry)
        self._backends = backends
        self._zernio = zernio_clients
        self._telegram = telegram
        self.gate = calendar_gate.evaluate(run_date)
        self.episode_id = self.gate.episode_id
        self.out = config.out_dir(self.episode_id)
        self._budget = None

    @property
    def backends(self):
        # 발행 워크플로에는 생성 API 키가 없다. 생성 단계에서만 초기화한다.
        if self._backends is None:
            self._backends = make_backends(self.dry)
        return self._backends

    @property
    def budget(self) -> budget_mod.Budget:
        if self._budget is None:
            self._budget = budget_mod.Budget(self.store, self.episode_id)
        return self._budget

    def zernio(self) -> dict[str, Any]:
        if self._zernio is None:
            from .zernio import DummyZernioClient, ZernioClient

            if self.dry:
                self._zernio = {a: DummyZernioClient(a) for a in ("main", "sub")}
            else:
                self._zernio = {a: ZernioClient(a) for a in ("main", "sub")}
        return self._zernio

    def telegram(self):
        """DRY_RUN 이고 토큰이 없으면 None — 알림 생략."""
        if self._telegram is None:
            if self.dry and not (secret_env.optional("TELEGRAM_BOT_TOKEN")
                                 and secret_env.optional("TELEGRAM_PAID_CHANNEL_ID")):
                return None
            from .notify import TelegramClient

            self._telegram = TelegramClient()
        return self._telegram

    def episode(self) -> dict[str, Any]:
        ep = self.store.get_episode(self.episode_id)
        if not ep:
            raise PipelineError(f"원장에 회차 없음: {self.episode_id}")
        return ep


def _report(ctx: Ctx, text: str) -> None:
    tg = ctx.telegram()
    if tg is None:
        log.info("[NunaWeekly:pipeline] 리포트(전송 생략): %s", text)
        return
    tg.send_report(text, ctx.dry)


def _step_log(ctx: Ctx, step: str, status: str, started: float, error: str | None = None) -> None:
    try:
        ctx.store.step_run({"episode_id": ctx.episode_id, "step": step, "gh_run_id": os.environ.get("GH_RUN_ID"),
                            "status": status, "duration_ms": int((time.time() - started) * 1000),
                            "finished_at": datetime.now(config.KST).isoformat(),
                            "error": secret_env.redact(error, 500) if error else None, "version": VERSION})
    except Exception as e:  # 실행 로그 실패는 본 흐름을 막지 않는다
        log.warning("step_runs 기록 실패: %s", secret_env.redact(str(e), 200))


# --------------------------------------------------------------------------- #
# 자산 직렬화·복원
# --------------------------------------------------------------------------- #
def _assets_dump(d: dict[str, Asset]) -> dict[str, dict[str, Any]]:
    return {k: asdict(v) for k, v in d.items()}


def _assets_load(d: dict[str, Any] | None) -> dict[str, Asset]:
    return {k: Asset(**v) for k, v in (d or {}).items()}


def _rehydrate(assets: dict[str, Asset], out: Path) -> None:
    """로컬 파일이 없으면 원격 URL 로 재다운로드 (새 러너에서 재개 시). dummy URL 은 건너뛴다."""
    import requests

    for key, a in assets.items():
        p = Path(a.local_path)
        if p.is_file():
            continue
        if not a.remote_url or not a.remote_url.startswith("https://") or ".invalid/" in a.remote_url:
            raise PipelineError(f"자산 복원 불가(원격 URL 없음): {key}")
        target = out / p.name
        r = requests.get(a.remote_url, timeout=120)
        if r.status_code != 200 or len(r.content) < 1024:
            raise PipelineError(f"자산 복원 실패 HTTP {r.status_code}: {key}")
        target.write_bytes(r.content)
        images_mod.write_meta(target, a.remote_url, a.request_id)
        a.local_path = str(target)
        log.info("자산 복원: %s (%dB)", key, len(r.content))


def _audio_by_seg(assets: list[Asset]) -> dict[str, Asset]:
    return {a.key.split("_", 1)[1]: a for a in assets}


# --------------------------------------------------------------------------- #
# 생성 단계 구현
# --------------------------------------------------------------------------- #
def _facts(ep: dict[str, Any]) -> Facts:
    if not ep.get("facts_json"):
        raise PipelineError("facts_json 없음 — collect 선행 필요")
    return Facts.from_dict(ep["facts_json"])


def _script(ep: dict[str, Any]) -> Script:
    if not ep.get("script_json"):
        raise PipelineError("script_json 없음 — script 선행 필요")
    return Script.from_dict(ep["script_json"])


def _segs() -> list[config.Segment]:
    return config.segments(config.duration_sec())


def _variants() -> list[str]:
    v = ["main"]
    if config.duration_sec() == 70 and config.fb_cut_enabled():
        v.append("fb_cut")
    return v


def _do_step(ctx: Ctx, step: str) -> dict[str, Any]:
    """단계 1개 실행 → 원장에 기록할 필드 반환."""
    ep = ctx.episode()
    segs = _segs()
    assets_json = dict(ep.get("assets_json") or {})
    if step == "collect":
        facts = collect.build_facts(ctx.gate, ctx.store)
        return {"facts_json": facts.to_dict()}
    if step == "script":
        sc = script_mod.generate(_facts(ep), segs, ctx.backends.llm, ctx.budget, ctx.gate.publish_date)
        return {"script_json": sc.to_dict()}
    if step == "tts":
        audio = tts_mod.synthesize_segments(_script(ep), segs, ctx.backends.tts, ctx.budget, ctx.out)
        assets_json["audio"] = _assets_dump({a.key: a for a in audio})
        return {"assets_json": assets_json}
    if step == "images":
        imgs = images_mod.generate_images(ctx.episode_id, _script(ep), segs, ctx.backends.fal, ctx.budget, ctx.out)
        assets_json["images"] = _assets_dump(imgs)
        return {"assets_json": assets_json}
    if step == "clips":
        imgs = _assets_load(assets_json.get("images"))
        if not ctx.backends.is_dummy:
            _rehydrate(imgs, ctx.out)
        cl = clips_mod.generate_clips(ctx.episode_id, _script(ep), segs, imgs, ctx.backends.fal, ctx.budget, ctx.out)
        assets_json["clips"] = _assets_dump(cl)
        return {"assets_json": assets_json}
    if step == "assemble":
        sc = _script(ep)
        imgs = _assets_load(assets_json.get("images"))
        cl = _assets_load(assets_json.get("clips"))
        audio = _assets_load(assets_json.get("audio"))
        if not ctx.backends.is_dummy:
            _rehydrate(imgs, ctx.out)
            _rehydrate(cl, ctx.out)
        if any(not Path(a.local_path).is_file() for a in audio.values()):
            log.info("음성 파일 없음(새 러너) — TTS 재생성")
            new_audio = tts_mod.synthesize_segments(sc, segs, ctx.backends.tts, ctx.budget, ctx.out)
            audio = {a.key: a for a in new_audio}
            assets_json["audio"] = _assets_dump(audio)
        videos = {}
        for v in _variants():
            out = assemble.build(v, segs, sc, imgs, cl, _audio_by_seg(list(audio.values())), ctx.out)
            videos[v] = str(out)
        assets_json["videos"] = videos
        return {"assets_json": assets_json}
    if step == "qc":
        sc, facts = _script(ep), _facts(ep)
        reports = {}
        for v, path in (assets_json.get("videos") or {}).items():
            if not Path(path).is_file():
                raise PipelineError(f"영상 파일 없음(재조립 필요): {v}")
            target = float(sum(s.seconds for s in assemble.select_segments(v, segs)))
            rep = qc.run(Path(path), v, target, sc, facts, ctx.gate.publish_date,
                         verify_numbers=script_mod.verify_numbers)
            reports[v] = asdict(rep)
            if not rep.passed:
                failed = [g for g, r in rep.gates.items() if not r.get("ok")]
                raise PipelineError(f"QC 실패 {v}: {failed}")
        return {"qc_json": reports}
    if step == "stage":
        media: dict[str, dict[str, str]] = {}
        zc = ctx.zernio()
        for v, path in (assets_json.get("videos") or {}).items():
            aliases = ["main", "sub"] if v == "main" else ["sub"]
            media[v] = {}
            for alias in aliases:
                staged = zc[alias].stage_file(path)
                media[v][alias] = staged["public_url"]
                log.info("스테이징 %s/%s sha256=%s…", v, alias, str(staged.get("sha256", ""))[:12])
        return {"media_json": media}
    if step == "notify":
        now = datetime.now(config.KST)
        sched = publish_mod.scheduled_for(ctx.gate.publish_date, now)
        tg = ctx.telegram()
        video = (assets_json.get("videos") or {}).get("main")
        if tg is None:
            log.info("[NunaWeekly:pipeline] DRY_RUN·텔레그램 미설정 — 알림 생략 (예정 %s)", sched.isoformat())
        else:
            tg.notify_generated(ctx.episode_id, video, sched, ctx.dry)
        return {"notified_at": now.isoformat(), "scheduled_for": sched.isoformat()}
    raise PipelineError(f"알 수 없는 단계: {step}")


def run_gen(ctx: Ctx, only: str | None = None) -> int:
    log.info("[NunaWeekly:pipeline] v%s (pkg %s) 생성 시작 · %s · dry=%s · 길이 %ds · 시장소스 %s",
             VERSION, PKG_VERSION, ctx.episode_id, ctx.dry, config.duration_sec(), config.market_source())
    ctx.store.create_episode({"episode_id": ctx.episode_id, "publish_date": ctx.gate.publish_date.isoformat(),
                              "us_days": [d.isoformat() for d in ctx.gate.us_days],
                              "duration_target_sec": config.duration_sec(), "pipeline_version": VERSION})
    if not ctx.gate.ok:
        ctx.store.transition(ctx.episode_id, ["PLANNED"], "SKIPPED", {"skip_reason": ctx.gate.skip_reason})
        log.info("[NunaWeekly:pipeline] 미발행 판정: %s", ctx.gate.skip_reason)
        _report(ctx, f"[NUNA] {ctx.episode_id} 미발행: {ctx.gate.skip_reason}")
        return 0
    status = ctx.episode()["status"]
    if status in DONE_GEN or status in ("SKIPPED", "FAILED"):
        log.info("[NunaWeekly:pipeline] 이미 %s — 생성 단계 없음", status)
        return 0
    if status in ("ASSEMBLED", "QC_PASSED"):
        assets = dict(ctx.episode().get("assets_json") or {})
        videos = assets.get("videos") or {}
        if not videos or any(not Path(p).is_file() for p in videos.values()):
            if only:
                raise PipelineError("새 러너에 영상 없음 — step=run 으로 재조립·QC 후 재개 필요")
            assets.pop("videos", None)
            if not ctx.store.transition(ctx.episode_id, [status], "CLIPPED",
                                        {"assets_json": assets, "qc_json": None}):
                raise PipelineError("재조립 상태 전이 경합")
            log.info("새 러너에 영상 없음 — 원격 이미지·클립 복원 후 재조립·QC 재실행")
    for step, before, after in GEN_STEPS:
        if only and step != only:
            continue
        cur = ctx.episode()["status"]
        if cur != before:
            if only:
                raise PipelineError(f"{step} 실행 불가 — 현재 상태 {cur}, 필요 {before}")
            continue
        started = time.time()
        log.info("[NunaWeekly:pipeline] ▶ %s", step)
        try:
            fields = _do_step(ctx, step)
        except Exception as e:
            err = secret_env.redact(f"{step}: {type(e).__name__}: {e}", 500)
            ctx.store.update_episode(ctx.episode_id, {"last_error": err})
            _step_log(ctx, step, "failed", started, err)
            log.error("[NunaWeekly:pipeline] ✖ %s", err)
            _report(ctx, f"[NUNA] {ctx.episode_id} 생성 실패 — {err}\n재실행 시 '{step}' 단계부터 이어서 진행")
            return 1
        fields["last_error"] = None
        if not ctx.store.transition(ctx.episode_id, [before], after, fields):
            raise PipelineError(f"상태 전이 경합 — {before}→{after} 선점됨")
        _step_log(ctx, step, "ok", started)
        log.info("[NunaWeekly:pipeline] ✔ %s → %s (%.1fs)", step, after, time.time() - started)
    log.info("[NunaWeekly:pipeline] 생성 종료 · 상태 %s · 이번 달 누적 ₩%d", ctx.episode()["status"],
             ctx.store.month_spent_krw())
    return 0


# --------------------------------------------------------------------------- #
# 발행
# --------------------------------------------------------------------------- #
def _accounts(ctx: Ctx) -> dict[str, str]:
    if ctx.dry:
        return {p: (secret_env.optional(config.ACCOUNT_ENV[p]) or f"dry-{p}") for p in config.PLATFORMS}
    return publish_mod.accounts_from_env()


def run_publish(ctx: Ctx, step: str = "publish", sleep=time.sleep) -> int:
    log.info("[NunaWeekly:pipeline] v%s 발행 시작 · %s · step=%s · dry=%s", VERSION, ctx.episode_id, step, ctx.dry)
    ep = ctx.store.get_episode(ctx.episode_id)
    if not ep:
        log.error("원장에 회차 없음 — 생성 성공 및 DRY_RUN=false 원장 적재 확인 필요")
        return 1
    status = ep["status"]
    if step == "verify":
        return _verify(ctx, sleep)
    if status in ("PUBLISHED", "HELD", "SKIPPED"):
        log.info("[NunaWeekly:pipeline] 이미 %s — 발행 단계 없음", status)
        return 0
    retry = status in ("SCHEDULED", "PARTIAL", "FAILED") and bool(ep.get("notified_at"))
    if status != "NOTIFIED" and not retry:
        msg = f"[NUNA] {ctx.episode_id} 발행 보류 — 생성 미완료(상태 {status}). 생성 완료 후 발행 워크플로 수동 실행"
        log.warning(msg)
        _report(ctx, msg)
        return 1
    tg = ctx.telegram()
    if tg is not None and not ctx.dry:
        hold = tg.poll_hold(ctx.episode_id)
        if hold:
            if retry:
                raise PipelineError("이미 예약된 회차 보류는 Zernio 예약 취소 후 처리 필요")
            ctx.store.transition(ctx.episode_id, ["NOTIFIED"], "HELD", {"hold_at": hold["at"], "hold_by": hold["by"]})
            _report(ctx, f"[NUNA] {ctx.episode_id} 보류 처리됨 — 발행하지 않음")
            return 0
    notified_at = datetime.fromisoformat(ep["notified_at"]) if ep.get("notified_at") else None
    sched = publish_mod.scheduled_for(ctx.gate.publish_date, notified_at)
    existing = {t["platform"]: t.get("status") for t in ctx.store.list_targets(ctx.episode_id)}
    unsent = [p for p in config.PLATFORMS if existing.get(p) not in publish_mod.DONE_STATES]
    if not ctx.dry and unsent and sched <= datetime.now(config.KST):
        raise PipelineError("예약 시각 경과 — 과거 회차 자동 재발행 차단. 예약·채널 원장 확인 필요")
    media = ep.get("media_json") or {}
    fb_variant = "fb_cut" if "fb_cut" in media else "main"
    results = publish_mod.publish(ctx.episode_id, ctx.store, ctx.zernio(), Script.from_dict(ep["script_json"]),
                                  media, _accounts(ctx), notified_at, ctx.gate.publish_date, fb_variant, ctx.dry)
    summary = {p: r.status for p, r in results.items()}
    log.info("[NunaWeekly:pipeline] 발행 결과: %s", summary)
    if ctx.dry:
        _report(ctx, f"[NUNA] {ctx.episode_id} DRY_RUN 발행 점검 완료: {summary}")
        return 0
    ok = any(s in ("scheduled", "published") for s in summary.values())
    if not ctx.store.transition(ctx.episode_id, [status], "SCHEDULED" if ok else "FAILED",
                         {"scheduled_for": sched.isoformat(),
                          "last_error": None if ok else secret_env.redact(json.dumps(summary, ensure_ascii=False), 500)}):
        raise PipelineError("발행 상태 전이 실패 — 원장 확인 필요")
    _report(ctx, f"[NUNA] {ctx.episode_id} 예약 {sched.strftime('%m/%d %H:%M')} KST · {summary}")
    if not ok:
        return 1
    # 예약 시각 + 5분까지 대기 후 검증 (워크플로 timeout 45분 이내)
    wait = (sched + timedelta(minutes=5) - datetime.now(config.KST)).total_seconds()
    if 0 < wait <= 30 * 60:
        log.info("[NunaWeekly:pipeline] 검증 대기 %.0f초", wait)
        sleep(wait)
    elif wait > 30 * 60:
        log.info("[NunaWeekly:pipeline] 예약까지 30분 초과 — verify 는 수동 dispatch(step=verify)")
        return 0
    return _verify(ctx, sleep)


def _verify(ctx: Ctx, sleep) -> int:
    ep = ctx.episode()
    if ep["status"] not in ("SCHEDULED", "PARTIAL"):
        log.info("[NunaWeekly:pipeline] verify 대상 아님 — 상태 %s", ep["status"])
        return 0
    res = publish_mod.verify(ctx.episode_id, ctx.store, ctx.zernio(), sleep=sleep)
    targets = ctx.store.list_targets(ctx.episode_id)
    states = [t.get("status") for t in targets]
    if states and all(s == "published" for s in states):
        new = "PUBLISHED"
    elif "scheduled" in states:
        new = ep["status"]
    else:
        new = "PARTIAL" if "published" in states else "FAILED"
    if new != ep["status"]:
        ctx.store.transition(ctx.episode_id, [ep["status"]], new)
    urls = {t["platform"]: t.get("platform_post_url") for t in targets if t.get("platform_post_url")}
    _report(ctx, f"[NUNA] {ctx.episode_id} 발행 확인 → {new} · {res} · {urls}")
    return 0 if new in ("PUBLISHED", "SCHEDULED") else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_date(s: str | None) -> date:
    if s:
        return date.fromisoformat(s)
    return datetime.now(config.KST).date()


def main(argv: list[str] | None = None) -> int:
    secret_env.setup_logging()
    ap = argparse.ArgumentParser(prog="python -m gen.nuna_weekly")
    ap.add_argument("command", choices=["gen", "publish"])
    ap.add_argument("--step", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--scheduled", action="store_true")
    a = ap.parse_args(argv)
    if a.scheduled and not config.enabled():
        log.info("[NunaWeekly:pipeline] NUNA_ENABLED != 1 — schedule 실행 종료")
        return 0
    try:
        run_date = _parse_date(a.date)
        allowed = ({"run", "preflight", "poc"} | {s[0] for s in GEN_STEPS}
                   if a.command == "gen" else {"publish", "verify"})
        if a.step and a.step not in allowed:
            raise PipelineError(f"{a.command} 단계 허용값 아님: {a.step}")
        if a.command == "gen" and a.step == "preflight":
            gate = calendar_gate.evaluate(run_date)
            log.info("[NunaWeekly:pipeline] preflight: %s ok=%s days=%s holidays=%s reason=%s",
                     gate.episode_id, gate.ok, gate.us_days, gate.holidays, gate.skip_reason)
            if gate.ok:
                collect.require_closed_sessions(gate, datetime.now(config.KST))
            return 0
        ctx = Ctx(run_date, config.dry_run())
        if a.command == "gen":
            step = a.step or "run"
            if step == "poc":
                raise PipelineError("poc 단계는 PoC 승인 후 별도 구현 예정(설계서 11장)")
            return run_gen(ctx, None if step == "run" else step)
        return run_publish(ctx, a.step or "publish")
    except Exception as e:
        log.error("[NunaWeekly:pipeline] 중단: %s", secret_env.redact(f"{type(e).__name__}: {e}", 500))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
