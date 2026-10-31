"""nuna_weekly 테스트 공통 설정 (DN2026_0086).

뉴스 방송 연출(구간별 zoompan 움직임·veryfast 최종 인코딩)은 60초 영상 1편에 수십 초가 걸려,
전체 흐름 테스트 여러 개를 돌리는 nuna-regression CI(timeout 15분)를 넘길 수 있다.
연출 자체는 test_assemble.py 가 실제 설정으로 검증하므로, 그 밖의 테스트에서는
이미지 조각을 정지 화면 + 카드 고정 합성으로, 인코딩 프리셋을 ultrafast 로 바꿔 흐름만 검증한다.
(전환·로고·시세 띠·자막·음량 합성과 길이 검증은 그대로 실행된다.)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gen.nuna_weekly import assemble

_FAST_X264 = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
              "-r", str(assemble.VIDEO_FPS), "-video_track_timescale", str(assemble.VIDEO_FPS * 1000)]


def _fast_image_piece(img: Path, seconds: float, card_png: Path | None, out: Path, motion=None) -> Path:
    n = assemble._frames(seconds)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(img)]
    base = f"[0:v]{assemble._fill_crop()},loop=loop=-1:size=1:start=0,setpts=N/{assemble.VIDEO_FPS}/TB"
    if card_png is not None:
        trimmed, cx, cy = assemble._trim_png(card_png)
        cmd += ["-i", str(trimmed)]
        fc = f"{base}[bg];{assemble._still(1)}[c];[bg][c]overlay={cx}:{cy}:shortest=1,format=yuv420p[v]"
    else:
        fc = f"{base},format=yuv420p[v]"
    assemble._run(cmd + ["-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-an", *_FAST_X264,
                         str(out)], f"이미지 조각(테스트 고속) {out.name}")
    return out


@pytest.fixture(autouse=True)
def _fast_render(request, monkeypatch):
    if request.node.module.__name__.endswith("test_assemble"):
        return
    monkeypatch.setattr(assemble, "_image_piece", _fast_image_piece)
    monkeypatch.setattr(assemble, "_X264", _FAST_X264)
    monkeypatch.setattr(assemble, "_X264_PIECE", _FAST_X264)
