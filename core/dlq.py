"""
core/dlq.py (B-17)
=====================
Dead Letter Queue — 외부 API 발행 실패 저장 + 자동 재처리

대상: X API / Telegram / Gemini 모든 외부 호출
저장: data/published/dlq.json
영구 실패: data/published/dead_letters.json

흐름:
  1. 외부 API 실패 → enqueue() → dlq.json 저장
  2. 다음 세션 시작 시 → process_queue() → 재처리
  3. 3회 실패 → dead_letters.json 이동 + TG 알림

DLQ 항목 구조:
{
  "id": "uuid",
  "type": "x_tweet" | "tg_message" | "tg_document" | "gemini_call",
  "payload": { ... },
  "created_at": "ISO",
  "retry_count": 0,
  "max_retries": 3,
  "last_error": "...",
  "last_retry_at": null
}
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

DLQ_PATH = Path(os.getenv("DLQ_PATH", "data/published/dlq.json"))
DEAD_LETTER_PATH = Path(os.getenv("DEAD_LETTER_PATH", "data/published/dead_letters.json"))

DEFAULT_MAX_RETRIES = 3


# ──────────────────────────────────────────────────────────────
# 1. DLQ 저장/로드
# ──────────────────────────────────────────────────────────────

def _load_queue() -> list:
    """dlq.json 로드"""
    if DLQ_PATH.exists():
        try:
            data = json.loads(DLQ_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def _save_queue(queue: list) -> None:
    """dlq.json 저장"""
    DLQ_PATH.parent.mkdir(parents=True, exist_ok=True)
    DLQ_PATH.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_dead_letters() -> list:
    """dead_letters.json 로드"""
    if DEAD_LETTER_PATH.exists():
        try:
            data = json.loads(DEAD_LETTER_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def _save_dead_letters(letters: list) -> None:
    """dead_letters.json 저장 (최근 100건만 유지)"""
    DEAD_LETTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEAD_LETTER_PATH.write_text(
        json.dumps(letters[-100:], ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────────────────────
# 2. enqueue — 실패 항목 DLQ에 추가
# ──────────────────────────────────────────────────────────────

def enqueue(
    task_type: str,
    payload: dict,
    error: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """
    실패한 작업을 DLQ에 저장.

    Args:
        task_type:   "x_tweet" | "tg_message" | "tg_document" | "gemini_call"
        payload:     재처리에 필요한 데이터
        error:       마지막 에러 메시지
        max_retries: 최대 재시도 횟수

    Returns:
        생성된 DLQ 항목 ID
    """
    item_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "id": item_id,
        "type": task_type,
        "payload": payload,
        "created_at": now,
        "retry_count": 0,
        "max_retries": max_retries,
        "last_error": error[:300],
        "last_retry_at": None,
    }

    queue = _load_queue()
    queue.append(item)
    _save_queue(queue)

    logger.info(f"[DLQ] 저장: {task_type} | id={item_id} | error={error[:80]}")
    return item_id


# ──────────────────────────────────────────────────────────────
# 3. process_queue — DLQ 자동 재처리
# ──────────────────────────────────────────────────────────────

# 재처리 핸들러 레지스트리
_HANDLERS: dict[str, Callable] = {}


def register_handler(task_type: str, handler: Callable) -> None:
    """
    DLQ 재처리 핸들러 등록.

    handler(payload: dict) -> bool
      True: 성공 (DLQ에서 제거)
      False: 실패 (retry_count 증가)
    """
    _HANDLERS[task_type] = handler
    logger.debug(f"[DLQ] 핸들러 등록: {task_type}")


def _auto_register_handlers() -> None:
    """DLQ 재처리 핸들러 자동 등록 (최초 1회)"""
    if _HANDLERS:
        return  # 이미 등록됨

    # X 트윗 재발행
    def _retry_x_tweet(payload: dict) -> bool:
        from publishers.x_publisher import publish_tweet
        result = publish_tweet(payload.get("text", ""))
        return result.get("success", False)

    # TG 메시지 재발행
    def _retry_tg_message(payload: dict) -> bool:
        from publishers.telegram_publisher import send_message
        results = send_message(
            payload.get("text", ""),
            channel=payload.get("channel", "free"),
        )
        return any(r.get("ok") for r in results) if results else False

    # TG 문서 재발행
    def _retry_tg_document(payload: dict) -> bool:
        from publishers.telegram_publisher import send_document
        path = payload.get("path", "")
        if not path or not os.path.exists(path):
            return False
        results = send_document(
            path,
            caption=payload.get("caption", ""),
            channel=payload.get("channel", "paid"),
        )
        return any(r.get("ok") for r in results) if results else False

    # Gemini 호출은 컨텍스트 의존적이라 재처리 불가 → 스킵 처리
    def _retry_gemini_call(payload: dict) -> bool:
        logger.info("[DLQ] Gemini 호출은 재처리 불가 — dead letter 처리")
        return False

    register_handler("x_tweet", _retry_x_tweet)
    register_handler("tg_message", _retry_tg_message)
    register_handler("tg_document", _retry_tg_document)
    register_handler("gemini_call", _retry_gemini_call)


def process_queue() -> dict:
    """
    DLQ 대기열 전체 재처리.
    매 세션 시작 시 호출. (run_view / run_alert Step 0)

    Returns:
        {
          "total": 5,
          "success": 3,
          "failed": 1,
          "dead_lettered": 1,
          "remaining": 1,
        }
    """
    # 핸들러 자동 등록 (최초 1회)
    _auto_register_handlers()

    queue = _load_queue()
    if not queue:
        return {"total": 0, "success": 0, "failed": 0, "dead_lettered": 0, "remaining": 0}

    logger.info(f"[DLQ] 재처리 시작: {len(queue)}건")

    success_count = 0
    fail_count = 0
    dead_count = 0
    remaining = []
    dead_letters = _load_dead_letters()

    for item in queue:
        task_type = item.get("type", "")
        item_id = item.get("id", "?")
        retry = item.get("retry_count", 0)
        max_r = item.get("max_retries", DEFAULT_MAX_RETRIES)

        # 핸들러 없으면 그냥 유지
        handler = _HANDLERS.get(task_type)
        if not handler:
            logger.warning(f"[DLQ] 핸들러 없음: {task_type} | id={item_id} — 유지")
            remaining.append(item)
            continue

        # 재시도
        try:
            result = handler(item.get("payload", {}))
            if result:
                logger.info(f"[DLQ] 재처리 성공: {task_type} | id={item_id}")
                success_count += 1
            else:
                raise Exception("handler returned False")
        except Exception as e:
            item["retry_count"] = retry + 1
            item["last_error"] = str(e)[:300]
            item["last_retry_at"] = datetime.now(timezone.utc).isoformat()

            if item["retry_count"] >= max_r:
                # 영구 실패 → dead_letters로 이동
                logger.error(
                    f"[DLQ] 영구 실패 → dead_letters: {task_type} | id={item_id} "
                    f"| retries={item['retry_count']}"
                )
                dead_letters.append(item)
                dead_count += 1

                # TG 알림 시도
                _notify_dead_letter(item)
            else:
                logger.warning(
                    f"[DLQ] 재처리 실패 ({item['retry_count']}/{max_r}): "
                    f"{task_type} | id={item_id} | {str(e)[:80]}"
                )
                remaining.append(item)
                fail_count += 1

    _save_queue(remaining)
    if dead_count > 0:
        _save_dead_letters(dead_letters)

    result = {
        "total": len(queue),
        "success": success_count,
        "failed": fail_count,
        "dead_lettered": dead_count,
        "remaining": len(remaining),
    }
    logger.info(f"[DLQ] 재처리 완료: {result}")
    return result


def _notify_dead_letter(item: dict) -> None:
    """영구 실패 시 TG 무료 채널에 알림 발송"""
    try:
        from publishers.telegram_publisher import send_message
        msg = (
            f"❌ [DLQ] 영구 실패 알림\n\n"
            f"Type: {item.get('type', '?')}\n"
            f"ID: {item.get('id', '?')}\n"
            f"Retries: {item.get('retry_count', 0)}\n"
            f"Error: {item.get('last_error', '?')[:100]}\n"
            f"Created: {item.get('created_at', '?')}\n\n"
            f"수동 확인 필요합니다."
        )
        send_message(msg, channel="free")
        logger.info(f"[DLQ] 영구 실패 TG 알림 발송: {item.get('id')}")
    except Exception as e:
        logger.warning(f"[DLQ] TG 알림 실패 (무시): {e}")


# ──────────────────────────────────────────────────────────────
# 4. 유틸리티
# ──────────────────────────────────────────────────────────────

def get_queue_size() -> int:
    """현재 DLQ 대기 건수"""
    return len(_load_queue())


def get_dead_letter_count() -> int:
    """영구 실패 건수"""
    return len(_load_dead_letters())


def clear_queue() -> int:
    """DLQ 전체 초기화 (테스트용)"""
    q = _load_queue()
    count = len(q)
    _save_queue([])
    return count
