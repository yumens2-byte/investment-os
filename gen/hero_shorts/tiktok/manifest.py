"""
gen/hero_shorts/tiktok/manifest.py — JEN 7컷 → 틱톡 3편 발행 매니페스트
======================================================================
설계서 v1.0 §2 준수. cut_split(분할) + caption(문구) + gates(게이트)를 결합한다.
결정론적: 같은 입력 → 같은 매니페스트. 미디어 URL은 업로드 단계에서 주입한다.
"""
import json
import logging
from pathlib import Path

from .. import cut_split
from . import caption as tk_caption
from . import gates as tk_gates

log = logging.getLogger("tiktok.manifest")

VERSION = "1.2.0"
# 발행 길이 상한 — 플랫폼 실측 한도(TikTok/Zernio 3초~10분).
# 계정별 실제 상한은 TikTok creator_info.max_video_post_duration_sec 로 확인한다.
# 마스터 지침(2026-10-13): "60초 초과 무관" — 정기 발행은 합본 1편을 기본으로 한다.
DEFAULT_MAX_DURATION_SEC = 600


def build_manifest(cuts, title, hook=None, max_duration_sec=DEFAULT_MAX_DURATION_SEC,
                   privacy_level=None, draft=False, lane=tk_gates.BUSINESS_LANE,
                   allow_comments=True, allow_duet=True, allow_stitch=True):
    """7컷 목록/plan dict → 3편 발행 매니페스트."""
    if isinstance(cuts, dict):
        cuts = cuts.get("cuts", [])
    level = privacy_level or tk_gates.DEFAULT_PRIVACY_LEVEL
    segments = cut_split.split_plan(cuts)
    out_segments = []
    for seg in segments:
        text = tk_caption.build_segment_caption(seg, title, hook=hook)
        gate = tk_gates.run_gates(
            caption=text,
            duration_sec=seg["total_duration_sec"],
            max_duration_sec=max_duration_sec,
            privacy_level=level,
            draft=draft,
            lane=lane,
        )
        out_segments.append({
            "segment_no": seg["segment_no"],
            "cut_nos": seg["cut_nos"],
            "start": None,
            "end": None,
            "total_duration_sec": seg["total_duration_sec"],
            "series_marker": seg["series_marker"],
            "caption": text,
            "media_url": None,
            "privacy_level": level,
            "draft": draft,
            "allow_comments": allow_comments,
            "allow_duet": allow_duet,
            "allow_stitch": allow_stitch,
            "gates": gate,
            "status": "ready" if gate["ok"] else "blocked",
        })
    manifest = {
        "platform": "tiktok",
        "title": title,
        "privacy_level": level,
        "draft": draft,
        "lane": lane,
        "segment_count": len(out_segments),
        "total_duration_sec": cut_split.total_duration(segments),
        "all_gates_ok": all(s["gates"]["ok"] for s in out_segments),
        "segments": out_segments,
        "version": VERSION,
    }
    log.info(
        "틱톡 매니페스트 v%s: %d편 · 게이트 %s",
        VERSION, len(out_segments),
        "통과" if manifest["all_gates_ok"] else "실패(발행 차단)",
    )
    return manifest


def save_manifest(path, manifest):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def segment_gate_ok(segment):
    """세그먼트 게이트 판정 — 정본 키는 `gates.ok` 단 하나.

    과거 폴백 스크립트가 `gates_ok` 키를 쓴 탓에 발행 단계에서 KeyError 가 났다.
    레거시 키를 발견하면 조용히 넘기지 않고 명시적으로 실패시킨다(재발 방지).
    """
    g = segment.get("gates")
    if not isinstance(g, dict) or "ok" not in g:
        legacy = "gates_ok" in segment
        raise ValueError(
            "세그먼트 스키마 위반 — 'gates' dict(gates.ok) 필요"
            + (" [레거시 'gates_ok' 키 발견: from_split() 으로 재생성하세요]" if legacy else "")
        )
    return bool(g["ok"])


def from_split(split_result, title, hook=None, privacy_level=None, draft=False,
               lane=tk_gates.BUSINESS_LANE, allow_comments=True, allow_duet=True,
               allow_stitch=True, media_urls=None):
    """미디어 분할 결과(split_media) → 정본 스키마 매니페스트.

    build_manifest 와 **동일한 세그먼트 스키마**를 보장한다(수기 세그먼트 조립 금지).
    과거 결함(균등 분할 스크립트가 스키마를 어긋나게 기록)의 재발을 막는 단일 경로다.
    """
    level = privacy_level or tk_gates.DEFAULT_PRIVACY_LEVEL
    segs = split_result.get("segments") or []
    if not segs:
        raise ValueError("분할 결과에 segments 없음 — 매니페스트 생성 거부(추측 금지)")
    out_segments = []
    for i, s in enumerate(segs, start=1):
        no = int(s.get("segment_no", i))
        marker = f"{no}/{len(segs)}"
        text = tk_caption.build_segment_caption(
            {"segment_no": no, "series_marker": marker}, title, hook=hook)
        dur = float(s["duration_sec"])
        gate = tk_gates.run_gates(
            caption=text, duration_sec=dur,
            max_duration_sec=DEFAULT_MAX_DURATION_SEC,
            privacy_level=level, draft=draft, lane=lane,
        )
        url = media_urls.get(no) if isinstance(media_urls, dict) else None
        out_segments.append({
            "segment_no": no,
            "cut_nos": s.get("cut_nos"),
            "start": s.get("start"),
            "end": s.get("end"),
            "total_duration_sec": dur,
            "series_marker": marker,
            "caption": text,
            "media_url": url,
            "privacy_level": level,
            "draft": draft,
            "allow_comments": allow_comments,
            "allow_duet": allow_duet,
            "allow_stitch": allow_stitch,
            "gates": gate,
            "status": "ready" if gate["ok"] else "blocked",
        })
    manifest = {
        "platform": "tiktok",
        "title": title,
        "privacy_level": level,
        "draft": draft,
        "lane": lane,
        "split_method": split_result.get("method"),
        "segment_count": len(out_segments),
        "total_duration_sec": round(sum(x["total_duration_sec"] for x in out_segments), 3),
        "all_gates_ok": all(segment_gate_ok(x) for x in out_segments),
        "segments": out_segments,
        "version": VERSION,
    }
    log.info(
        "틱톡 매니페스트(분할 기반) v%s: %d편 · method=%s · 게이트 %s",
        VERSION, len(out_segments), manifest["split_method"],
        "통과" if manifest["all_gates_ok"] else "실패(발행 차단)",
    )
    return manifest


def from_combined(video_path, duration_sec, title, hook=None,
                  max_duration_sec=DEFAULT_MAX_DURATION_SEC,
                  privacy_level=None, draft=False, lane=tk_gates.BUSINESS_LANE,
                  allow_comments=True, allow_duet=True, allow_stitch=True,
                  media_url=None, cut_nos=None):
    """합본(단일 편) 매니페스트 — 분할 없이 1편으로 발행한다.

    마스터 지침(2026-10-13): 정기 발행은 3편 분할이 아니라 전편 합본 1편으로 올린다.
    build_manifest/from_split 과 동일한 세그먼트 스키마를 쓴다(수기 조립 금지).
    """
    if not video_path and not media_url:
        raise ValueError("합본 매니페스트 — video_path 또는 media_url 필요(추측 금지)")
    dur = float(duration_sec)
    if dur <= 0:
        raise ValueError(f"합본 길이 무효: {duration_sec}")
    level = privacy_level or tk_gates.DEFAULT_PRIVACY_LEVEL
    text = tk_caption.build_combined_caption(title, hook=hook)
    gate = tk_gates.run_gates(
        caption=text, duration_sec=dur, max_duration_sec=max_duration_sec,
        privacy_level=level, draft=draft, lane=lane,
    )
    seg = {
        "segment_no": 1,
        "cut_nos": list(cut_nos) if cut_nos else None,
        "start": 0.0,
        "end": round(dur, 3),
        "total_duration_sec": round(dur, 3),
        "series_marker": "",
        "caption": text,
        "media_url": media_url,
        "video_path": str(video_path) if video_path else None,
        "privacy_level": level,
        "draft": draft,
        "allow_comments": allow_comments,
        "allow_duet": allow_duet,
        "allow_stitch": allow_stitch,
        "gates": gate,
        "status": "ready" if gate["ok"] else "blocked",
    }
    manifest = {
        "platform": "tiktok",
        "title": title,
        "mode": "combined",
        "privacy_level": level,
        "draft": draft,
        "lane": lane,
        "segment_count": 1,
        "total_duration_sec": round(dur, 3),
        "all_gates_ok": gate["ok"],
        "segments": [seg],
        "version": VERSION,
    }
    log.info("틱톡 합본 매니페스트 v%s: 1편 %.3fs · 게이트 %s", VERSION, dur,
             "통과" if gate["ok"] else "실패(발행 차단)")
    return manifest


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
