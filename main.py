import os
import re
import time
import html
import json
import hashlib
import sqlite3
import psycopg
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser
import requests
from openai import OpenAI

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@sportnow0")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
MAX_POSTS_PER_CYCLE = int(os.getenv("MAX_POSTS_PER_CYCLE", "5"))
DAILY_POST_LIMIT = int(os.getenv("DAILY_POST_LIMIT", "40"))
ENABLE_NEWS_PICKS = os.getenv("ENABLE_NEWS_PICKS", "true").lower() == "true"
PREMATCH_MIN_MINUTES = int(os.getenv("PREMATCH_MIN_MINUTES", "90"))
PREMATCH_MAX_MINUTES = int(os.getenv("PREMATCH_MAX_MINUTES", "240"))
MAX_PICKS_PER_DAY = int(os.getenv("MAX_PICKS_PER_DAY", "4"))

FIRST_RUN_SKIP_EXISTING = os.getenv("FIRST_RUN_SKIP_EXISTING", "true").lower() == "true"
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DB_PATH = os.getenv("DB_PATH", "sports_news.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ENABLE_RESULT_POSTS = os.getenv("ENABLE_RESULT_POSTS", "true").lower() == "true"

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("sportnow")

SEARCHES_KO = [
    ("⚽ 축구", "축구 OR K리그 OR 국가대표 OR 손흥민"),
    ("⚾ 야구", "야구 OR KBO OR MLB"),
    ("🏀 농구", "농구 OR KBL OR NBA"),
    ("🏐 배구", "배구 OR V리그"),
    ("🥊 격투기", "UFC OR MMA OR 복싱"),
    ("🏎 모터스포츠", "F1 OR Formula 1"),
    ("🎮 e스포츠", "e스포츠 OR esports OR LCK OR 롤"),
]

SEARCHES_EN = [
    ("🌍 해외축구", '"Premier League" OR "Champions League" OR soccer transfer'),
    ("🌍 해외야구", 'MLB baseball'),
    ("🌍 해외농구", 'NBA basketball'),
    ("🌍 해외미식축구", 'NFL football'),
    ("🌍 해외아이스하키", 'NHL hockey'),
    ("🌍 해외격투기", 'UFC MMA boxing'),
    ("🌍 해외모터스포츠", '"Formula 1" OR F1'),
    ("🌍 해외종합", '"sports breaking news"'),
]

def google_news_rss(query: str, lang="ko"):
    q = quote_plus(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

FEEDS = [(cat, google_news_rss(q, "ko"), False) for cat, q in SEARCHES_KO]
FEEDS += [(cat, google_news_rss(q, "en"), True) for cat, q in SEARCHES_EN]


class DB:
    def __init__(self):
        self.pg = bool(DATABASE_URL)
        if self.pg:
            self.conn = psycopg.connect(DATABASE_URL, autocommit=False)
        else:
            self.conn = sqlite3.connect(DB_PATH)

    def execute(self, query, params=()):
        if self.pg:
            query = query.replace("?", "%s")
        return self.conn.execute(query, params)

    def commit(self):
        self.conn.commit()

def db():
    conn = DB()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_articles (
            fingerprint TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS article_cache (
            fingerprint TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT,
            summary TEXT,
            category TEXT,
            link TEXT NOT NULL,
            is_foreign INTEGER NOT NULL DEFAULT 0,
            published_at TEXT,
            cached_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pick_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matchup_key TEXT NOT NULL,
            pick_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """ if not conn.pg else """
        CREATE TABLE IF NOT EXISTS pick_logs (
            id BIGSERIAL PRIMARY KEY,
            matchup_key TEXT NOT NULL,
            pick_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prematch_picks (
            event_id TEXT PRIMARY KEY,
            league TEXT NOT NULL,
            sport TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            pick_team TEXT NOT NULL,
            probability INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            start_utc TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            result_status TEXT NOT NULL DEFAULT 'pending',
            home_score INTEGER,
            away_score INTEGER,
            winner_team TEXT,
            settled_at TEXT,
            result_posted INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    return conn

def fingerprint(title: str, link: str) -> str:
    raw = f"{title.strip().lower()}|{link.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def normalize_words(title):
    return set(re.findall(r"[0-9A-Za-z가-힣]{2,}", title.lower()))

def title_similarity(a, b):
    aa, bb = normalize_words(a), normalize_words(b)
    return len(aa & bb) / len(aa | bb) if aa and bb else 0.0

def is_similar_recent(conn, title):
    cutoff = _iso_hours_ago(18)
    rows = conn.execute(
        "SELECT title FROM sent_articles WHERE sent_at >= ? ORDER BY sent_at DESC LIMIT 250",
        (cutoff,)
    ).fetchall()
    return any(title_similarity(title, r[0]) >= 0.58 for r in rows)

def posts_last_24h(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM sent_articles WHERE datetime(sent_at) >= datetime('now','-24 hours')"
    ).fetchone()[0]

def importance_score(item):
    t=item["title"].lower()
    keys=["속보","공식","확정","이적","영입","부상","선발","라인업","우승","신기록","계약",
          "breaking","official","confirmed","transfer","trade","injury","lineup","champion","record","contract"]
    return sum(3 for k in keys if k in t)

def already_sent(conn, fp: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sent_articles WHERE fingerprint=? LIMIT 1", (fp,)
    ).fetchone() is not None

def mark_sent(conn, fp: str, title: str, link: str):
    conn.execute(
        "INSERT INTO sent_articles (fingerprint, title, link, sent_at) VALUES (?, ?, ?, ?) ON CONFLICT (fingerprint) DO NOTHING"
        if conn.pg else
        "INSERT OR IGNORE INTO sent_articles VALUES (?, ?, ?, ?)",
        (fp, title, link, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

def _iso_hours_ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

def posts_last_24h(conn):
    cutoff = _iso_hours_ago(24)
    return conn.execute(
        "SELECT COUNT(*) FROM sent_articles WHERE sent_at >= ?",
        (cutoff,)
    ).fetchone()[0]

def picks_last_24h(conn):
    cutoff = _iso_hours_ago(24)
    return conn.execute(
        "SELECT COUNT(*) FROM pick_logs WHERE created_at >= ?",
        (cutoff,)
    ).fetchone()[0]

def pick_recently_posted(conn, matchup_key):
    cutoff = _iso_hours_ago(24)
    row = conn.execute(
        "SELECT 1 FROM pick_logs WHERE matchup_key=? AND created_at >= ? LIMIT 1",
        (matchup_key, cutoff)
    ).fetchone()
    return row is not None

def strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def clean_source(entry):
    source = ""
    try:
        source = entry.get("source", {}).get("title", "")
    except Exception:
        pass
    title = entry.get("title", "").strip()
    if not source and " - " in title:
        source = title.rsplit(" - ", 1)[-1].strip()
    return source or "Google News"

def clean_title(entry):
    title = html.unescape(entry.get("title", "").strip())
    source = clean_source(entry)
    suffix = f" - {source}"
    if title.endswith(suffix):
        title = title[:-len(suffix)].strip()
    return title

def article_context(entry):
    # RSS에 포함된 짧은 설명만 사용. 기사 본문 전체를 복사하지 않음.
    summary = strip_tags(entry.get("summary", ""))
    return summary[:1800]

def ai_translate_and_summarize(title, summary, source):
    prompt = f"""
너는 한국 스포츠 속보 채널의 편집자다.

아래 해외 스포츠 뉴스 정보를 한국어로 바꿔라.

규칙:
- 원문의 의미를 바꾸거나 없는 사실을 만들어내지 말 것.
- 선수명/구단명/리그명은 한국에서 흔히 쓰는 표기로 번역.
- 제목은 자연스럽고 짧은 한국어 스포츠 기사 제목으로 작성.
- 요약은 최대 2~3문장.
- RSS 설명에 정보가 부족하면 억지로 내용을 보충하지 말고 제목에서 확인되는 사실만 요약.
- 선정적 과장 표현 금지.
- 결과는 반드시 JSON 하나만 출력.

JSON 형식:
{{"title":"한국어 제목","summary":"한국어 요약"}}

출처: {source}
원문 제목: {title}
RSS 설명: {summary}
""".strip()

    response = client.responses.create(
        model=AI_MODEL,
        input=prompt
    )
    raw = response.output_text.strip()

    # 모델이 코드펜스를 붙였을 경우 제거
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw)
    return data["title"].strip(), data["summary"].strip()

def ai_summarize_korean(title, summary, source):
    # 국내 기사도 RSS 설명이 있으면 짧게 정리.
    # API 비용을 줄이고 싶다면 SUMMARIZE_KOREAN=false로 설정 가능.
    if os.getenv("SUMMARIZE_KOREAN", "true").lower() != "true":
        return title, ""

    prompt = f"""
아래 한국 스포츠 뉴스의 제목과 RSS 설명만 이용해 핵심을 최대 2문장으로 요약해라.
없는 사실은 절대 추가하지 마라.
결과는 반드시 JSON 하나만 출력해라.

{{"summary":"요약"}}

출처: {source}
제목: {title}
RSS 설명: {summary}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    raw = response.output_text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return title, data.get("summary", "").strip()


def cache_article(conn, item):
    entry = item["entry"]
    fp = fingerprint(item["title"], item["link"])
    source = clean_source(entry)
    summary = article_context(entry)
    published_at = ""
    if entry.get("published"):
        published_at = str(entry.get("published"))
    conn.execute(
        ("""INSERT INTO article_cache
           (fingerprint, title, source, summary, category, link, is_foreign, published_at, cached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (fingerprint) DO UPDATE SET
             title=EXCLUDED.title,
             source=EXCLUDED.source,
             summary=EXCLUDED.summary,
             category=EXCLUDED.category,
             link=EXCLUDED.link,
             is_foreign=EXCLUDED.is_foreign,
             published_at=EXCLUDED.published_at,
             cached_at=EXCLUDED.cached_at"""
         if conn.pg else
         """INSERT OR REPLACE INTO article_cache
           (fingerprint, title, source, summary, category, link, is_foreign, published_at, cached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
        (
            fp,
            item["title"],
            source,
            summary,
            item["category"],
            item["link"],
            1 if item["is_foreign"] else 0,
            published_at,
            datetime.now(timezone.utc).isoformat(),
        )
    )
    conn.commit()

def picks_last_24h(conn):
    return conn.execute(
        """SELECT COUNT(*) FROM pick_logs
           WHERE datetime(created_at) >= datetime('now', '-24 hours')"""
    ).fetchone()[0]

def pick_recently_posted(conn, matchup_key):
    row = conn.execute(
        """SELECT 1 FROM pick_logs
           WHERE matchup_key=? AND datetime(created_at) >= datetime('now','-24 hours')
           LIMIT 1""",
        (matchup_key,)
    ).fetchone()
    return row is not None

def recent_news_for_picks(conn, hours=48, limit=80):
    cutoff = _iso_hours_ago(hours)
    rows = conn.execute(
        """SELECT title, source, summary, category, link, is_foreign, cached_at
           FROM article_cache
           WHERE cached_at >= ?
           ORDER BY cached_at DESC
           LIMIT ?""",
        (cutoff, limit)
    ).fetchall()

    out = []
    for r in rows:
        out.append({
            "title": r[0],
            "source": r[1] or "",
            "summary": r[2] or "",
            "category": r[3] or "",
            "link": r[4],
            "is_foreign": bool(r[5]),
            "cached_at": r[6],
        })
    return out


MAJOR_LEAGUES = [
    # 축구 메이저 5대리그 + UCL
    ("soccer", "eng.1", "EPL"),
    ("soccer", "esp.1", "La Liga"),
    ("soccer", "ger.1", "Bundesliga"),
    ("soccer", "ita.1", "Serie A"),
    ("soccer", "fra.1", "Ligue 1"),
    ("soccer", "uefa.champions", "UEFA Champions League"),

    # 야구
    ("baseball", "mlb", "MLB"),

    # 농구 / 미식축구 / 아이스하키
    ("basketball", "nba", "NBA"),
    ("football", "nfl", "NFL"),
    ("hockey", "nhl", "NHL"),
]

def fetch_major_upcoming_games():
    """ESPN의 공개 scoreboard JSON에서 메이저리그 일정/시작시각만 가져온다.
    공식 유료 API 계약을 전제로 하지 않는 경량 일정 소스이므로,
    응답 실패 시 해당 리그는 조용히 건너뛴다.
    """
    now = datetime.now(timezone.utc)
    games = []

    # UTC 날짜 기준 전후 하루를 확인해 한국시간 경계의 경기를 놓치지 않음
    date_keys = {(now + timedelta(days=d)).strftime("%Y%m%d") for d in (-1, 0, 1, 2)}

    for sport, league, league_name in MAJOR_LEAGUES:
        for date_key in date_keys:
            url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
            try:
                r = requests.get(url, params={"dates": date_key}, timeout=15)
                if r.status_code != 200:
                    continue
                data = r.json()
                for ev in data.get("events", []):
                    comps = ev.get("competitions") or []
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors") or []
                    if len(competitors) < 2:
                        continue

                    start_raw = ev.get("date") or comp.get("date")
                    if not start_raw:
                        continue
                    start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                    mins = (start - now).total_seconds() / 60
                    if mins < PREMATCH_MIN_MINUTES or mins > PREMATCH_MAX_MINUTES:
                        continue

                    teams = []
                    for c in competitors:
                        t = c.get("team") or {}
                        teams.append({
                            "name": t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "",
                            "abbr": t.get("abbreviation") or "",
                            "homeAway": c.get("homeAway") or "",
                        })
                    if not all(x["name"] for x in teams):
                        continue

                    home = next((x for x in teams if x["homeAway"] == "home"), teams[0])
                    away = next((x for x in teams if x["homeAway"] == "away"), teams[1])

                    games.append({
                        "event_id": str(ev.get("id", "")),
                        "sport": sport,
                        "league": league_name,
                        "home": home["name"],
                        "away": away["name"],
                        "home_abbr": home["abbr"],
                        "away_abbr": away["abbr"],
                        "start_utc": start.isoformat(),
                        "minutes_to_start": round(mins),
                    })
            except Exception:
                log.exception("Schedule fetch failed | %s %s", sport, league)

    # 중복 이벤트 제거
    unique = {}
    for g in games:
        key = g["event_id"] or f'{g["league"]}|{g["home"]}|{g["away"]}|{g["start_utc"]}'
        unique[key] = g
    return list(unique.values())

def select_prematch_top_picks(games, news_items):
    if not games or len(news_items) < 3:
        return []

    # 기사 전체를 너무 길게 보내지 않도록 축약
    news = []
    for i, n in enumerate(news_items[:100], 1):
        news.append({
            "id": i,
            "title": n["title"],
            "summary": n["summary"][:450],
            "source": n["source"],
            "category": n["category"],
            "link": n["link"],
        })

    prompt = f"""
너는 SPORT NOW의 경기 전 뉴스 분석 엔진이다.

분석 대상 경기는 아래 '메이저리그 예정 경기'에 있는 경기만 허용된다.
잡리그나 목록에 없는 경기를 절대 추가하지 않는다.

중요:
- 제공된 최근 뉴스만 분석 근거로 사용한다.
- 외부 지식, 기억, 임의 통계, 임의 배당을 사용하지 않는다.
- 부상/결장/선발투수/라인업/휴식/징계/복귀/로스터 변경 등 직접적인 전력 정보를 가장 중요하게 본다.
- 뉴스 근거가 없는 경기는 제외한다.
- 최대 4경기만 선택한다.
- 가장 근거가 강하고 한쪽 우세가 뚜렷한 순서로 정렬한다.
- 억지로 4개를 채우지 않는다.
- probability는 '뉴스 정보만으로 추정한 상대적 우세도'다. 실제 통계 승률이 아니다.
- probability는 50~75 사이 정수만 허용한다. 근거가 약하면 경기를 제외한다.
- confidence는 high 또는 medium만 허용한다.
- 각 source_ids는 반드시 제공된 기사 ID여야 한다.
- 경기 시작시각은 games에 제공된 값을 그대로 사용한다.

JSON 배열만 출력:
[
  {{
    "event_id":"경기 ID",
    "league":"MLB",
    "matchup":"원정팀 vs 홈팀",
    "pick":"우세팀 이름",
    "probability":64,
    "confidence":"medium",
    "reasons":["근거1","근거2"],
    "source_ids":[1,5]
  }}
]

메이저리그 예정 경기:
{json.dumps(games, ensure_ascii=False)}

최근 뉴스:
{json.dumps(news, ensure_ascii=False)}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    raw = response.output_text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, list):
        return []

    game_map = {str(g["event_id"]): g for g in games}
    out = []
    for x in data[:4]:
        eid = str(x.get("event_id", ""))
        if eid not in game_map:
            continue
        try:
            prob = int(x.get("probability", 0))
        except Exception:
            continue
        if not (50 <= prob <= 75):
            continue
        if x.get("confidence") not in ("high", "medium"):
            continue
        if not x.get("source_ids") or not x.get("reasons"):
            continue
        x["_game"] = game_map[eid]
        out.append(x)

    out.sort(key=lambda x: (x["probability"], x["confidence"] == "high"), reverse=True)
    return out[:4]

def format_prematch_pick(pick, news_items):
    g = pick["_game"]
    start = datetime.fromisoformat(g["start_utc"]).astimezone(
        timezone(timedelta(hours=9))
    )
    reasons = "\n".join(
        f"• {html.escape(str(r))}" for r in pick.get("reasons", [])[:3]
    )

    sources = []
    for sid in pick.get("source_ids", [])[:3]:
        try:
            idx = int(sid) - 1
            if 0 <= idx < len(news_items):
                n = news_items[idx]
                sources.append(
                    f'• <a href="{html.escape(n["link"], quote=True)}">{html.escape(n["source"] or "관련 기사")}</a>'
                )
        except Exception:
            pass
    src = "\n".join(sources) or "• 관련 스포츠 뉴스"
    conf = "높음" if pick["confidence"] == "high" else "보통"

    return (
        f"🎯 <b>SPORT NOW 프리매치 PICK</b>\n\n"
        f"🏆 {html.escape(g['league'])}\n"
        f"🏟 <b>{html.escape(g['away'])} vs {html.escape(g['home'])}</b>\n"
        f"⏰ 경기 시작: {start.strftime('%m/%d %H:%M')} KST\n\n"
        f"✅ 뉴스 기반 우세: <b>{html.escape(str(pick['pick']))}</b>\n"
        f"📊 뉴스 기반 우세도: <b>{pick['probability']}%</b>\n"
        f"🔎 신뢰도: <b>{conf}</b>\n\n"
        f"<b>분석 근거</b>\n{reasons}\n\n"
        f"<b>관련 기사</b>\n{src}\n\n"
        f"⚠️ 위 수치는 뉴스 정보만으로 산출한 AI 추정 우세도이며 실제 통계적 승률이나 결과 보장이 아닙니다."
    )


def extract_news_based_picks(news_items):
    if len(news_items) < 5:
        return []

    compact = []
    for i, n in enumerate(news_items[:80], start=1):
        compact.append({
            "id": i,
            "category": n["category"],
            "title": n["title"],
            "source": n["source"],
            "summary": n["summary"][:500],
            "link": n["link"],
        })

    prompt = f"""
너는 스포츠 뉴스 기반 프리매치 분석 편집자다.
아래 최근 스포츠 뉴스만 근거로 분석한다. 스포츠 통계 API, 배당 API, 외부 지식은 사용하지 않는다.

목표:
- 기사에서 실제로 '곧 맞붙을 두 팀/선수'가 충분히 확인되는 경우에만 뉴스 기반 우세 픽을 만든다.
- 맞대결 자체가 기사에서 불명확하면 절대 추측해서 만들지 않는다.
- 단순 루머, 팬 의견, 과거 경기 기사만으로 픽을 만들지 않는다.
- 부상/결장/선발 변경/라인업/휴식/징계/복귀/감독의 출전 관련 발언처럼 경기력에 직접 연결되는 사실을 우선한다.
- '분위기가 좋다', '연승 중이다' 같은 표현만으로 강한 결론을 내리지 않는다.
- 숫자 승률은 만들지 않는다.
- 결과 보장은 절대 표현하지 않는다.
- 신뢰도는 high / medium / low 중 하나. low는 출력하지 말고 버린다.
- 한 번에 최대 3개.
- 같은 뉴스 사건을 반복하지 않는다.
- 반드시 제공된 기사만 인용 근거로 삼는다.

JSON 배열만 출력:
[
  {{
    "matchup_key":"팀A_vs_팀B",
    "sport":"야구",
    "matchup":"팀A vs 팀B",
    "lean":"팀A 우세",
    "confidence":"medium",
    "reasons":["근거1","근거2"],
    "source_ids":[1,4]
  }}
]

최근 기사:
{json.dumps(compact, ensure_ascii=False)}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    raw = response.output_text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)

    if not isinstance(data, list):
        return []

    good = []
    for p in data[:3]:
        if p.get("confidence") not in ("high", "medium"):
            continue
        if not p.get("matchup_key") or not p.get("matchup") or not p.get("lean"):
            continue
        if len(p.get("source_ids", [])) < 1:
            continue
        good.append(p)
    return good

def format_pick_post(pick, news_items):
    reasons = pick.get("reasons", [])[:3]
    reason_text = "\n".join(f"• {html.escape(str(r))}" for r in reasons)

    src_lines = []
    for sid in pick.get("source_ids", [])[:3]:
        try:
            idx = int(sid) - 1
            if 0 <= idx < len(news_items):
                n = news_items[idx]
                src_lines.append(
                    f'• <a href="{html.escape(n["link"], quote=True)}">{html.escape(n["source"] or "원문")}</a>'
                )
        except Exception:
            pass

    src_text = "\n".join(src_lines) if src_lines else "• 최근 스포츠 뉴스"

    conf = "높음" if pick.get("confidence") == "high" else "보통"

    return (
        f"🎯 <b>SPORT NOW 뉴스 기반 픽</b>\n\n"
        f"🏟 <b>{html.escape(pick.get('matchup',''))}</b>\n"
        f"📌 뉴스 흐름 우세: <b>{html.escape(pick.get('lean',''))}</b>\n"
        f"🔎 신뢰도: <b>{conf}</b>\n\n"
        f"<b>근거</b>\n{reason_text}\n\n"
        f"<b>관련 기사</b>\n{src_text}\n\n"
        f"⚠️ 기사 정보만 반영한 참고용 분석이며 결과를 보장하지 않습니다."
    )

def maybe_post_news_picks(conn):
    if not ENABLE_NEWS_PICKS:
        return
    if picks_last_24h(conn) >= MAX_PICKS_PER_DAY:
        return

    games = fetch_major_upcoming_games()
    if not games:
        log.info("No major games in prematch window")
        return

    # 이미 게시한 경기 제거
    fresh_games = []
    for g in games:
        key = f"event:{g['event_id']}"
        if not pick_recently_posted(conn, key):
            fresh_games.append(g)
    if not fresh_games:
        return

    news_items = recent_news_for_picks(conn, hours=48, limit=100)
    try:
        picks = select_prematch_top_picks(fresh_games, news_items)
    except Exception:
        log.exception("Prematch pick generation failed")
        return

    remaining = MAX_PICKS_PER_DAY - picks_last_24h(conn)
    for p in picks[:remaining]:
        g = p["_game"]
        key = f"event:{g['event_id']}"
        try:
            text = format_prematch_pick(p, news_items)
            send_telegram(text)
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO pick_logs (matchup_key, pick_text, created_at) VALUES (?, ?, ?)",
                (key, text, now_iso)
            )
            conn.execute(
                """INSERT INTO prematch_picks
                   (event_id, league, sport, home_team, away_team, pick_team,
                    probability, confidence, start_utc, posted_at, result_status, result_posted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)
                   ON CONFLICT (event_id) DO NOTHING""",
                (
                    str(g["event_id"]), g["league"], g["sport"], g["home"], g["away"],
                    str(p.get("pick","")), int(p.get("probability", 0)),
                    str(p.get("confidence","medium")), g["start_utc"], now_iso
                )
            )
            conn.commit()
            log.info("PREMATCH PICK POSTED | %s | %s", p.get("matchup"), p.get("pick"))
            time.sleep(2)
        except Exception:
            log.exception("Prematch pick send failed | %s", key)

def fetch_event_result(event_id, sport, league_name):
    league_map = {
        "EPL": ("soccer", "eng.1"),
        "La Liga": ("soccer", "esp.1"),
        "Bundesliga": ("soccer", "ger.1"),
        "Serie A": ("soccer", "ita.1"),
        "Ligue 1": ("soccer", "fra.1"),
        "UEFA Champions League": ("soccer", "uefa.champions"),
        "MLB": ("baseball", "mlb"),
        "NBA": ("basketball", "nba"),
        "NFL": ("football", "nfl"),
        "NHL": ("hockey", "nhl"),
    }
    pair = league_map.get(league_name)
    if not pair:
        return None

    sp, lg = pair
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sp}/{lg}/summary"
    try:
        r = requests.get(url, params={"event": event_id}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        header = data.get("header") or {}
        comps = header.get("competitions") or []
        if not comps:
            return None
        comp = comps[0]
        status = ((comp.get("status") or {}).get("type") or {})
        if not status.get("completed"):
            return None

        home = away = None
        for c in comp.get("competitors", []):
            team = (c.get("team") or {}).get("displayName") or (c.get("team") or {}).get("name") or ""
            try:
                score = int(float(c.get("score", 0)))
            except Exception:
                score = 0
            if c.get("homeAway") == "home":
                home = (team, score, c.get("winner", False))
            elif c.get("homeAway") == "away":
                away = (team, score, c.get("winner", False))

        if not home or not away:
            return None

        winner = home[0] if home[2] else away[0] if away[2] else ""
        return {
            "home_team": home[0],
            "away_team": away[0],
            "home_score": home[1],
            "away_score": away[1],
            "winner_team": winner,
            "draw": not winner,
        }
    except Exception:
        log.exception("Result fetch failed | %s", event_id)
        return None

def result_stats(conn):
    rows = conn.execute(
        """SELECT result_status, settled_at
           FROM prematch_picks
           WHERE result_status IN ('hit','miss','push')"""
    ).fetchall()

    total_hit = total_miss = 0
    today_hit = today_miss = 0
    cutoff = _iso_hours_ago(24)

    for status, settled_at in rows:
        if status == "hit":
            total_hit += 1
            if settled_at and settled_at >= cutoff:
                today_hit += 1
        elif status == "miss":
            total_miss += 1
            if settled_at and settled_at >= cutoff:
                today_miss += 1

    return {
        "today_hit": today_hit,
        "today_miss": today_miss,
        "total_hit": total_hit,
        "total_miss": total_miss,
    }

def format_result_post(row, result, final_status, stats):
    event_id, league, sport, home_team, away_team, pick_team, probability = row
    icon = "✅ 적중" if final_status == "hit" else "❌ 미적중" if final_status == "miss" else "➖ 무승부/적중판정 제외"

    today_total = stats["today_hit"] + stats["today_miss"]
    total_total = stats["total_hit"] + stats["total_miss"]
    today_rate = (stats["today_hit"] / today_total * 100) if today_total else 0
    total_rate = (stats["total_hit"] / total_total * 100) if total_total else 0

    return (
        f"🏁 <b>SPORT NOW PICK RESULT</b>\n\n"
        f"🏆 {html.escape(league)}\n"
        f"🏟 <b>{html.escape(away_team)} {result['away_score']} : "
        f"{result['home_score']} {html.escape(home_team)}</b>\n\n"
        f"🎯 사전 PICK: <b>{html.escape(pick_team)}</b>\n"
        f"📊 뉴스 기반 우세도: <b>{probability}%</b>\n"
        f"📌 결과: <b>{icon}</b>\n\n"
        f"📈 최근 24시간: {stats['today_hit']}승 {stats['today_miss']}패 "
        f"({today_rate:.1f}%)\n"
        f"📚 누적: {stats['total_hit']}승 {stats['total_miss']}패 "
        f"({total_rate:.1f}%)\n\n"
        f"⚠️ 뉴스 기반 AI 분석 기록이며 결과를 보장하지 않습니다."
    )

def settle_finished_picks(conn):
    if not ENABLE_RESULT_POSTS:
        return

    cutoff = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT event_id, league, sport, home_team, away_team, pick_team, probability
           FROM prematch_picks
           WHERE result_status='pending' AND start_utc <= ?
           ORDER BY start_utc ASC
           LIMIT 20""",
        (cutoff,)
    ).fetchall()

    for row in rows:
        event_id, league, sport, home_team, away_team, pick_team, probability = row
        result = fetch_event_result(event_id, sport, league)
        if not result:
            continue

        if result["draw"]:
            status = "push"
        else:
            # Normalize for minor naming differences
            def norm(x):
                return re.sub(r"[^a-z0-9가-힣]", "", (x or "").lower())
            status = "hit" if (
                norm(pick_team) in norm(result["winner_team"]) or
                norm(result["winner_team"]) in norm(pick_team)
            ) else "miss"

        settled = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE prematch_picks
               SET result_status=?, home_score=?, away_score=?, winner_team=?,
                   settled_at=?, result_posted=0
               WHERE event_id=?""",
            (
                status, result["home_score"], result["away_score"],
                result["winner_team"], settled, event_id
            )
        )
        conn.commit()

        stats = result_stats(conn)
        text = format_result_post(row, result, status, stats)

        try:
            send_telegram(text)
            conn.execute(
                "UPDATE prematch_picks SET result_posted=1 WHERE event_id=?",
                (event_id,)
            )
            conn.commit()
            log.info("RESULT POSTED | %s | %s", event_id, status)
            time.sleep(2)
        except Exception:
            log.exception("Result post failed | %s", event_id)


def seed_existing(conn):
    """첫 실행 시 현재 RSS의 기존 기사를 전송하지 않고 DB에 기록한다."""
    items = collect_entries()
    for item in items:
        try:
            cache_article(conn, item)
        except Exception:
            log.exception("Article cache failed during seed: %s", item["title"])

        fp = fingerprint(item["title"], item["link"])
        mark_sent(conn, fp, item["title"], item["link"])

    log.info("첫 실행: 기존 기사 %d건 스킵 처리", len(items))


def main():
    conn = db()

    row_count = conn.execute("SELECT COUNT(*) FROM sent_articles").fetchone()[0]
    if row_count == 0 and FIRST_RUN_SKIP_EXISTING:
        seed_existing(conn)

    log.info(
        "SportNow v2 started | channel=%s | interval=%ss | model=%s",
        CHANNEL_ID, CHECK_INTERVAL, AI_MODEL
    )

    while True:
        try:
            run_cycle(conn)
            maybe_post_news_picks(conn)
            settle_finished_picks(conn)
        except Exception:
            log.exception("Cycle error")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
