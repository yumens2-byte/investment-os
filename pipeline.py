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
DEFAULT_ZERNIO_ACCOUNT_ID = os.getenv("HERO_ZERNIO_ACCOUNT_ID")
DEFAULT_ALERT_EMAIL = os.getenv("HERO_ALERT_EMAIL")
DEFAULT_FROM_ACCOUNT = os.getenv("HERO_FROM_ACCOUNT")


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
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def cmd_plan(ep_number, out_dir):
    """원장 → 컷계획 JSON 저장 (0원)."""
    from . import ledger, cutplanner
    ep = ledger.load_episode(ep_number)
    plan = cutplanner.plan_episode(ep)
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
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
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


def cmd_qc(plan_file, final_video):
    """QC 4게이트 — 프롬프트 집합 + 합본 + 원장 메타 비교."""
    from . import assemble_qc, ledger
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    ep = ledger.load_episode(plan["episode"])
    prompts = [c["prompt"] for c in plan["cuts"]]
    return assemble_qc.qc_4gates(
        prompts, final_video, ep,
        plan_meta={"title": plan["title"], "type": plan["type"],
                   "outcome": plan["outcome"]})


def cmd_publish(plan_file, final_video, qc_result, channel="instagram"):
    """발행 원장 갱신 — 실제 SNS 업로드 전 QC 통과 상태를 기록."""
    qc = qc_result or {}
    if not (qc.get("G1") and qc.get("G2") and qc.get("G3") and qc.get("G4")):
        raise RuntimeError("QC 미통과 — 발행 원장 기록 거부(G1~G4 전부 통과 후만 허용)")
    state = load_state()
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    key = f"ep{plan['episode']}"
    state[key] = {"title": plan["title"], "type": plan["type"],
                  "final": final_video, "qc": qc_result, "channel": channel,
                  "status": "QC_PASSED_READY", "version": VERSION}
    save_state(state)
    logging.getLogger("hero_shorts").info("발행 원장 갱신: %s", key)
    return state[key]


def cmd_zernio_publish(plan_file, final_video, caption_file, media_url,
                       qc_result=None, schedule_at: Optional[str] = None,
                       account_id: Optional[str] = DEFAULT_ZERNIO_ACCOUNT_ID,
                       platform: str = "instagram",
                       channel: str = "instagram",
                       max_wait_sec: int = 900,
                       poll_interval_sec: int = 30,
                       alert_to: Optional[str] = DEFAULT_ALERT_EMAIL,
                       from_account: Optional[str] = DEFAULT_FROM_ACCOUNT,
                       ai_generated: bool = True,
                       require_audible_audio: bool = False):
    """QC/G5 통과 산출물로 Zernio 발행 후 공개 링크까지 회수한다."""
    from . import assemble_qc, publish_runtime
    qc = qc_result or {}
    if not (qc.get("G1") and qc.get("G2") and qc.get("G3") and qc.get("G4")):
        raise RuntimeError("QC 미통과 — Zernio 발행 거부(G1~G4 전부 통과 후만 허용)")
    if not account_id:
        raise RuntimeError("HERO_ZERNIO_ACCOUNT_ID 누락 — 소스 하드코딩 없이 환경변수 또는 --account-id로 주입 필요")
    caption = Path(caption_file).read_text(encoding="utf-8").strip()
    effective_schedule = schedule_at or datetime_now_iso_local()
    assemble_qc.validate_publish_assets(
        final_video,
        caption,
        media_url,
        effective_schedule,
        require_audible_audio=require_audible_audio,
    )
    result = publish_runtime.create_and_monitor_post(
        text=caption,
        media_url=media_url,
        account_id=account_id,
        schedule_at=schedule_at,
        platform=platform,
        ai_generated=ai_generated,
        max_wait_sec=max_wait_sec,
        poll_interval_sec=poll_interval_sec,
        alert_on_timeout=True,
        alert_on_error=True,
        from_account=from_account,
        alert_to=alert_to,
    )
    state = load_state()
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    key = f"ep{plan['episode']}"
    state[key] = {
        "title": plan["title"],
        "type": plan["type"],
        "final": final_video,
        "caption_file": str(caption_file),
        "media_url": media_url,
        "channel": channel,
        "platform": platform,
        "qc": qc,
        "status": result["outcome"],
        "zernio_post_id": result.get("post_id"),
        "platform_post_url": result.get("post_url"),
        "schedule_at": schedule_at,
        "version": VERSION,
        "require_audible_audio": require_audible_audio,
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
    ap.add_argument("step", choices=["plan", "generate", "assemble", "qc", "publish", "zernio_publish", "run", "test"])
    ap.add_argument("--ep", type=int, default=86)
    ap.add_argument("--backend", choices=["fal", "dummy"], default="dummy",
                    help="fal=현금(승인 후), dummy=테스트 0원")
    ap.add_argument("--plan", default=None, help="plan 단계 산출 JSON 경로")
    ap.add_argument("--only", type=int, default=None, help="지정 컷 번호만 생성 (단건 — 연속 생성 방지)")
    ap.add_argument("--caption-file", default=None, help="발행용 캡션 txt 경로")
    ap.add_argument("--media-url", default=None, help="제르니오가 내려받을 Genspark 파일 URL")
    ap.add_argument("--schedule-at", default=None, help="예약 발행 ISO8601 시각")
    ap.add_argument("--account-id", default=DEFAULT_ZERNIO_ACCOUNT_ID, help="제르니오 계정 ID(환경변수 주입 권장)")
    ap.add_argument("--platform", default="instagram", help="게시 플랫폼")
    ap.add_argument("--alert-to", default=DEFAULT_ALERT_EMAIL, help="지연/실패 알림 수신 메일(환경변수 주입 권장)")
    ap.add_argument("--from-account", default=DEFAULT_FROM_ACCOUNT, help="지연/실패 알림 발신 Gmail 계정(환경변수 주입 권장)")
    ap.add_argument("--max-wait-sec", type=int, default=900, help="게시 완료 대기 최대 초")
    ap.add_argument("--poll-interval-sec", type=int, default=30, help="게시 상태 조회 간격 초")
    ap.add_argument("--require-audible-audio", action="store_true", help="무음/대사 누락이면 발행 차단")
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
        paths = [l for l in Path(args.plan).read_text().splitlines() if l.endswith(".mp4")]
        print(cmd_assemble(paths, OUT / f"ep{args.ep}_final.mp4"))
    elif args.step == "qc":
        print(cmd_qc(args.plan, OUT / f"ep{args.ep}_final.mp4"))
    elif args.step == "publish":
        plan_file = args.plan
        qc_result = json.loads(Path(OUT / f"ep{args.ep}_qc.json").read_text())
        print(cmd_publish(plan_file, str(OUT / f"ep{args.ep}_final.mp4"), qc_result))
    elif args.step == "zernio_publish":
        if not args.caption_file or not args.media_url:
            raise RuntimeError("zernio_publish 단계는 --caption-file 과 --media-url 필수")
        plan_file = args.plan
        qc_result = json.loads(Path(OUT / f"ep{args.ep}_qc.json").read_text())
        final_video = str(OUT / f"ep{args.ep}_final.mp4")
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
        ), ensure_ascii=False, indent=2))
    elif args.step == "run":
        # E2E — plan→generate→assemble→qc (publish 는 승인 후 별도)
        plan_file = cmd_plan(args.ep, OUT)
        paths = cmd_generate(plan_file, args.backend, OUT, only=args.only)
        final = cmd_assemble(paths, OUT / f"ep{args.ep}_final.mp4")
        qc = cmd_qc(plan_file, final)
        (OUT / f"ep{args.ep}_qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2))
        cmd_publish(plan_file, final, qc)
        log.info("E2E 완료 (backend=%s): %s", args.backend, final)


if __name__ == "__main__":
    main()
