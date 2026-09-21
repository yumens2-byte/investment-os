"""
gen/hero_shorts/assemble_qc.py — 합본(720p 트랜스코드) + QC 4게이트
=====================================================================
합본:
    컷 MPF 목록 → ffmpeg concat → 720x1280(9:16)·30fps·AAC 정규화.
    fal H3 는 768P 네이티브로 생성하므로(2K/4K는 업스케일 아님),
    최종 720p 트랜스코드로 플랫폼 사양을 맞춘다.

QC 4게이트 (하나라도 실패하면 발행 차단):
    G1 표현 가드  — 프롬프트 집합에 투자 권유·확정 전망 0건
    G2 캐논      — 모든 프롬프트에 필수 토큰 존재 + 금지 문구 0건
    G3 기술      — ffprobe: 720x1280·9:16·길이 ±1s·오디오 스트림 존재
    G4 원장 정합 — 계획 메타가 원장 title/outcome/type 과 일치
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("hero_shorts.assemble_qc")


def assemble(cut_paths, out_path, target_h=1280):
    """컷 파일들을 이어붙여 최종 720p(720x1280) 세로 영상으로 출력.

    Args:
        cut_paths: 컷 MP4 경로 리스트 (순서 = 재생 순서).
        out_path:  출력 경로 (예: data/out/ep86_final.mp4).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lst = out_path.with_suffix(".txt")
    # concat 데모어 형식 — 파일명에 따옴표·특수문자 없는 내부 경로만 사용
    lst.write_text("\n".join(f"file '{p}'" for p in cut_paths), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,fps=30",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error("합본 실패: %s", r.stderr[:300])
        raise RuntimeError("ffmpeg 합본 실패")
    log.info("합본 완료: %s (%d컷 → 720x1280)", out_path, len(cut_paths))
    return str(out_path)


def ffprobe_check(path, min_sec=29, max_sec=61):
    """G3 기술검증 — 해상도·비율·길이·오디오.

    Returns:
        (bool 통과, dict 실측값) — 통과 기준: 720x1280, 길이 29~61s, 오디오 1개.
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(r.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    w, h = int(v["width"]), int(v["height"])
    dur = float(info["format"]["duration"])
    ok = (w == 720 and h == 1280 and audio and min_sec <= dur <= max_sec)
    log.info("G3 실측: %dx%d, %.1fs, 오디오 %d개 → %s", w, h, dur, len(audio),
             "통과" if ok else "실패")
    return ok, {"w": w, "h": h, "duration": dur, "audio": len(audio)}


def qc_4gates(prompts, final_video, ep_state, plan_meta=None):
    """QC 4게이트 실행 — 실패 시 RuntimeError 로 발행 차단.

    Args:
        prompts:    사용한 컷 프롬프트 전체 리스트.
        final_video: 합본 MP4 경로.
        ep_state:   원장 정본 dict — G4 비교 기준.
        plan_meta:  컷계획 메타(dict) — 유형·컷수 등.
    """
    from . import canon as C
    joined = "\n".join(prompts)

    # G1 — 표현 가드
    g1_words = ["buy", "sell", "투자 권유", "확정 전망", "guaranteed", "무조건", "확실"]
    g1_ok = not any(w in joined for w in g1_words)
    log.info("G1 표현 가드: %s", "통과" if g1_ok else "실패 — 권유성 문구 발견")

    # G2 — 캐논 (필수 토큰 존재 + 금지 문구 부재)
    missing = [t for t in C.PROMPT_TOKENS if t not in joined]
    forbidden = [f for f in C.FORBIDDEN_PHRASES if f in joined]
    g2_ok = not missing and not forbidden
    log.info("G2 캐논: %s (누락토큰=%s 금지문구=%s)", "통과" if g2_ok else "실패", missing, forbidden)

    # G3 — 기술검증
    g3_ok, measured = ffprobe_check(final_video)

    # G4 — 원장 정합 (제목·결과·유형이 계획 메타와 일치)
    meta = plan_meta or {}
    g4_ok = (meta.get("title") == ep_state.get("title")
             and meta.get("type") == ep_state.get("type")
             and meta.get("outcome") == ep_state.get("outcome"))
    log.info("G4 원장 정합: %s (title=%s type=%s)", "통과" if g4_ok else "실패",
             ep_state.get("title"), ep_state.get("type"))

    if not all([g1_ok, g2_ok, g3_ok, g4_ok]):
        raise RuntimeError(f"QC 게이트 실패 — G1:{g1_ok} G2:{g2_ok} G3:{g3_ok} G4:{g4_ok}")
    log.info("QC 4게이트 전부 통과 — 발행 가능")
    return {"G1": g1_ok, "G2": g2_ok, "G3": measured, "G4": g4_ok}


# ── G5 발행 사전검증 (v2.4.4 — 제르니오 예약 직전, 비용 0) ──────────
IG_CAPTION_LIMIT = 2200
DISCLAIMER_MARKS = ["면책", "투자 참고", "not investment advice"]


def validate_publish_assets(final_video, caption, media_url, schedule_at):
    """G5 — 잘못된 발행(캡션 초과·면책 누락·링크 만료·무결성)을 예약 전에 차단."""
    import datetime
    problems = []
    if len(caption) > IG_CAPTION_LIMIT:
        problems.append(f"캡션 {len(caption)}자 > {IG_CAPTION_LIMIT} 제한")
    if not any(m in caption for m in DISCLAIMER_MARKS):
        problems.append("면책 문구 미포함 — 발행 금지(JCU 규칙)")
    if not media_url.startswith("https://"):
        problems.append("미디어 URL https 아님 — 제르니오 다운로드 실패 위험")
    days = (datetime.date.fromisoformat(schedule_at[:10]) - datetime.date.today()).days
    if days > 6:
        problems.append(f"예약 {days}일 뒤 — 젠스파크 파일 URL 6일 한도 초과(발행 시 만료)")
    ok, m = ffprobe_check(final_video)
    if not ok:
        problems.append(f"영상 기술검증 실패 {m}")
    log.info("G5 발행 사전검증: %s", "통과" if not problems else f"실패 {problems}")
    if problems:
        raise RuntimeError("발행 사전검증 실패: " + "; ".join(problems))
    return True
