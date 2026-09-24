"""
gen/hero_shorts/mediashare.py — 발행 미디어 업로드·공개링크 회수
================================================================
gsk upload(래퍼 URL) → gsk download(토큰화 공개 URL, 24시간 유효) 2단계.
비용 0원. gsk CLI 환경(Genspark 세션/스케줄)에서만 동작한다.
"""
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("hero_shorts.mediashare")

_URL_RE = re.compile(r"https?://[^\s\"']+")


def _gsk_call(args):
    cp = subprocess.run(["gsk", *args], capture_output=True, text=True, timeout=300)
    if cp.returncode != 0:
        raise RuntimeError(
            f"gsk {' '.join(args[:2])} 실패 exit={cp.returncode} — {cp.stderr.strip()[:200]}"
        )
    out = cp.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        m = _URL_RE.search(out)
        if m:
            return {"url": m.group(0)}
        raise RuntimeError(f"gsk 출력 파싱 실패: {out[:200]}")


def _first_url(payload):
    if isinstance(payload, dict):
        for k in ("url", "public_url", "file_url", "download_url", "link"):
            v = payload.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in payload.values():
            u = _first_url(v) if isinstance(v, (dict, list)) else (
                v if isinstance(v, str) and v.startswith("http") else None)
            if u:
                return u
    if isinstance(payload, list):
        for v in payload:
            u = _first_url(v)
            if u:
                return u
    return None


def upload_media(path):
    """로컬 영상 → 래퍼 URL → 공개 URL. {"wrapper_url", "public_url"} 반환."""
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"업로드 대상 없음: {p}")
    up = _gsk_call(["upload", str(p)])
    wrapper = _first_url(up)
    if not wrapper:
        raise RuntimeError(f"래퍼 URL 미검출: {json.dumps(up)[:200]}")
    dl = _gsk_call(["download", wrapper])
    public = _first_url(dl) or wrapper
    log.info("미디어 업로드 완료: %s → %s", p.name, public)
    return {"wrapper_url": wrapper, "public_url": public}
