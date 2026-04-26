"""
Investment OS — Viral Targeting Policy Loader v1.0.0

YAML 정책 파일을 로드하고, 캐시(TTL=300s + mtime 비교)로 운영자 변경 즉시 반영.
파싱 실패 시 fallback 정책 반환 (NFR-02).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

VERSION = "1.0.0"

logger = logging.getLogger(__name__)

# 정책 파일 경로 (환경변수로 오버라이드 가능 — 테스트용)
DEFAULT_POLICY_PATH = "config/viral_targeting.yml"
CACHE_TTL_SECONDS = 300

# Fallback 정책 (파싱 실패 시 안전 기본값)
_FALLBACK_POLICY: dict[str, Any] = {
    "version": "fallback",
    "segments": {
        "S2_25_29": {
            "age_range": [25, 29],
            "weight": 1.0,
            "pain": "이직, 연봉, 자산 시작",
            "desire": "연봉 점프, 시드",
            "allowed_stimulus_level": 4,
            "keywords_supportive": [],
            "numeric_range": {
                "salary_monthly": [300, 600],
                "asset_total": [3000, 20000],
            },
        }
    },
    "conflict_axes": {
        "money": {"weight": 1.0, "label": "돈"},
    },
    "banned_expressions": [],
    "cta_templates": ["댓글로 답해주세요"],
    "disclaimer_templates": ["투자 참고 정보, 권유 아님"],
    "viral_score_threshold": 70,
    "max_retry_count": 3,
    "duplicate_block_hours": 24,
    "auto_delete": {
        "enabled_mode": "off",
        "measurement_schedule": [],
        "thresholds": {
            "min_impressions": 50,
            "min_engagement_rate": 0.005,
            "decision_logic": "AND",
        },
        "safety": {
            "grace_period_hours": 24,
            "daily_delete_limit": 5,
            "protect_high_score": 85,
            "require_manual_review_below": 50,
        },
        "engagement_formula": {
            "numerator": ["like_count", "reply_count", "retweet_count", "quote_count"],
            "denominator": "impression_count",
        },
        "channels": {"x_twitter": False, "telegram": False},
    },
    "fallback": {},
}


class _PolicyCache:
    """Thread-safe 정책 캐시. TTL + mtime 비교."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at: float = 0.0
        self._cached_mtime: float = 0.0
        self._cached_path: str = ""

    def get(self, path: str) -> dict[str, Any]:
        with self._lock:
            now = time.time()

            if (
                self._cached is not None
                and self._cached_path == path
                and (now - self._cached_at) < CACHE_TTL_SECONDS
            ):
                try:
                    current_mtime = os.path.getmtime(path)
                    if current_mtime == self._cached_mtime:
                        return deepcopy(self._cached)
                except OSError:
                    pass

            policy = _load_policy_from_disk(path)
            try:
                self._cached_mtime = os.path.getmtime(path)
            except OSError:
                self._cached_mtime = 0.0

            self._cached = policy
            self._cached_at = now
            self._cached_path = path
            return deepcopy(policy)

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._cached_at = 0.0
            self._cached_mtime = 0.0


_cache = _PolicyCache()


def _load_policy_from_disk(path: str) -> dict[str, Any]:
    """디스크에서 YAML 파싱. 실패 시 fallback 반환."""
    try:
        p = Path(path)
        if not p.is_file():
            logger.warning(
                "[ViralPolicyLoader] 정책 파일 없음 → fallback 사용 (path=%s)", path
            )
            return deepcopy(_FALLBACK_POLICY)

        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            logger.error(
                "[ViralPolicyLoader] YAML 루트가 dict 아님 → fallback (path=%s)", path
            )
            return deepcopy(_FALLBACK_POLICY)

        required_keys = ["segments", "conflict_axes", "viral_score_threshold"]
        missing = [k for k in required_keys if k not in data]
        if missing:
            logger.error(
                "[ViralPolicyLoader] 필수 키 누락 %s → fallback (path=%s)",
                missing,
                path,
            )
            return deepcopy(_FALLBACK_POLICY)

        # 가중치 합 검증 (segments)
        weight_sum = sum(s.get("weight", 0.0) for s in data["segments"].values())
        if abs(weight_sum - 1.0) > 0.01:
            logger.warning(
                "[ViralPolicyLoader] segments weight 합=%.4f (1.0 권장) — 정상화 처리",
                weight_sum,
            )
            for sid, cfg in data["segments"].items():
                cfg["weight"] = (
                    cfg.get("weight", 0.0) / weight_sum if weight_sum > 0 else 0.0
                )

        logger.info(
            "[ViralPolicyLoader] v%s 로드 완료 (정책 v%s, segments=%d, axes=%d)",
            VERSION,
            data.get("version", "?"),
            len(data.get("segments", {})),
            len(data.get("conflict_axes", {})),
        )
        return data

    except yaml.YAMLError as e:
        logger.error("[ViralPolicyLoader] YAML 파싱 실패 → fallback: %s", e)
        return deepcopy(_FALLBACK_POLICY)
    except Exception as e:
        logger.error("[ViralPolicyLoader] 알 수 없는 오류 → fallback: %s", e)
        return deepcopy(_FALLBACK_POLICY)


def load_policy(path: str | None = None) -> dict[str, Any]:
    """정책 dict 반환. 호출 측에서 자유롭게 수정해도 캐시에 영향 없음 (deepcopy)."""
    resolved_path = path or os.getenv(
        "VIRAL_TARGETING_POLICY_PATH", DEFAULT_POLICY_PATH
    )
    return _cache.get(resolved_path)


def invalidate_cache() -> None:
    """캐시 강제 무효화 (테스트/긴급 운영용)."""
    _cache.invalidate()
    logger.info("[ViralPolicyLoader] 캐시 강제 무효화")


def is_fallback(policy: dict[str, Any]) -> bool:
    """주어진 정책이 fallback인지 판정 (운영 모니터링용)."""
    return policy.get("version") == "fallback"
