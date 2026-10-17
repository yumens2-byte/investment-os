"""
gen/hero_shorts/publish_targets/__init__.py — 발행 타깃 계층 (유튜브·쓰레드)
============================================================================
설계 정본: `hero_shorts_channel_design_2026-10-18.md` v1.0 (마스터 승인 E1 = 경로 A)
전환: 틱톡 단일 라인 → 유튜브 쇼츠 + 쓰레드 2채널 (Zernio REST 경유)

계층 원칙:
    - 공통 계층은 플랫폼을 모른다 (승인·원장·미러·복구·감시).
    - 타깃 계층만 플랫폼을 안다 (페이로드·게이트·캡션·검증).
    - 전송 수단은 transport 하나에 격리한다 (경로 A/B 교체 지점).

타깃 인스턴스는 **지연 import** 로 만든다 — 패키지 import 만으로는
무거운 의존(검증·네트워크)을 끌어오지 않기 위함이다(순환 import 회피 포함).
"""
VERSION = "2.0.0"

PLATFORMS = ("youtube", "threads")

# 플랫폼 → (모듈 경로, 클래스명)
_TARGET_REGISTRY = {
    "youtube": ("publish_targets.youtube", "YoutubeTarget"),
    "threads": ("publish_targets.threads", "ThreadsTarget"),
}


def get_target(platform: str, **kwargs):
    """플랫폼명 → 타깃 인스턴스. 미지원 플랫폼은 즉시 거부한다."""
    key = str(platform or "").strip().lower()
    if key not in _TARGET_REGISTRY:
        raise ValueError(
            f"미지원 발행 타깃: {platform!r} — 지원: {list(_TARGET_REGISTRY)}")
    import importlib

    mod_name, cls_name = _TARGET_REGISTRY[key]
    mod = importlib.import_module(f"gen.hero_shorts.{mod_name}")
    return getattr(mod, cls_name)(**kwargs)


def make_targets(platforms=PLATFORMS, **kwargs):
    """플랫폼 목록 → {platform: target}. kwargs 는 플랫폼별 dict 로 전달한다."""
    return {p: get_target(p, **kwargs.get(p, {})) for p in platforms}


def registry() -> dict:
    return dict(_TARGET_REGISTRY)
