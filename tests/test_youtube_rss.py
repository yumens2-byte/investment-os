"""DN2026_0101 (2026-11-06): C-16 유튜버 RSS 수집 관측 강화 테스트.

외부 호출 없음(STD-TEST-14): feedparser.parse 를 가짜로 바꾸고, 문자열 XML 을 실제 feedparser 로 파싱한다.
픽스처: tests/fixtures/youtube_rss_20261106.json — 11-06 실측 15개 채널의 발행 시각 분포(최신 3개, STD-CI-11).
  채널 이름·ID·제목은 가명(실제 Secret 내용 미포함).
XML 구조는 같은 날 실측한 YouTube Atom 피드(yt·media 네임스페이스, media:group 안 description)를 따른다.
"""
import json
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import feedparser
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collectors.youtube_rss as yr
from config.streamer_list import RSS_BASE

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "youtube_rss_20261106.json"
_REAL_PARSE = feedparser.parse


def _atom(name: str, cid: str, entries: list) -> str:
    body = "".join(
        f"""<entry><id>yt:video:v{i}</id><yt:videoId>v{i}</yt:videoId><yt:channelId>{cid}</yt:channelId>
<title>{escape(e['title'])}</title><link rel="alternate" href="https://www.youtube.com/watch?v=v{i}"/>
<author><name>{escape(name)}</name></author><published>{e['published']}</published><updated>{e['published']}</updated>
<media:group><media:title>{escape(e['title'])}</media:title>
<media:description>{escape(e.get('description', ''))}</media:description></media:group></entry>"""
        for i, e in enumerate(entries)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">'
        f"<title>{escape(name)}</title>{body}</feed>"
    )


def _parsed(xml: str, status=200, ctype="text/xml; charset=UTF-8"):
    r = _REAL_PARSE(xml)
    r["status"] = status
    r["headers"] = {"content-type": ctype}
    return r


class _FixedDT(datetime):
    NOW = datetime(2026, 11, 6, 1, 45, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.NOW


@pytest.fixture
def env(monkeypatch):
    """채널 목록·응답을 주입하는 헬퍼. responses: {channel_id: FeedParserDict | Exception}"""
    monkeypatch.setattr(yr, "datetime", _FixedDT)
    calls = []

    def setup(channels: list, responses: dict):
        monkeypatch.setenv("YOUTUBE_CHANNELS", ",".join(f"{n}:{c}" for n, c in channels))

        def fake_parse(url, *a, **k):
            calls.append(url)
            res = responses[url[len(RSS_BASE):]]
            if isinstance(res, Exception):
                raise res
            return res

        monkeypatch.setattr(feedparser, "parse", fake_parse)
        return calls

    return setup


def test_version_bumped():
    assert yr.VERSION.startswith("1.1.")


def test_ok_and_48h_filter(env):
    entries = [
        {"title": "신규1", "published": "2026-11-05T20:00:00+00:00", "description": "d1"},
        {"title": "신규2", "published": "2026-11-04T02:00:00+00:00", "description": ""},
        {"title": "48시간 밖", "published": "2026-11-04T01:00:00+00:00", "description": "x"},
    ]
    env([("A", "UCa")], {"UCa": _parsed(_atom("A", "UCa", entries))})
    r = yr.collect_youtube_summaries()
    assert r["success"] is True and r["total"] == 2 and r["error"] is None
    assert [v["title"] for v in r["videos"]] == ["신규1", "신규2"]
    assert r["videos"][0]["description"] == "d1"
    assert r["stats"]["ok"] == 1 and r["stats"]["channels"] == 1


def test_http_404_continues_to_next_channel(env, caplog):
    ok = _parsed(_atom("B", "UCb", [{"title": "t", "published": "2026-11-05T00:00:00+00:00"}]))
    env([("A", "UCa"), ("B", "UCb")], {
        "UCa": _parsed("<html><body>404</body></html>", status=404, ctype="text/html"),
        "UCb": ok,
    })
    caplog.set_level("INFO")
    r = yr.collect_youtube_summaries()
    assert r["total"] == 1 and r["stats"]["http_error"] == 1 and r["stats"]["ok"] == 1
    assert "A: http_error status=404" in caplog.text


def test_redirect_status_with_entries_is_ok(env):
    feed = _parsed(_atom("A", "UCa", [{"title": "t", "published": "2026-11-05T00:00:00+00:00"}]), status=301)
    env([("A", "UCa")], {"UCa": feed})
    r = yr.collect_youtube_summaries()
    assert r["total"] == 1 and r["stats"]["ok"] == 1 and r["stats"]["http_error"] == 0


def test_html_200_is_empty_feed(env, caplog):
    env([("A", "UCa")], {"UCa": _parsed("<html><body>consent</body></html>", ctype="text/html")})
    caplog.set_level("INFO")
    r = yr.collect_youtube_summaries()
    assert r["stats"]["empty_feed"] == 1
    assert "A: empty_feed status=200 ctype=text/html" in caplog.text


def test_parse_error_is_separate(env):
    env([("A", "UCa")], {"UCa": _parsed('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title><broken')})
    r = yr.collect_youtube_summaries()
    assert r["stats"]["parse_error"] == 1 and r["stats"]["empty_feed"] == 0


def test_stale_is_not_failure(env, capsys):
    old = [{"title": "old", "published": "2026-11-01T00:00:00+00:00"}]
    env([("A", "UCa")], {"UCa": _parsed(_atom("A", "UCa", old))})
    r = yr.collect_youtube_summaries()
    assert r["success"] is False and r["error"] == yr.ERROR_NO_RECENT
    assert r["stats"]["stale"] == 1
    assert "::warning" not in capsys.readouterr().out


def test_all_channels_failed_warns(env, capsys, caplog):
    env([("A", "UCa"), ("B", "UCb"), ("C", "UCc")], {
        "UCa": _parsed("<html/>", status=404, ctype="text/html"),
        "UCb": _parsed("<html/>", ctype="text/html"),
        "UCc": RuntimeError("t"),
    })
    caplog.set_level("INFO")
    r = yr.collect_youtube_summaries()
    assert r["success"] is False and r["error"] == yr.ERROR_ALL_FAILED
    assert r["stats"] == {"channels": 3, "ok": 0, "stale": 0, "empty_feed": 1,
                          "http_error": 1, "parse_error": 0, "fetch_error": 1, "entry_error": 0}
    out = capsys.readouterr().out
    assert out.count("::warning title=YouTubeRSS::all_channels_failed") == 1
    assert "youtube.com" not in out and "UCa" not in out  # 공개 화면에 URL·ID 미노출
    assert "C: fetch_error RuntimeError" in caplog.text


def test_channels_not_set(monkeypatch):
    monkeypatch.delenv("YOUTUBE_CHANNELS", raising=False)
    r = yr.collect_youtube_summaries()
    assert r == {"success": False, "videos": [], "total": 0, "error": "YOUTUBE_CHANNELS 미설정"}


def test_real_sample_20261106(env):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["channels"]
    calls = env(
        [(c["name"], c["channel_id"]) for c in data],
        {c["channel_id"]: _parsed(_atom(c["name"], c["channel_id"], c["entries"])) for c in data},
    )
    r = yr.collect_youtube_summaries()
    assert len(calls) == 15
    assert r["success"] is True and r["total"] == 36
    assert r["stats"]["ok"] == 14 and r["stats"]["stale"] == 1
    assert sum(r["stats"][k] for k in yr.FAIL_REASONS) == 0
    assert not any(v["channel"].endswith("_stale") for v in r["videos"])
    # 반환 videos 형식 불변(streamer_analyzer 입력 계약)
    assert set(r["videos"][0]) == {"channel", "title", "description", "published"}


def test_urlerror_in_bozo_is_fetch_error(env, caplog):
    """R6-01: feedparser 6.0.11 은 연결 실패를 예외 대신 bozo_exception=URLError 로 돌려준다."""
    feed = _REAL_PARSE("http://127.0.0.1:1/feed")  # 로컬 거부 포트 — 외부 호출 아님
    assert feed.bozo and isinstance(feed.bozo_exception, urllib.error.URLError)
    env([("A", "UCa")], {"UCa": feed})
    caplog.set_level("INFO")
    r = yr.collect_youtube_summaries()
    assert r["stats"]["fetch_error"] == 1 and r["stats"]["parse_error"] == 0
    assert r["error"] == yr.ERROR_ALL_FAILED
    assert "A: fetch_error status=None ctype=- bozo=URLError" in caplog.text


def test_entry_error_is_contained_per_channel(env, monkeypatch, caplog):
    """R6-02: 항목 처리 예외는 해당 채널만 entry_error 로 집계하고 다음 채널을 계속 수집한다."""
    a = _parsed(_atom("A", "UCa", [{"title": "a", "published": "2026-11-05T00:00:00+00:00"}]))
    b = _parsed(_atom("B", "UCb", [{"title": "b", "published": "2026-11-05T00:00:00+00:00"}]))
    env([("A", "UCa"), ("B", "UCb")], {"UCa": a, "UCb": b})
    real = yr._extract_description

    def boom(entry):
        if entry.get("title") == "a":
            raise ValueError("x")
        return real(entry)

    monkeypatch.setattr(yr, "_extract_description", boom)
    caplog.set_level("INFO")
    r = yr.collect_youtube_summaries()
    assert [v["title"] for v in r["videos"]] == ["b"]
    assert r["stats"]["entry_error"] == 1 and r["stats"]["ok"] == 1
    assert "A: entry_error ValueError" in caplog.text
