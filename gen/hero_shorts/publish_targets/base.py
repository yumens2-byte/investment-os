"""
gen/hero_shorts/publish_targets/base.py — 타깃 계층 공통 인터페이스
====================================================================
설계서 §2.3. 모든 타깃(유튜브·쓰레드)은 이 계약을 만족한다.
전송 수단은 여기서 알지 않는다 — transport 가 주입된다.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

VERSION = "1.0.0"

# 원장 targets[platform].status 로 허용되는 값 (설계서 §3.1 상태값 정의).
STATUS_PENDING = "pending"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_SKIPPED = "skipped"

VALID_STATUSES = (
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_FAILED,
    STATUS_BLOCKED,
    STATUS_SKIPPED,
)

# 재시도 금지 상태 — 게이트 미통과/정책 제외는 사람이 원인을 제거해야 한다.
NO_RETRY_STATUSES = (STATUS_BLOCKED, STATUS_SKIPPED)
# 재발행 금지 상태 — 멱등성.
NO_REPUBLISH_STATUSES = (STATUS_PUBLISHED,)


@dataclass(frozen=True)
class TargetPlan:
    """타깃 1건의 발행 계획 — 전송 직전 상태."""

    platform: str
    account_id: Optional[str]
    gate: Dict[str, Any]
    payload: Dict[str, Any]
    idempotency_key: str

    @property
    def gate_ok(self) -> bool:
        return bool(self.gate.get("ok"))


@dataclass
class TargetResult:
    """타깃 1건의 발행 결과 — 원장 기록 단위."""

    platform: str
    outcome: str                                   # STATUS_* 중 하나
    post_id: Optional[str] = None
    platform_post_id: Optional[str] = None
    platform_post_url: Optional[str] = None
    published_at: Optional[str] = None
    published_at_source: Optional[str] = None
    recorded_at: Optional[str] = None
    error: Optional[str] = None
    attempts: List[Dict[str, Any]] = field(default_factory=list)

    def to_ledger(self) -> Dict[str, Any]:
        """원장 targets[platform] 에 저장할 dict. None 값은 저장하지 않는다."""
        out: Dict[str, Any] = {"status": self.outcome}
        for k, v in (
            ("post_id", self.post_id),
            ("platform_post_id", self.platform_post_id),
            ("platform_post_url", self.platform_post_url),
            ("published_at", self.published_at),
            ("published_at_source", self.published_at_source),
            ("recorded_at", self.recorded_at),
            ("error", self.error),
        ):
            if v is not None:
                out[k] = v
        out["attempts"] = list(self.attempts)
        return out


@runtime_checkable
class Target(Protocol):
    """타깃 계약. 각 플랫폼 모듈이 구현한다."""

    platform: str
    env_account_key: str

    def build_plan(self, record: Dict[str, Any], projection: Dict[str, Any]) -> TargetPlan:
        ...

    def verify(self, record: Dict[str, Any], result: TargetResult) -> Dict[str, Any]:
        ...

    def extract_id(self, url: str) -> Optional[str]:
        ...


def resolve_account_id(env_key: str, explicit: Optional[str] = None) -> Optional[str]:
    """계정 ID 해석 — 인자 > 환경변수. 미설정이면 None(호출부가 차단 판단)."""
    import os

    return explicit or os.environ.get(env_key) or None
