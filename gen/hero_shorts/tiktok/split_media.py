"""
gen/hero_shorts/tiktok/split_media.py — 영상 분할 (ffmpeg, 크레딧 0)
====================================================================
두 경로를 제공한다.

    1) split_by_timestamps : 컷 타임스탬프 기록(정본)으로 정밀 분할  ← 권장
    2) split_by_groups     : 기록이 없을 때의 폴백(균등 분할) — 근사치이며
                             정밀하지 않음을 호출부가 인지해야 한다.

정밀 분할의 유일한 근거는 `gen/hero_shorts/timestamps.py` 가 합본 시점에 남긴
사이드카 JSON 이다. 무음·장면전환 탐지로 경계를 '추정'하지 않는다(실측 결과
두 방법 모두 ep86/ep87 에서 경계를 찾지 못했다).
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("tiktok.split_media")

VERSION = "1.1.0"


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _cut_clip(src, start, end, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
         "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)],
        check=True,
    )


def split_by_timestamps(src, out_dir, ts_payload, groups, prefix="seg"):
    """컷 타임스탬프 기록 기반 정밀 분할(권장 경로).

    Args:
        ts_payload: timestamps.record_cut_timestamps() 산출 dict.
        groups: ((1,2),(3,4),(5,6)) — 컷 번호 그룹.
    """
    from .. import timestamps as T
    bounds = T.group_bounds(ts_payload, groups)
    total = float(ts_payload["total_duration_sec"])
    real = probe_duration(src)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, ((s, e), (a, b)) in enumerate(zip(bounds, groups), start=1):
        dst = out / f"{prefix}{i}.mp4"
        _cut_clip(src, s, e, dst)
        segs.append({
            "segment_no": i, "cut_nos": list(range(a, b + 1)),
            "start": round(s, 3), "end": round(e, 3),
            "duration_sec": round(e - s, 3), "path": str(dst),
        })
        log.info("정밀 분할 %d: 컷%s %.3f~%.3f (%.3fs)", i, list(range(a, b + 1)), s, e, e - s)
    return {
        "src": str(src), "method": "timestamps",
        "total_duration_sec": round(total, 3), "media_duration_sec": round(real, 3),
        "duration_delta_sec": round(real - total, 3),
        "cut_count": ts_payload["cut_count"], "segments": segs, "version": VERSION,
    }


def split_by_groups(src, out_dir, cut_count, groups, prefix="seg"):
    """폴백: 균등 분할(기록 부재 시). 정밀하지 않음 — 결과에 명시한다."""
    dur = probe_duration(src)
    per = dur / cut_count
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, (a, b) in enumerate(groups, start=1):
        start = per * (a - 1)
        end = dur if b == cut_count else per * b
        dst = out / f"{prefix}{i}.mp4"
        _cut_clip(src, start, end, dst)
        segs.append({
            "segment_no": i, "cut_nos": list(range(a, b + 1)),
            "start": round(start, 3), "end": round(end, 3),
            "duration_sec": round(end - start, 3), "path": str(dst),
        })
        log.info("균등 분할 %d: 컷%s %.2f~%.2f (%.2fs)", i, list(range(a, b + 1)), start, end, end - start)
    return {"src": str(src), "method": "equal_fallback",
            "total_duration_sec": round(dur, 3), "cut_count": cut_count,
            "segments": segs, "version": VERSION}


def save_json(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)
