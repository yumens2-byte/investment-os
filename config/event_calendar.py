"""
config/event_calendar.py (B-23A)
===================================
2026년 미국 주요 경제 이벤트 캘린더

이벤트 유형:
  FOMC  — 연방공개시장위원회 금리 결정
  CPI   — 소비자물가지수 발표
  JOBS  — 고용지표 (Non-Farm Payrolls)
  GDP   — GDP 발표
  PPI   — 생산자물가지수

캐릭터 매핑:
  FOMC → The Volatician (혼돈, 예측불가)
  CPI  → Baron Bearsworth (인플레이션 = Baron의 무기)
  JOBS → Max Bullhorn (고용 = Max의 힘의 원천)
  GDP  → Max vs Baron (경제성장 = 전장의 승패)
  PPI  → Baron Bearsworth (생산자물가 = Baron의 전조)
"""

EVENT_CALENDAR_2026 = [
    # FOMC (8회)
    {"date": "2026-01-28", "type": "FOMC", "name": "FOMC 금리결정 (1월)"},
    {"date": "2026-03-18", "type": "FOMC", "name": "FOMC 금리결정 (3월)"},
    {"date": "2026-05-06", "type": "FOMC", "name": "FOMC 금리결정 (5월)"},
    {"date": "2026-06-17", "type": "FOMC", "name": "FOMC 금리결정 (6월)"},
    {"date": "2026-07-29", "type": "FOMC", "name": "FOMC 금리결정 (7월)"},
    {"date": "2026-09-16", "type": "FOMC", "name": "FOMC 금리결정 (9월)"},
    {"date": "2026-11-04", "type": "FOMC", "name": "FOMC 금리결정 (11월)"},
    {"date": "2026-12-16", "type": "FOMC", "name": "FOMC 금리결정 (12월)"},

    # CPI (12회)
    {"date": "2026-01-14", "type": "CPI", "name": "12월 CPI 발표"},
    {"date": "2026-02-11", "type": "CPI", "name": "1월 CPI 발표"},
    {"date": "2026-03-11", "type": "CPI", "name": "2월 CPI 발표"},
    {"date": "2026-04-10", "type": "CPI", "name": "3월 CPI 발표"},
    {"date": "2026-05-12", "type": "CPI", "name": "4월 CPI 발표"},
    {"date": "2026-06-10", "type": "CPI", "name": "5월 CPI 발표"},
    {"date": "2026-07-14", "type": "CPI", "name": "6월 CPI 발표"},
    {"date": "2026-08-12", "type": "CPI", "name": "7월 CPI 발표"},
    {"date": "2026-09-11", "type": "CPI", "name": "8월 CPI 발표"},
    {"date": "2026-10-13", "type": "CPI", "name": "9월 CPI 발표"},
    {"date": "2026-11-10", "type": "CPI", "name": "10월 CPI 발표"},
    {"date": "2026-12-10", "type": "CPI", "name": "11월 CPI 발표"},

    # JOBS (12회)
    {"date": "2026-01-09", "type": "JOBS", "name": "12월 고용지표"},
    {"date": "2026-02-06", "type": "JOBS", "name": "1월 고용지표"},
    {"date": "2026-03-06", "type": "JOBS", "name": "2월 고용지표"},
    {"date": "2026-04-03", "type": "JOBS", "name": "3월 고용지표"},
    {"date": "2026-05-08", "type": "JOBS", "name": "4월 고용지표"},
    {"date": "2026-06-05", "type": "JOBS", "name": "5월 고용지표"},
    {"date": "2026-07-02", "type": "JOBS", "name": "6월 고용지표"},
    {"date": "2026-08-07", "type": "JOBS", "name": "7월 고용지표"},
    {"date": "2026-09-04", "type": "JOBS", "name": "8월 고용지표"},
    {"date": "2026-10-02", "type": "JOBS", "name": "9월 고용지표"},
    {"date": "2026-11-06", "type": "JOBS", "name": "10월 고용지표"},
    {"date": "2026-12-04", "type": "JOBS", "name": "11월 고용지표"},

    # GDP (4회)
    {"date": "2026-01-29", "type": "GDP", "name": "Q4 2025 GDP (속보)"},
    {"date": "2026-04-29", "type": "GDP", "name": "Q1 2026 GDP (속보)"},
    {"date": "2026-07-30", "type": "GDP", "name": "Q2 2026 GDP (속보)"},
    {"date": "2026-10-29", "type": "GDP", "name": "Q3 2026 GDP (속보)"},
]

# 이벤트 유형별 캐릭터 매핑
EVENT_CHARACTER = {
    "FOMC": {
        "character": "The Volatician",
        "flavor": "혼돈의 마법사가 금리 투표장을 지배한다. 시장의 운명이 한 표에 달렸다.",
        "emoji": "⚡",
        "force_risk": "HIGH",
    },
    "CPI": {
        "character": "Baron Bearsworth",
        "flavor": "인플레이션은 Baron의 가장 강력한 무기. 물가가 오를수록 Baron의 힘이 커진다.",
        "emoji": "📊",
        "force_risk": None,
    },
    "JOBS": {
        "character": "Max Bullhorn",
        "flavor": "고용시장은 Max의 힘의 원천. 강한 고용이 Max에게 황금 갑옷을 입힌다.",
        "emoji": "💪",
        "force_risk": None,
    },
    "GDP": {
        "character": "Max vs Baron",
        "flavor": "경제성장은 전장의 최종 승패를 결정한다. GDP가 Max와 Baron의 운명을 가른다.",
        "emoji": "⚔️",
        "force_risk": None,
    },
    "PPI": {
        "character": "Baron Bearsworth",
        "flavor": "생산자물가는 Baron의 전조. PPI가 올라가면 CPI도 따라온다.",
        "emoji": "🏭",
        "force_risk": None,
    },
}
