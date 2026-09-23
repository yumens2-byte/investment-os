"""
gen/hero_shorts/ledger.py — 노션 EDT 에피소드 트래커(DB) 직독 로더
=================================================================
역할:
    1. EDT 에피소드 트래커 DB에서 회차(번호) 기준 행을 조회한다.
    2. 동일 번호 다중 행이 있으면 논리적 폐기·미정본을 제외하고 최신 정본 1행을 고른다.
    3. DB 행을 cutplanner가 쓰는 arc_state 구조로 정규화한다.

설계 전환(2026-09-23):
    - 기존 인덱스 페이지(D-6) → 개별 페이지 읽기 구조는 운영 의도와 불일치했다.
    - 이제 SSOT는 `EDT 에피소드 트래커` DB 자체다.
    - 세션/개발 환경: gsk Notion MCP query_data_sources(SQL) 사용.
    - Actions/서버 환경: 공식 Notion REST API(data source query) 사용.

주의:
    - 트래커 DB에는 동일 번호 다중 행이 존재할 수 있다(Ep86 실측: 논리적 폐기 행 + ACT2 정본 행).
      => `특이사항`의 폐기 마커와 `발행 상태`를 함께 보아 정본 행을 선택한다.
    - 트래커의 `메인 히어로` 표기는 캐릭터 캐논의 최종명과 다를 수 있다.
      => 현재 cutplanner는 이 필드를 직접 쓰지 않고, 히어로 캐논은 canon.py가 책임진다.
"""
import json
import logging
import os
import re
import subprocess

log = logging.getLogger("hero_shorts.ledger")

TRACKER_DB_ID = os.getenv("HERO_SHORTS_TRACKER_DB_ID", "32a9e71588b843e1a650d2c9c87d1d9f")
TRACKER_VIEW_URL = os.getenv(
    "HERO_SHORTS_TRACKER_VIEW_URL",
    "https://app.notion.com/p/32a9e71588b843e1a650d2c9c87d1d9f?v=741c74121afe4b6da48fa89d688e1fa6&source=copy_link",
)
TRACKER_DATA_SOURCE_ID = os.getenv("HERO_SHORTS_TRACKER_DATA_SOURCE_ID", "bdc5e21c-58eb-40f9-b659-8c6d33fd0dae")
TRACKER_DATA_SOURCE_URL = f"collection://{TRACKER_DATA_SOURCE_ID}"
DISCARD_MARKERS = ("논리적 폐기", "폐기됨", "폐기 ")


def _notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2026-03-11",
        "Content-Type": "application/json",
    }


def _rich_text_plain(items):
    if not items:
        return None
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(item.get("plain_text") or item.get("text", {}).get("content") or "")
        else:
            out.append(str(item))
    text = "".join(out).strip()
    return text or None


def _extract_plain_value(prop):
    """Notion page property → Python 원시값.

    트래커에서 쓰는 대표 타입(title/rich_text/select/status/number/date 등)만
    손실 없이 꺼내면 충분하다.
    """
    if not isinstance(prop, dict):
        return prop
    ptype = prop.get("type")
    if ptype == "title":
        return _rich_text_plain(prop.get("title"))
    if ptype == "rich_text":
        return _rich_text_plain(prop.get("rich_text"))
    if ptype == "number":
        return prop.get("number")
    if ptype == "status":
        v = prop.get("status") or {}
        return v.get("name")
    if ptype == "select":
        v = prop.get("select") or {}
        return v.get("name")
    if ptype == "multi_select":
        vals = [v.get("name") for v in prop.get("multi_select", []) if v.get("name")]
        return ", ".join(vals) if vals else None
    if ptype == "date":
        v = prop.get("date") or {}
        return v.get("start")
    if ptype == "url":
        return prop.get("url")
    if ptype == "checkbox":
        return prop.get("checkbox")
    if ptype == "people":
        vals = [v.get("name") for v in prop.get("people", []) if v.get("name")]
        return ", ".join(vals) if vals else None
    if ptype == "formula":
        v = prop.get("formula") or {}
        ftype = v.get("type")
        return v.get(ftype) if ftype else None
    if ptype == "relation":
        vals = [v.get("id") for v in prop.get("relation", []) if v.get("id")]
        return vals or None
    return prop.get(ptype)


def _page_to_row(page):
    props = page.get("properties") or {}
    row = {
        "id": page.get("id"),
        "url": page.get("url"),
        "createdTime": page.get("created_time"),
    }
    for name, prop in props.items():
        row[name] = _extract_plain_value(prop)
    return row


def _query_rows_notion_api(ep_number, token):
    """Actions 경로 — 공식 REST API로 트래커 data source 질의."""
    import urllib.error
    import urllib.request

    url = f"https://api.notion.com/v1/data-sources/{TRACKER_DATA_SOURCE_ID}/query"
    body = {
        "filter": {"property": "번호", "number": {"equals": int(ep_number)}},
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": 10,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_notion_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(
            f"트래커 DB query 실패(HTTP {e.code}) — NOTION_API_KEY/NOTION_TOKEN 또는 DB 공유 권한 확인: {detail}"
        ) from e
    rows = [_page_to_row(p) for p in data.get("results", [])]
    log.info("트래커 DB query(API): Ep%d → %d행", ep_number, len(rows))
    return rows


def _parse_gsk_query_rows(stdout):
    outer = json.loads(stdout)
    if outer.get("status") != "ok":
        raise RuntimeError(outer.get("message") or "gsk notion query_data_sources 실패")
    payload = outer.get("data")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "")
        raise RuntimeError(msg[:500])
    text = payload.get("content", [{}])[0].get("text", "{}")
    return json.loads(text).get("results", [])


def _query_rows_via_gsk(ep_number):
    """세션/개발 경로 — Notion MCP의 query_data_sources(SQL) 사용."""
    query = (
        f'SELECT * FROM "{TRACKER_DATA_SOURCE_URL}" '
        f'WHERE 번호 = {int(ep_number)} ORDER BY createdTime DESC'
    )
    args = {
        "action": "notion-query-data-sources",
        "args": json.dumps(
            {
                "data": {
                    "mode": "sql",
                    "data_source_urls": [TRACKER_DATA_SOURCE_URL],
                    "query": query,
                }
            },
            ensure_ascii=False,
        ),
    }
    cmd = ["gsk", "connector", "call", "notion", "-t", "call", "-a", json.dumps(args, ensure_ascii=False)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError(
            "gsk CLI 부재 + NOTION_API_KEY/NOTION_TOKEN 미설정 — 트래커 DB 직독 불가"
        ) from e
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout or "gsk notion query 실패")[:500])
    rows = _parse_gsk_query_rows(out.stdout)
    log.info("트래커 DB query(gsk): Ep%d → %d행", ep_number, len(rows))
    return rows


def query_tracker_rows(ep_number):
    token = (
        os.getenv("NOTION_API_TOKEN", "")
        or os.getenv("NOTION_TOKEN", "")
        or os.getenv("NOTION_API_KEY", "")
    )
    if token:
        return _query_rows_notion_api(ep_number, token)
    return _query_rows_via_gsk(ep_number)



def _is_discarded(row):
    note = str(row.get("특이사항") or "")
    return any(m in note for m in DISCARD_MARKERS)


def choose_canonical_row(rows):
    """동일 번호 다중 행 중 정본 1행 선택.

    우선순위:
      1) 논리적 폐기 아님
      2) 발행 상태 == 완료
      3) createdTime 최신
    """
    if not rows:
        raise KeyError("트래커 DB 해당 회차 없음")
    candidates = [r for r in rows if not _is_discarded(r)] or list(rows)
    completed = [r for r in candidates if str(r.get("발행 상태") or "") == "완료"] or candidates
    best = sorted(completed, key=lambda r: str(r.get("createdTime") or ""), reverse=True)[0]
    log.info(
        "트래커 정본 선택: Ep%s → row=%s created=%s status=%s discarded=%s",
        best.get("번호"), best.get("id"), best.get("createdTime"),
        best.get("발행 상태"), _is_discarded(best),
    )
    return best


def _split_names(value):
    if not value:
        return []
    text = str(value).replace("+", ",")
    parts = [p.strip() for p in re.split(r",|/|\|", text) if p.strip()]
    return parts


def _normalize_title(ep_number, title):
    if not title:
        return f"Ep{ep_number}"
    text = str(title).strip()
    text = re.sub(rf"^Ep\s*0*{int(ep_number)}\s*[—\-:：]?\s*", "", text).strip()
    text = text.strip("「」").strip()
    return text or f"Ep{ep_number}"


def row_to_episode(row):
    ep_number = int(row.get("번호"))
    title = _normalize_title(ep_number, row.get("에피소드"))
    villains = _split_names(row.get("활성 빌런"))
    ep = {
        "episode": f"Ep{ep_number}",
        "title": title,
        "date": row.get("date:발행일:start"),
        "type": row.get("에피소드 타입"),
        "outcome": row.get("전투 결과"),
        "source": {
            "kind": "tracker_db",
            "database_id": TRACKER_DB_ID,
            "data_source_id": TRACKER_DATA_SOURCE_ID,
            "row_id": row.get("id"),
            "row_url": row.get("url"),
            "created_time": row.get("createdTime"),
        },
        "arc_state": {
            "active_villains": villains,
            "arc_day": row.get("Arc Day"),
            "battle_balance": row.get("Battle Balance"),
            "arc_tension": row.get("arc_tension"),
            "main_hero_tracker": row.get("메인 히어로"),
            "special_notes": row.get("특이사항"),
            "publish_state": row.get("발행 상태"),
        },
        "next_episode": {"number": f"Ep{ep_number + 1}"},
    }
    if row.get("메인 히어로") == "Guardian of Capital":
        log.warning("트래커 주인공 표기 'Guardian of Capital' 감지 — 캐릭터 캐논은 canon.py(EDT) 우선")
    return ep


def load_episode(ep_number):
    """회차 번호 → 트래커 DB 정본 행 → cutplanner 입력 arc_state."""
    rows = query_tracker_rows(ep_number)
    row = choose_canonical_row(rows)
    ep = row_to_episode(row)
    log.info("Ep%d 로드 OK(DB): %s / %s", ep_number, ep.get("title"), row.get("id"))
    return ep
