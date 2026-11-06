"""임시 진단(DN2026_0105 후속 전수 검토) — YouTube RSS 404 원인 분리. 읽기 전용, 발행·DB 없음.
채널 이름·ID는 출력하지 않고 chNN 별칭만 쓴다."""
import os, re, time, urllib.request, urllib.error
import feedparser

UA = {
    "feedparser": None,  # feedparser 기본(현행 경로)
    "python-urllib": "Python-urllib/3.11",
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
}
chs = [e.split(":", 1)[1].strip() for e in os.environ["YOUTUBE_CHANNELS"].split(",") if ":" in e]
print(f"channels={len(chs)} feedparser={feedparser.__version__} utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")

def get(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua} if ua else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
            return r.status, r.headers.get("content-type", ""), body
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()
    except Exception as e:
        return type(e).__name__, "", b""

def summ(code, ct, body):
    t = re.search(rb"<title>(.*?)</title>", body[:4000], re.S)
    n = body.count(b"<entry>")
    return f"{code} ct={ct.split(';')[0] or '-'} entries={n} title={(t.group(1)[:40].decode('utf-8','replace') if t else '-')!r}"

forms = {
    "channel_id": "https://www.youtube.com/feeds/videos.xml?channel_id={c}",
    "channel_id_nowww": "https://youtube.com/feeds/videos.xml?channel_id={c}",
    "playlist_UU": "https://www.youtube.com/feeds/videos.xml?playlist_id=UU{r}",
}
for i, c in enumerate(chs[:5], 1):
    for fname, tpl in forms.items():
        url = tpl.format(c=c, r=c[2:])
        f = feedparser.parse(url)
        print(f"ch{i:02d} {fname:16s} feedparser   status={f.get('status')} entries={len(f.entries)} bozo={type(f.get('bozo_exception')).__name__ if f.get('bozo') else '-'}")
        for k in ("python-urllib", "chrome"):
            print(f"ch{i:02d} {fname:16s} {k:12s} {summ(*get(url, UA[k]))}")
        time.sleep(1)
# 비교군: YouTube 다른 공개 엔드포인트
for name, url in {"watch_home": "https://www.youtube.com/", "oembed": "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&format=json"}.items():
    print(f"ref {name:12s} chrome {summ(*get(url, UA['chrome']))}")
# 전 채널 현행 경로 1회
ok = sum(1 for c in chs if len(feedparser.parse(forms['channel_id'].format(c=c)).entries) > 0)
print(f"all_channels feedparser channel_id ok={ok}/{len(chs)}")
