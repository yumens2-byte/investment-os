"""
gen/hero_shorts/publish_targets/threads/gates.py — 쓰레드 전용 게이트 (TH-G1~TH-G5)
==================================================================================
설계서 §4.4 · §4.5. 통과(ok=True)가 아니면 전송하지 않는다(fail-closed).

    TH-G1 길이 ≤ 300초 · 비율 ∈ {9:16, 16:9, 1:1}   (F7)
    TH-G2 본문 ≤ 500자                              (F7)
    TH-G3 topic_tag 1~50자, '.'·'&' 미포함           (F8)
    TH-G4 파일 ≤ 1GB · MP4(H.264/AAC)               (F7)
    TH-G5 firstComment 사용 시 ≤ 500자               (F8)
    TH-Q  24시간 내 발행 수 ≤ 250 — 차단 + 경보        (F8)

TH-Q 는 경보형이 아니라 **차단형**이다(설계서 §4.5). 한도 초과 시 발행하지 않는다.
"""
import logging
import os
from typing import Optional, Sequence

log = logging.getLogger("hero_shorts.publish_targets.threads.gates")

VERSION = "1.0.0"

GATE_IDS = ("TH-G1", "TH-G2", "TH-G3", "TH-G4", "TH-G5")

MAX_DURATION_SEC = 300          # F7 — 5분
VALID_RATIOS = ("9:16", "16:9", "1:1")   # F7
TEXT_LIMIT = 500                # F7
FIRST_COMMENT_LIMIT = 500       # F8
TOPIC_TAG_MIN = 1               # F8
TOPIC_TAG_MAX = 50              # F8
MAX_FILE_BYTES = 1024 ** 3      # F7 — 1GB
QUOTA_PER_24H = 250             # F8
FORBIDDEN_TOPIC_CHARS = (".", "&")   # F8

ALLOWED_VIDEO_CODECS = ("h264", "avc1")     # F7
ALLOWED_AUDIO_CODECS = ("aac", "mp4a")      # F7


def gate_thg1(duration_sec, ratio):
    problems = []
    if duration_sec is None:
        problems.append("길이 미확정")
    else:
        try:
            d = float(duration_sec)
        except (TypeError, ValueError):
            problems.append(f"길이 값이 수치가 아님: {duration_sec!r}")
            d = None
        if d is not None:
            if d <= 0:
                problems.append("길이 0 이하")
            elif d > MAX_DURATION_SEC:
                problems.append(f"길이 {d}s > 쓰레드 상한 {MAX_DURATION_SEC}s")
    if ratio not in VALID_RATIOS:
        problems.append(f"비율 {ratio} ∉ {list(VALID_RATIOS)}")
    return {"gate": "TH-G1", "ok": not problems, "problems": problems}


def gate_thg2(content):
    n = len(str(content or ""))
    return {"gate": "TH-G2", "ok": n <= TEXT_LIMIT,
            "problems": [] if n <= TEXT_LIMIT else [f"본문 {n}자 > {TEXT_LIMIT}자"],
            "length": n}


def gate_thg3(topic_tag):
    t = str(topic_tag or "")
    problems = []
    if not t:
        problems.append("topic_tag 없음")
    else:
        if len(t) < TOPIC_TAG_MIN or len(t) > TOPIC_TAG_MAX:
            problems.append(f"topic_tag {len(t)}자 ∉ [{TOPIC_TAG_MIN},{TOPIC_TAG_MAX}]")
        bad = [c for c in FORBIDDEN_TOPIC_CHARS if c in t]
        if bad:
            problems.append(f"topic_tag 에 금지 문자 포함: {bad}")
    return {"gate": "TH-G3", "ok": not problems, "problems": problems}


def gate_thg4(file_bytes=None, *, container: Optional[str] = None,
              video_codec: Optional[str] = None,
              audio_codec: Optional[str] = None):
    problems = []
    if file_bytes is None:
        problems.append("파일 크기 미확정 — 실측 필요(추정 금지)")
    elif int(file_bytes) > MAX_FILE_BYTES:
        problems.append(f"파일 {int(file_bytes)}B > {MAX_FILE_BYTES}B")
    if container is not None and str(container).lower() not in ("mp4", "m4v"):
        problems.append(f"컨테이너 '{container}' — MP4 만 허용")
    if video_codec is not None and str(video_codec).lower() not in ALLOWED_VIDEO_CODECS:
        problems.append(f"비디오 코덱 '{video_codec}' — H.264(avc1) 만 허용")
    if audio_codec is not None and str(audio_codec).lower() not in ALLOWED_AUDIO_CODECS:
        problems.append(f"오디오 코덱 '{audio_codec}' — AAC(mp4a) 만 허용")
    return {"gate": "TH-G4", "ok": not problems, "problems": problems}


def gate_thg5(first_comment):
    if first_comment is None:
        return {"gate": "TH-G5", "ok": True, "problems": [], "used": False}
    n = len(str(first_comment))
    return {"gate": "TH-G5", "ok": n <= FIRST_COMMENT_LIMIT,
            "problems": [] if n <= FIRST_COMMENT_LIMIT
            else [f"firstComment {n}자 > {FIRST_COMMENT_LIMIT}자"],
            "used": True, "length": n}


def gate_thq(recent_post_count_24h):
    """TH-Q — 24시간 쿼터. 차단형(설계서 §4.5)."""
    n = int(recent_post_count_24h or 0)
    ok = n < QUOTA_PER_24H
    return {"gate": "TH-Q", "ok": ok, "count": n, "limit": QUOTA_PER_24H,
            "problems": [] if ok else [f"쓰레드 24시간 발행 {n}건 — 한도 {QUOTA_PER_24H} 도달(차단)"]}


def run_threads_gates(duration_sec=None, ratio=None, content=None,
                      topic_tag=None, first_comment=None,
                      file_bytes=None, container="mp4",
                      video_codec="h264", audio_codec="aac",
                      recent_post_count_24h=0):
    """TH-G1~TH-G5 + TH-Q 전건 실행. ok=False 이면 쓰레드로 전송하지 않는다."""
    results = [
        gate_thg1(duration_sec, ratio),
        gate_thg2(content),
        gate_thg3(topic_tag),
        gate_thg4(file_bytes, container=container,
                  video_codec=video_codec, audio_codec=audio_codec),
        gate_thg5(first_comment),
        gate_thq(recent_post_count_24h),
    ]
    ok = all(r["ok"] for r in results)
    failed = [r["gate"] for r in results if not r["ok"]]
    log.info("쓰레드 게이트 %s%s", "통과" if ok else "실패", "" if ok else f" — {failed}")
    return {"ok": ok, "failed": failed, "results": results, "version": VERSION}


def local_file_bytes(path) -> Optional[int]:
    """로컬 파일 실측 크기. 없으면 None(추정 금지)."""
    if not path:
        return None
    try:
        return os.path.getsize(str(path))
    except OSError:
        return None


def probe_codecs(path, *, runner=None) -> dict:
    """ffprobe 실측 — 값이 없으면 빈 dict 로 남긴다(추정하지 않는다)."""
    import json
    import subprocess

    if not path:
        return {}
    runner = runner or (lambda a: subprocess.run(a, capture_output=True, text=True))
    try:
        cp = runner(["ffprobe", "-v", "error", "-show_streams", "-of",
                     "json", str(path)])
        data = json.loads(getattr(cp, "stdout", "") or "{}")
    except Exception as exc:
        log.warning("ffprobe 실패 — 게이트 판정 불가: %s", exc)
        return {}
    out = {"container": None, "video_codec": None, "audio_codec": None}
    for st in data.get("streams") or []:
        kind = st.get("codec_type")
        if kind == "video" and not out["video_codec"]:
            out["video_codec"] = st.get("codec_name")
        elif kind == "audio" and not out["audio_codec"]:
            out["audio_codec"] = st.get("codec_name")
    name = str(data.get("format", {}).get("format_name") or "")
    if name:
        out["container"] = name.split(",")[0]
    return out
