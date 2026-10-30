"""
gen/nuna_weekly/images.py — 구간 이미지 생성 (단계 images → IMAGED)
===================================================================
generate_images(episode_id, script, segs, fal, budget, out_dir) -> dict[seg_id, Asset]
    visual 이 image / image_card 인 구간마다 1장 (60초: S2,S3,S4,S5,S7 / 70초: +S6).
    프롬프트 = 캐논 접두(canon_nuna.json) + 구간 image_prompt + ", vertical 9:16, no text, no watermark"
    기준 이미지(캐릭터 일관성, 설계서 17장):
        1) env NUNA_REF_IMAGE_URLS (쉼표 구분 https URL)
        2) 없으면 assets/nuna_ref_front.png (+ nuna_ref_34.png) 를 data URI 로
           ※ PoC 항목 — fal 의 data URI 입력 수용 여부 미검증
        3) 둘 다 없고 dummy 가 아니면 ImageError
    이미 생성된 파일(img_<seg>.png)이 있으면 재사용(재과금 없음). remote_url 은 사이드카 .meta.json.
    jobs(원장 fal_pending) 에 제출 기록이 있으면 재제출 없이 재조회(fal_jobs.run_paid).
    대기 상한: config.fal_image_max_wait_sec() (기본 300s).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config
from .fal_jobs import FalJobs, run_paid
from .models import Asset, Script, ScriptSegment

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.images")

IMAGE_VISUALS = ("image", "image_card")
PROMPT_SUFFIX = ", vertical 9:16, no text, no watermark"
REF_FILES = ("nuna_ref_front.png", "nuna_ref_34.png")
REF_ENV = "NUNA_REF_IMAGE_URLS"


class ImageError(RuntimeError):
    """이미지 생성 실패."""


@lru_cache(maxsize=1)
def load_canon() -> dict[str, Any]:
    try:
        return json.loads(config.CANON_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise ImageError(f"캐논 파일 읽기 실패: {config.CANON_FILE.name} ({type(e).__name__})") from None


def canon_prefix() -> str:
    c = load_canon()
    return f"{c['name']}, {c['appearance']}, wearing {c['outfit']}. Style: {c['style']}. "


def canon_short() -> str:
    c = load_canon()
    return f"{c['name']}, {c['appearance']}, silver ETF shield armor over navy blazer, {c['style']}. "


def build_prompt(seg: ScriptSegment) -> str:
    if not seg.image_prompt.strip():
        raise ImageError(f"{seg.seg_id} image_prompt 비어있음")
    return canon_prefix() + seg.image_prompt.strip() + PROMPT_SUFFIX


def reference_urls(assets_dir: Path | None = None) -> list[str]:
    raw = os.environ.get(REF_ENV, "").strip()
    if raw:
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        bad = [u for u in urls if not u.startswith("https://")]
        if bad:
            raise ImageError(f"{REF_ENV} 에 https 가 아닌 항목 {len(bad)}건")
        return urls
    d = assets_dir or config.ASSETS_DIR
    out: list[str] = []
    for name in REF_FILES:
        p = d / name
        if p.is_file() and p.stat().st_size > 0:
            out.append("data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii"))
    return out


def image_segments(segs: Sequence[config.Segment]) -> list[config.Segment]:
    return [s for s in segs if s.visual in IMAGE_VISUALS]


def _meta_path(p: Path) -> Path:
    return p.with_name(p.name + ".meta.json")


def read_meta(p: Path) -> dict[str, Any]:
    m = _meta_path(p)
    if not m.exists():
        return {}
    try:
        return json.loads(m.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_meta(p: Path, remote_url: str | None, request_id: str | None) -> None:
    _meta_path(p).write_text(json.dumps({"remote_url": remote_url, "request_id": request_id},
                                        ensure_ascii=False), encoding="utf-8")


def _valid_image(p: Path) -> bool:
    if not p.is_file() or p.stat().st_size == 0:
        return False
    try:
        from PIL import Image

        with Image.open(p) as im:
            im.verify()
        return True
    except Exception:
        return False


def generate_images(episode_id: str, script: Script, segs: Sequence[config.Segment], fal: Any, budget: Any,
                    out_dir: Path, jobs: FalJobs | None = None) -> dict[str, Asset]:
    targets = image_segments(segs)
    log.info("[NunaWeekly:images] v%s 시작 · %s · backend=%s · %d장",
             VERSION, episode_id, getattr(fal, "name", "?"), len(targets))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_id = {s.seg_id: s for s in script.segments}
    refs = reference_urls()
    if not refs and getattr(fal, "name", "") != "dummy":
        raise ImageError("기준 이미지 없음 — 설계서 17장")
    out: dict[str, Asset] = {}
    for seg in targets:
        ss = by_id.get(seg.seg_id)
        if ss is None:
            raise ImageError(f"{seg.seg_id} 대본 구간 없음")
        path = out_dir / f"img_{seg.seg_id}.png"
        if _valid_image(path):
            meta = read_meta(path)
            out[seg.seg_id] = Asset(kind="image", key=f"img_{seg.seg_id}", local_path=str(path),
                                    remote_url=meta.get("remote_url"), request_id=meta.get("request_id"))
            log.info("%s 기존 이미지 재사용 (재과금 없음)", seg.seg_id)
            continue
        payload = {"prompt": build_prompt(ss), "image_urls": refs, "aspect_ratio": "9:16",
                   "output_format": "png", "num_images": 1}
        pending = out_dir / f"img_{seg.seg_id}.fal_pending.json"
        res = run_paid(fal, budget, jobs, f"img_{seg.seg_id}", step="images", endpoint=config.FAL_IMAGE_ENDPOINT,
                       payload=payload, out_path=path, pending_path=pending, units=1, unit="image",
                       est_usd=config.PRICE_IMAGE_USD, max_wait_sec=config.fal_image_max_wait_sec())
        if not _valid_image(path):
            raise ImageError(f"{seg.seg_id} 이미지 검증 실패 request_id={res.request_id}")
        write_meta(path, res.remote_url, res.request_id)
        out[seg.seg_id] = Asset(kind="image", key=f"img_{seg.seg_id}", local_path=str(path),
                                remote_url=res.remote_url, request_id=res.request_id)
        log.info("%s 이미지 완료 request_id=%s", seg.seg_id, res.request_id)
    return out
