"""
gen/hero_shorts/pipeline.py — E2E 오케스트레이션 + CLI 진입점
================================================================
단계 (마스터 지시 절차 준수):
    plan     — 원장 정본 로드 → 유형별 컷계획·프롬프트 산출 (0원)
    generate — fal H3 실생성 (현금·승인 후) / --backend dummy (0원, 테스트)
    assemble — 합본 720p
    qc       — QC 4게이트
    publish  — publish_state.json 발행 원장 갱신 (발행 상태는 ASL 과 독립 관리)
    run      — plan→generate→assemble→qc 전체 (생성 백엔드에 따름)

로그 정책 (마스터 지시 "로그파일 자세하게"):
    콘솔 INFO + 파일 DEBUG 이중 기록 — logs/hero_shorts/YYYYMMDD_HHMMSS.log
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from . import VERSION

# ── 경로 상수 ───────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent            # gen/
DATA = BASE / "data"
OUT = DATA / "out"
LOG_DIR = BASE / "logs" / "hero_shorts"
STATE_FILE = DATA / "publish_state.json"


def load_dotenv():
    """gen/.env 로더 — 기존 환경변수를 덮지 않음(setdefault). 키 값을 로그에 절대 출력하지 않는다."""
    envf = BASE / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        logging.getLogger("hero_shorts").info("gen/.env 로드 — 키 등록 확인(값 미출력)")


def setup_logging():
    """이중 로깅 — 콘솔(INFO) + 파일(DEBUG, 타임스탬프 파일명)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    import datetime
    fname = LOG_DIR / f"{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    root = logging.getLogger("hero_shorts")
    root.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    fh = logging.FileHandler(fname, encoding="utf-8")
    fh.setLevel(logging.DEBUG)                    # 파일은 DEBUG 전수 기록
    fh.setFormatter(fmt)
    root.addHandler(ch)
    root.addHandler(fh)
    root.info("hero_shorts v%s 시작 — 로그 파일: %s", VERSION, fname)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    import tempfile
    DATA.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(DATA), delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(STATE_FILE)


def _env(name: str, default=None):
    return os.getenv(name, default)


# 발행 운영 식별값 — 소스 기본값 허용(마스터 승인 v2.5.2). 환경변수/CLI 인자가 우선한다.
DEFAULT_ZERNIO_ACCOUNT_ID = "6aafea9b8d284ffb2120fa6f"
DEFAULT_ALERT_EMAIL = "yumens2@gmail.com"
DEFAULT_FROM_ACCOUNT = "yumens2@gmail.com"


def _resolve_publish_opts(account_id=None, alert_to=None, from_account=None):
    return {
        "account_id": account_id or _env("HERO_ZERNIO_ACCOUNT_ID", DEFAULT_ZERNIO_ACCOUNT_ID),
        "alert_to": alert_to or _env("HERO_ALERT_EMAIL", DEFAULT_ALERT_EMAIL),
        "from_account": from_account or _env("HERO_FROM_ACCOUNT", DEFAULT_FROM_ACCOUNT),
    }


def _load_plan(plan_file):
    return json.loads(Path(plan_file).read_text(encoding="utf-8"))


def _assemble_paths_from_input(plan_arg, ep_number):
    raw = Path(plan_arg).read_text(encoding="utf-8").strip()
    if raw.startswith("{"):
        plan = json.loads(raw)
        return [str(OUT / f"ep{ep_number}_cut{cut['cut_no']}.mp4") for cut in plan.get("cuts", [])]
    return [line.strip() for line in raw.splitlines() if line.strip().endswith(".mp4")]


def _canon_selfcheck(prompts):
    """v2.5.3 — plan 저장 전 캐논 셀프체크(캐논 누락 플랜을 원천 차단)."""
    from . import canon as C
    joined = "\n".join(prompts)
    missing = [t for t in C.PROMPT_TOKENS if t not in joined]
    forbidden_pool = C.FORBIDDEN_GLOBAL + [f for vals in C.FORBIDDEN_PER_CHARACTER.values() for f in vals]
    forbidden = [f for f in forbidden_pool if f in joined]
    if missing or forbidden:
        raise RuntimeError(f"캐논 셀프체크 실패(plan 단계 차단) — 누락토큰={missing} 금지문구={forbidden}")


def cmd_plan(ep_number, out_dir):
    """원장 → 컷계획 JSON 저장 (0원)."""
    from . import ledger, cutplanner
    ep = ledger.load_episode(ep_number)
    plan = cutplanner.plan_episode(ep)
    _canon_selfcheck([c["prompt"] for c in plan])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_file = out_dir / f"ep{ep_number}_plan.json"
    plan_file.write_text(json.dumps(
        {"episode": ep_number, "title": ep.get("title"), "type": ep.get("type"),
         "outcome": ep.get("outcome"), "cuts": plan},
        ensure_ascii=False, indent=2), encoding="utf-8")
    logging.getLogger("hero_shorts").info("컷계획 저장: %s", plan_file)
    return str(plan_file)


def cmd_generate(plan_file, backend, out_dir, only=None):
    """컷계획 → 컷 영상 생성. backend='dummy' 면 0원 전수테스트 경로."""
    from . import generator
    plan = _load_plan(plan_file)
    if backend == "fal" and not only and os.getenv("HERO_ALLOW_ALL", "") != "1":
        raise RuntimeError("연속 생성 금지 규칙(마스터 지시) — fal 전체 생성은 --only 필수, "
                           "전체 허용 시 HERO_ALLOW_ALL=1")
    for c in plan.get("cuts", []):
        miss = [k for k in ("prompt", "duration", "resolution", "aspect_ratio")
                if k not in c.get("request", {})]
        if miss:
            raise ValueError(f"plan 스키마 불완전: cut{c.get('cut_no')} {miss} 누락 — 생성 거부")
    ep = plan["episode"]
    paths = []
    for cut in plan["cuts"]:
        if only and cut["cut_no"] != only:
            continue  # 단건 실행(--only) — 연속 생성 방지 규칙 준수
        out_path = Path(out_dir) / f"ep{ep}_cut{cut['cut_no']}.mp4"
        if out_path.exists():
            logging.getLogger("hero_shorts").info("기존 컷 재사용: %s", out_path)
        else:
            generator.generate(backend, cut["request"], out_path)
        paths.append(str(out_path))
    return paths


def cmd_assemble(paths, out_file):
    from . import assemble_qc
    return assemble_qc.assemble(paths, out_file)


def cmd_voice_plan(plan_file, out_file):
    from . import voiceover
    return voiceover.save_dialogue_plan(plan_file, out_file)


def cmd_tts(dialogue_plan_file, out_dir, backend="dummy", model=None, speaker=None):
    from . import voiceover
    return voiceover.synthesize_tts_clips(dialogue_plan_file, out_dir, backend=backend, model=model, speaker=speaker)


def cmd_mix_audio(final_video, voice_audio, out_file):
    from . import voiceover
    return voiceover.mix_voice_over(final_video, voice_audio, out_file)


def cmd_qc(plan_file, final_video):
    """QC 4게이트 — 프롬프트 집합 + 합본 + 원장 메타 비교."""
    from . import assemble_qc, ledger
    plan = _load_plan(plan_file)
    try:
        ep = ledger.load_episode(plan["episode"])
    except Exception as exc:
        logging.getLogger("hero_shorts").warning(
            "원장 재조회 실패 — plan 메타로 QC 계속 진행: %s", exc
        )
        ep = {"title": plan.get("title"), "type": plan.get("type"), "outcome": plan.get("outcome")}
    prompts = [c["prompt"] for c in plan["cuts"]]
    return assemble_qc.qc_4gates(
        prompts, final_video, ep,
        plan_meta={"title": plan["title"], "type": plan["type"],
                   "outcome": plan["outcome"]})


def cmd_publish(plan_file, final_video, qc_result, channel="instagram", require_audible_audio=False):
    """발행 원장 갱신 — 실제 SNS 업로드 전 QC 통과 상태를 기록."""
    from . import assemble_qc
    qc = qc_result or {}
    if not (qc.get("G1") and qc.get("G2") and qc.get("G3") and qc.get("G4")):
        raise RuntimeError("QC 미통과 — 발행 원장 기록 거부(G1~G4 전부 통과 후만 허용)")
    audio_info = None
    if require_audible_audio:
        audio_info = assemble_qc.detect_audio_presence(final_video)
        if not audio_info.get("audible"):
            raise RuntimeError("가청 오디오 미검출 — 발행 원장 기록 거부")
    state = load_state()
    plan = _load_plan(plan_file)
    key = f"ep{plan['episode']}"
    state[key] = {
        "title": plan["title"],
        "type": plan["type"],
        "final": final_video,
        "qc": qc_result,
        "channel": channel,
        "status": "QC_PASSED_READY",
        "version": VERSION,
        "require_audible_audio": require_audible_audio,
        "audio_check": audio_info,
    }
    save_state(state)
    logging.getLogger("hero_shorts").info("발행 원장 갱신: %s", key)
    return state[key]


def cmd_zernio_publish(plan_file, final_video, caption_file, media_url,
                       qc_result=None, schedule_at: Optional[str] = None,
                       account_id: Optional[str] = None,
                       platform: str = "instagram",
                       channel: str = "instagram",
                       max_wait_sec: int = 900,
                       poll_interval_sec: int = 30,
                       alert_to: Optional[str] = None,
                       from_account: Optional[str] = None,
                       ai_generated: bool = True,
                       require_audible_audio: bool = False,
                       allow_duplicate_post: bool = False,
                       require_media_reachable: bool = True):
    """QC/G5 통과 산출물로 Zernio 발행 후 공개 링크까지 회수한다."""
    from . import assemble_qc, publish_runtime
    qc = qc_result or {}
    if not (qc.get("G1") and qc.get("G2") and qc.get("G3") and qc.get("G4")):
        raise RuntimeError("QC 미통과 — Zernio 발행 거부(G1~G4 전부 통과 후만 허용)")
    resolved = _resolve_publish_opts(account_id=account_id, alert_to=alert_to, from_account=from_account)
    if not resolved["account_id"]:
        raise RuntimeError("Zernio 계정 ID 누락 — 소스 기본값 손상 또는 빈 값 주입. 환경변수 또는 --account-id로 주입 필요")
    caption = Path(caption_file).read_text(encoding="utf-8").strip()
    effective_schedule = schedule_at or datetime_now_iso_local()
    assemble_qc.validate_publish_assets(
        final_video,
        caption,
        media_url,
        effective_schedule,
        require_audible_audio=require_audible_audio,
    )
    state = load_state()
    plan = _load_plan(plan_file)
    key = f"ep{plan['episode']}"
    existing = state.get(key) or {}
    if not allow_duplicate_post and existing.get("zernio_post_id") and existing.get("status") in {"published", "pending", "timeout"}:
        raise RuntimeError(
            f"중복 발행 방지 — {key} 기존 post_id={existing.get('zernio_post_id')} status={existing.get('status')}"
        )
    if require_media_reachable:
        # v2.5.3 G5 보강 — 형식 검사를 넘어 발행 직전 실제 접근 가능 여부 확인
        assemble_qc.check_media_url_reachable(media_url)
    result = publish_runtime.create_and_monitor_post(
        text=caption,
        media_url=media_url,
        account_id=resolved["account_id"],
        schedule_at=schedule_at,
        platform=platform,
        ai_generated=ai_generated,
        max_wait_sec=max_wait_sec,
        poll_interval_sec=poll_interval_sec,
        alert_on_timeout=True,
        alert_on_error=True,
        from_account=resolved["from_account"],
        alert_to=resolved["alert_to"],
    )
    state[key] = {
        "title": plan["title"],
        "type": plan["type"],
        "final": final_video,
        "channel": channel,
        "platform": platform,
        "qc": qc,
        "status": result["outcome"],
        "zernio_post_id": result.get("post_id"),
        "platform_post_url": result.get("post_url"),
        "schedule_at": schedule_at,
        "version": VERSION,
        "require_audible_audio": require_audible_audio,
        "published_at": ((result.get("snapshot") or {}).get("platforms") or [{}])[0].get("publishedAt") if result.get("snapshot") else None,
    }
    save_state(state)
    logging.getLogger("hero_shorts").info("제르니오 발행/모니터 완료: %s outcome=%s url=%s", key, result.get("outcome"), result.get("post_url"))
    return result


def datetime_now_iso_local():
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def main(argv=None):
    """CLI 진입점 — python3 -m gen.hero_shorts.pipeline <단계> [옵션]."""
    setup_logging()
    load_dotenv()
    ap = argparse.ArgumentParser(description="hero shorts pipeline v" + VERSION)
    ap.add_argument("step", choices=["plan", "generate", "assemble", "voice_plan", "tts", "mix_audio", "qc", "publish", "zernio_publish", "run", "test"])
    ap.add_argument("--ep", type=int, default=86)
    ap.add_argument("--backend", choices=["fal", "dummy"], default="dummy",
                    help="fal=현금(승인 후), dummy=테스트 0원")
    ap.add_argument("--plan", default=None, help="plan 단계 산출 JSON 경로")
    ap.add_argument("--voice-plan", default=None, help="voice_plan 산출 JSON 또는 tts manifest 경로")
    ap.add_argument("--tts-backend", choices=["dummy", "gsk"], default="dummy", help="dummy=0원 테스트, gsk=실TTS")
    ap.add_argument("--tts-model", default=None, help="gsk TTS 모델 ID")
    ap.add_argument("--speaker", default=None, help="gsk TTS 화자")
    ap.add_argument("--final-video", default=None, help="QC/믹싱/발행 대상 최종 영상 경로")
    ap.add_argument("--only", type=int, default=None, help="지정 컷 번호만 생성 (단건 — 연속 생성 방지)")
    ap.add_argument("--caption-file", default=None, help="발행용 캡션 txt 경로")
    ap.add_argument("--media-url", default=None, help="제르니오가 내려받을 Genspark 파일 URL")
    ap.add_argument("--schedule-at", default=None, help="예약 발행 ISO8601 시각")
    ap.add_argument("--account-id", default=None, help="제르니오 계정 ID(환경변수 주입 권장)")
    ap.add_argument("--platform", default="instagram", help="게시 플랫폼")
    ap.add_argument("--alert-to", default=None, help="지연/실패 알림 수신 메일(환경변수 주입 권장)")
    ap.add_argument("--from-account", default=None, help="지연/실패 알림 발신 Gmail 계정(환경변수 주입 권장)")
    ap.add_argument("--max-wait-sec", type=int, default=900, help="게시 완료 대기 최대 초")
    ap.add_argument("--poll-interval-sec", type=int, default=30, help="게시 상태 조회 간격 초")
    ap.add_argument("--require-audible-audio", action="store_true", help="무음/대사 누락이면 발행 차단")
    ap.add_argument("--allow-duplicate-post", action="store_true", help="기존 post_id가 있어도 새 발행을 허용")
    args = ap.parse_args(argv)
    log = logging.getLogger("hero_shorts")

    if args.step == "test":
        import unittest
        from . import tests
        suite = unittest.defaultTestLoader.discover(tests.__path__[0])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)

    if args.step == "plan":
        print(cmd_plan(args.ep, OUT))
    elif args.step == "generate":
        print("\n".join(cmd_generate(args.plan, args.backend, OUT, only=args.only)))
    elif args.step == "assemble":
        paths = _assemble_paths_from_input(args.plan, args.ep)
        print(cmd_assemble(paths, OUT / f"ep{args.ep}_final.mp4"))
    elif args.step == "voice_plan":
        if not args.plan:
            raise RuntimeError("voice_plan 단계는 --plan 필수")
        print(cmd_voice_plan(args.plan, OUT / f"ep{args.ep}_voice_plan.json"))
    elif args.step == "tts":
        if not args.voice_plan:
            raise RuntimeError("tts 단계는 --voice-plan 필수")
        print(json.dumps(cmd_tts(
            args.voice_plan,
            OUT,
            backend=args.tts_backend,
            model=args.tts_model,
            speaker=args.speaker,
        ), ensure_ascii=False, indent=2))
    elif args.step == "mix_audio":
        if not args.voice_plan:
            raise RuntimeError("mix_audio 단계는 --voice-plan 필수")
        manifest = json.loads(Path(args.voice_plan).read_text(encoding="utf-8"))
        voice_audio = manifest.get("merged_audio")
        if not voice_audio:
            raise RuntimeError("voice manifest에 merged_audio 없음")
        final_video = args.final_video or str(OUT / f"ep{args.ep}_final.mp4")
        print(cmd_mix_audio(final_video, voice_audio, OUT / f"ep{args.ep}_final_voiced.mp4"))
    elif args.step == "qc":
        final_video = args.final_video or str(OUT / f"ep{args.ep}_final.mp4")
        print(cmd_qc(args.plan, final_video))
    elif args.step == "publish":
        plan_file = args.plan
        final_video = args.final_video or str(OUT / f"ep{args.ep}_final_voiced.mp4")
        qc_path = OUT / f"ep{args.ep}_qc.json"
        qc_result = json.loads(qc_path.read_text()) if qc_path.exists() else cmd_qc(plan_file, final_video)
        if not qc_path.exists():
            qc_path.write_text(json.dumps(qc_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(cmd_publish(plan_file, final_video, qc_result, require_audible_audio=args.require_audible_audio))
    elif args.step == "zernio_publish":
        if not args.caption_file or not args.media_url:
            raise RuntimeError("zernio_publish 단계는 --caption-file 과 --media-url 필수")
        plan_file = args.plan
        final_video = args.final_video or str(OUT / f"ep{args.ep}_final_voiced.mp4")
        qc_path = OUT / f"ep{args.ep}_qc.json"
        qc_result = json.loads(qc_path.read_text()) if qc_path.exists() else cmd_qc(plan_file, final_video)
        if not qc_path.exists():
            qc_path.write_text(json.dumps(qc_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(cmd_zernio_publish(
            plan_file=plan_file,
            final_video=final_video,
            caption_file=args.caption_file,
            media_url=args.media_url,
            qc_result=qc_result,
            schedule_at=args.schedule_at,
            account_id=args.account_id,
            platform=args.platform,
            max_wait_sec=args.max_wait_sec,
            poll_interval_sec=args.poll_interval_sec,
            alert_to=args.alert_to,
            from_account=args.from_account,
            require_audible_audio=args.require_audible_audio,
            allow_duplicate_post=args.allow_duplicate_post,
        ), ensure_ascii=False, indent=2))
    elif args.step == "run":
        # E2E — plan→generate→assemble→voice_plan→tts→mix_audio→qc (publish 는 승인 후 별도)
        plan_file = cmd_plan(args.ep, OUT)
        paths = cmd_generate(plan_file, args.backend, OUT, only=args.only)
        final = cmd_assemble(paths, OUT / f"ep{args.ep}_final.mp4")
        voice_plan = cmd_voice_plan(plan_file, OUT / f"ep{args.ep}_voice_plan.json")
        tts_manifest = cmd_tts(voice_plan, OUT, backend=args.tts_backend, model=args.tts_model, speaker=args.speaker)
        voiced_final = cmd_mix_audio(final, tts_manifest["merged_audio"], OUT / f"ep{args.ep}_final_voiced.mp4")
        qc = cmd_qc(plan_file, voiced_final)
        (OUT / f"ep{args.ep}_qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd_publish(plan_file, voiced_final, qc, require_audible_audio=True)
        log.info("E2E 완료 (backend=%s tts=%s): %s", args.backend, args.tts_backend, voiced_final)


if __name__ == "__main__":
    main()
