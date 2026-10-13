"""
gen/hero_shorts/timestamps.py — 컷 타임스탬프 기록/조회 (정밀 분할의 근거)
=========================================================================
문제: 기존 파이프라인은 컷별 '요청' 길이(10초)만 알고, fal 이 실제로 돌려준
길이는 `_validate_output` 안에서 검증에만 쓰고 버렸다. 그래서 최종 합본물에서
컷 경계를 파생할 근거가 남지 않았고, 분할은 균등 분할로 퇴화했다.

해법: 합본 시점에 각 컷 클립의 실측 길이를 ffprobe 로 읽어 누적 오프셋과 함께
사이드카 JSON(`ep{N}_cut_timestamps.json`)으로 남긴다. 이후 모든 분할은 이
기록을 유일한 근거로 삼는다(추측 금지).

기록 스키마:
    {
      "episode": 87,
      "cut_count": 6,
      "total_duration_sec": 60.867,
      "source": "ffprobe",
      "cuts": [{"cut_no":1,"path":"...","start":0.0,"end":10.145,"duration_sec":10.145}, ...],
      "version": "1.0.0"
    }
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("hero_shorts.timestamps")

VERSION = "1.1.0"


def probe_duration(path):
    """ffprobe 실측 길이(초). 실패 시 예외(추측 금지)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return round(float(r.stdout.strip()), 3)


def measures_path(clip_path):
    """생성 시점 실측 사이드카 경로(generator._measures_path 와 동일 규약)."""
    p = Path(clip_path)
    return p.with_name(p.name + ".measures.json")


def read_measures(clip_path):
    """생성 시점 fal 실측 기록을 읽는다. 없으면 None(추측 금지 — 폴백 판단은 호출부)."""
    mp = measures_path(clip_path)
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("실측 사이드카 해독 실패: %s", mp)
        return None


def record_cut_timestamps(clip_paths, out_path, episode=None):
    """컷 클립 목록 → 누적 오프셋 사이드카 기록.

    v1.1.0: 생성 시점 실측(generator 사이드카)이 있으면 그것을 우선 채택하고
    ffprobe 로 교차검증한다(기록 출처를 컷마다 남긴다).
    """
    if not clip_paths:
        raise ValueError("컷 클립 목록이 비었다 — 기록 거부(추측 금지)")
    cuts = []
    cursor = 0.0
    for i, p in enumerate(clip_paths, start=1):
        probed = probe_duration(p)
        m = read_measures(p)
        gen_dur = None if m is None else m.get("duration_sec")
        if gen_dur is not None:
            dur = round(float(gen_dur), 3)
            source = "fal_measures"
            drift = round(probed - dur, 3)
            if abs(drift) > 0.1:
                log.warning("실측 기록-ffprobe 불일치 cut%d: 기록 %.3f vs ffprobe %.3f (Δ%+.3f)",
                            i, dur, probed, drift)
        else:
            dur = probed
            source = "ffprobe"
            drift = None
        cuts.append({
            "cut_no": i, "path": str(p),
            "start": round(cursor, 3), "end": round(cursor + dur, 3),
            "duration_sec": dur, "source": source, "probe_sec": probed,
            "drift_sec": drift,
        })
        cursor += dur
    sources = sorted({c["source"] for c in cuts})
    payload = {
        "episode": episode,
        "cut_count": len(cuts),
        "total_duration_sec": round(cursor, 3),
        "source": sources[0] if len(sources) == 1 else "mixed",
        "sources": sources,
        "cuts": cuts,
        "version": VERSION,
    }
    pp = Path(out_path)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("컷 타임스탬프 기록: %d컷 · 총 %.3fs → %s", len(cuts), cursor, pp)
    return payload


def load_timestamps(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def group_bounds(payload, groups):
    """컷 그룹 → (start, end) 정밀 경계. groups=((1,2),(3,4),(5,6)).

    Raises:
        ValueError: 존재하지 않는 컷 번호 참조(기록과 그룹 불일치).
    """
    by_no = {c["cut_no"]: c for c in payload["cuts"]}
    out = []
    for a, b in groups:
        if a not in by_no or b not in by_no:
            raise ValueError(f"컷 번호 {a}~{b} 가 기록에 없음(기록 컷: {sorted(by_no)})")
        out.append((by_no[a]["start"], by_no[b]["end"]))
    return out
