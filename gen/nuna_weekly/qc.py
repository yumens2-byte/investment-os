"""
gen/nuna_weekly/qc.py — 발행 전 품질 게이트 Q1~Q8 (설계서 9장, 보안 S-02)
===========================================================================
    run(path, variant, target_sec, script, facts, publish_date, verify_numbers=None) -> QCReport

    Q1 규격     1080x1920 · h264 · yuv420p · ~30fps · AAC 오디오 ≥1
    Q2 길이     fb_cut: 3≤d≤60 / 그 외: |d-target|≤1.0 그리고 3≤d≤90
    Q3 가청     volumedetect max_volume > -60dB 그리고 mean_volume > -45dB
    Q4 수치     verify_numbers(script, facts, publish_date) — 미주입 시 통과("검증기 미주입")
    Q5 정책     제목·캡션·내레이션·카드 문구에 금지표현·링크/멘션 없음
    Q6 면책     모든 캡션과 S8 내레이션에 config.DISCLAIMER 포함
    Q7 길이제한 캡션 ≤ CAPTION_LIMITS[platform], 제목 ≤ YOUTUBE_TITLE_LIMIT
    Q8 용량     파일 ≤ IG_MAX_BYTES (telegram_ok 는 참고값)

결과는 <path.parent>/qc/qc_<variant>.json 에 실측값·게이트 결과만 기록한다.
이 파일은 유일한 공개 산출물이므로(S-02) URL·비밀값·문구 원문(링크 토큰)을 넣지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from . import policy
from .config import (
    CAPTION_LIMITS,
    IG_MAX_BYTES,
    TELEGRAM_VIDEO_MAX_BYTES,
    VIDEO_FPS,
    VIDEO_H,
    VIDEO_W,
    YOUTUBE_TITLE_LIMIT,
)
from .models import Facts, QCReport, Script
from .secret_env import redact

VERSION = "1.1.0"
log = logging.getLogger(__name__)

DISCLAIMER_SEG = "S8"
FPS_TOL = 0.5
DUR_TOL_SEC = 1.0
DUR_MIN_SEC = 3.0
DUR_MAX_SEC = 90.0
FB_CUT_MAX_SEC = 60.0
MAX_VOLUME_MIN_DB = -60.0
MEAN_VOLUME_MIN_DB = -45.0
DETAIL_LIMIT = 300

VerifyNumbers = Callable[[Script, Facts, date], Any]


class QCError(RuntimeError):
    """QC 실행 자체 불가(파일 없음 등)."""


def _gate(ok: bool, detail: Any) -> dict[str, Any]:
    if isinstance(detail, str):
        detail = redact(detail, DETAIL_LIMIT)
    return {"ok": bool(ok), "detail": detail}


def _parse_rate(rate: str | None) -> float | None:
    if not rate:
        return None
    num, _, den = rate.partition("/")
    try:
        n, d = float(num), float(den or 1)
        return n / d if d else None
    except ValueError:
        return None


def probe(path: Path) -> dict[str, Any]:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                        "-show_format", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise QCError(f"ffprobe 실패 rc={r.returncode}: {(r.stderr or '')[-300:]}")
    return json.loads(r.stdout or "{}")


def volumedetect(path: Path) -> tuple[float | None, float | None]:
    """(mean_volume_db, max_volume_db). 오디오 없음·실패 시 None."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0?",
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    err = r.stderr or ""
    mean_m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-?inf)\s*dB", err)
    max_m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?|-?inf)\s*dB", err)
    return (float(mean_m.group(1)) if mean_m else None, float(max_m.group(1)) if max_m else None)


_LUFS_RE = re.compile(r"I:\s+(-?[0-9.]+) LUFS")


def integrated_lufs(path: Path) -> float | None:
    """ebur128 통합 음량(LUFS). 측정 실패 시 None."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128",
                        "-f", "null", "-"], capture_output=True, text=True)
    hits = _LUFS_RE.findall(r.stderr or "")
    return float(hits[-1]) if r.returncode == 0 and hits else None


def _texts(script: Script) -> list[tuple[str, str]]:
    """(위치, 문구) — Q5 검사 대상."""
    out: list[tuple[str, str]] = [("title", script.title or "")]
    out += [(f"caption.{k}", v or "") for k, v in script.captions.items()]
    for s in script.segments:
        out.append((f"{s.seg_id}.narration", s.narration or ""))
        out.append((f"{s.seg_id}.card_title", s.card_title or ""))
        out += [(f"{s.seg_id}.card_lines[{i}]", ln or "") for i, ln in enumerate(s.card_lines)]
    return out


def _q4(verify_numbers: VerifyNumbers | None, script: Script, facts: Facts, publish_date: date) -> dict[str, Any]:
    if verify_numbers is None:
        return _gate(True, "검증기 미주입")
    try:
        res = verify_numbers(script, facts, publish_date)
    except Exception as e:  # 검증기 오류는 fail-closed
        return _gate(False, f"검증기 오류: {type(e).__name__}: {e}")
    # 반환 계약: bool | (ok, detail) | {"ok":..} | 근거 없는 숫자 목록(비면 통과)
    if isinstance(res, bool):
        return _gate(res, "검증기 판정")
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], bool):
        return _gate(res[0], res[1] if isinstance(res[1], str | int | float | list | dict) else str(res[1]))
    if isinstance(res, dict) and "ok" in res:
        return _gate(bool(res["ok"]), res.get("detail", "검증기 판정"))
    if isinstance(res, list | tuple | set):
        bad = [redact(str(x), 60) for x in res]
        return _gate(not bad, {"unsupported": bad[:20], "count": len(bad)})
    return _gate(False, f"검증기 반환 형식 미지원: {type(res).__name__}")


def run(path: str | Path, variant: str, target_sec: float, script: Script, facts: Facts,
        publish_date: date, verify_numbers: VerifyNumbers | None = None) -> QCReport:
    log.info("[NunaWeekly:qc] v%s 시작 variant=%s", VERSION, variant)
    path = Path(path)
    if not path.is_file():
        raise QCError(f"영상 파일 없음: {path.name}")

    gates: dict[str, dict[str, Any]] = {}
    measured: dict[str, Any] = {"variant": variant, "target_sec": target_sec,
                                "publish_date": str(publish_date), "file": path.name}

    # Q1 규격
    info = probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    aac = [s for s in auds if s.get("codec_name") == "aac"]
    fps = _parse_rate((v or {}).get("avg_frame_rate")) or _parse_rate((v or {}).get("r_frame_rate"))
    measured.update({
        "width": (v or {}).get("width"), "height": (v or {}).get("height"),
        "vcodec": (v or {}).get("codec_name"), "pix_fmt": (v or {}).get("pix_fmt"),
        "fps": round(fps, 3) if fps else None, "audio_streams": len(auds), "aac_streams": len(aac),
        "acodec": auds[0].get("codec_name") if auds else None,
        "sample_rate": int(auds[0]["sample_rate"]) if auds and auds[0].get("sample_rate") else None,
    })
    q1_fail = []
    if v is None:
        q1_fail.append("영상 스트림 없음")
    else:
        if (v.get("width"), v.get("height")) != (VIDEO_W, VIDEO_H):
            q1_fail.append(f"해상도 {v.get('width')}x{v.get('height')}")
        if v.get("codec_name") != "h264":
            q1_fail.append(f"코덱 {v.get('codec_name')}")
        if v.get("pix_fmt") != "yuv420p":
            q1_fail.append(f"픽셀포맷 {v.get('pix_fmt')}")
        if fps is None or abs(fps - VIDEO_FPS) > FPS_TOL:
            q1_fail.append(f"fps {fps}")
    if not aac:
        q1_fail.append("AAC 오디오 없음")
    gates["Q1"] = _gate(not q1_fail, "; ".join(q1_fail) or "규격 일치")

    # Q2 길이
    try:
        dur = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    measured["duration"] = round(dur, 3) if dur is not None else None
    if dur is None:
        gates["Q2"] = _gate(False, "길이 판독 불가")
    elif variant == "fb_cut":
        gates["Q2"] = _gate(DUR_MIN_SEC <= dur <= FB_CUT_MAX_SEC, f"{dur:.3f}s (fb_cut {DUR_MIN_SEC}~{FB_CUT_MAX_SEC}s)")
    else:
        ok = abs(dur - target_sec) <= DUR_TOL_SEC and DUR_MIN_SEC <= dur <= DUR_MAX_SEC
        gates["Q2"] = _gate(ok, f"{dur:.3f}s (목표 {target_sec}±{DUR_TOL_SEC}s, {DUR_MIN_SEC}~{DUR_MAX_SEC}s)")

    # Q3 가청
    mean_db, max_db = volumedetect(path) if auds else (None, None)
    measured.update({"mean_volume_db": mean_db, "max_volume_db": max_db})
    q3_ok = (max_db is not None and mean_db is not None
             and max_db > MAX_VOLUME_MIN_DB and mean_db > MEAN_VOLUME_MIN_DB)
    gates["Q3"] = _gate(q3_ok, f"max={max_db}dB(>{MAX_VOLUME_MIN_DB}) mean={mean_db}dB(>{MEAN_VOLUME_MIN_DB})")

    # 통합 음량(LUFS) — 기록만, 게이트 아님 (DN2026_0086, 목표 -14 LUFS)
    measured["integrated_lufs"] = integrated_lufs(path) if auds else None

    # Q4 수치
    gates["Q4"] = _q4(verify_numbers, script, facts, publish_date)

    # Q5 금지표현·링크 — 링크 토큰 원문은 기록하지 않는다(S-02)
    forbidden: list[dict[str, str]] = []
    links: list[str] = []
    for where, text in _texts(script):
        forbidden += [{"where": where, "phrase": f} for f in policy.find_forbidden(text)]
        if policy.find_links(text):
            links.append(where)
    gates["Q5"] = _gate(not forbidden and not links,
                        {"forbidden": forbidden[:20], "link_locations": links[:20]})

    # Q6 면책
    missing = [f"caption.{k}" for k, cap in script.captions.items() if not policy.has_disclaimer(cap)]
    if not script.captions:
        missing.append("captions(없음)")
    s8 = next((s for s in script.segments if s.seg_id == DISCLAIMER_SEG), None)
    if s8 is None or not policy.has_disclaimer(s8.narration):
        missing.append(f"{DISCLAIMER_SEG}.narration")
    gates["Q6"] = _gate(not missing, {"missing": missing})

    # Q7 길이 제한
    over: list[str] = []
    cap_lens = {k: len(cap or "") for k, cap in script.captions.items()}
    for k, n in cap_lens.items():
        lim = CAPTION_LIMITS.get(k)
        if lim is None:
            over.append(f"caption.{k}: 한도 미정의")
        elif n > lim:
            over.append(f"caption.{k}: {n}>{lim}")
    title_len = len(script.title or "")
    if title_len > YOUTUBE_TITLE_LIMIT:
        over.append(f"title: {title_len}>{YOUTUBE_TITLE_LIMIT}")
    measured.update({"caption_lengths": cap_lens, "title_length": title_len})
    gates["Q7"] = _gate(not over, {"over": over})

    # Q8 용량
    size = path.stat().st_size
    measured.update({"size_bytes": size, "telegram_ok": size <= TELEGRAM_VIDEO_MAX_BYTES})
    gates["Q8"] = _gate(size <= IG_MAX_BYTES, f"{size}B (≤{IG_MAX_BYTES}B)")

    passed = all(g["ok"] for g in gates.values())
    report = QCReport(variant=variant, passed=passed, gates=gates, measured=measured)

    qc_dir = path.parent / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    out_json = qc_dir / f"qc_{variant}.json"
    out_json.write_text(json.dumps({"variant": variant, "passed": passed, "gates": gates,
                                    "measured": measured, "qc_version": VERSION},
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    failed = [k for k, g in gates.items() if not g["ok"]]
    log.info("[NunaWeekly:qc] 완료 %s passed=%s 실패=%s", out_json.name, passed, failed or "없음")
    return report
