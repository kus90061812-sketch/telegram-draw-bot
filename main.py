import os
import re
import time
import html
import json
import hashlib
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
import psycopg
from openai import OpenAI


# =========================
# ENV
# =========================
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@sportnow0")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
FIRST_RUN_SKIP_EXISTING = os.getenv("FIRST_RUN_SKIP_EXISTING", "true").lower() == "true"
SUMMARIZE_KOREAN = os.getenv("SUMMARIZE_KOREAN", "true").lower() == "true"
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

ENABLE_NEWS_PICKS = os.getenv("ENABLE_NEWS_PICKS", "true").lower() == "true"
ENABLE_FREE_TEAM_DATA = os.getenv("ENABLE_FREE_TEAM_DATA", "true").lower() == "true"
RECENT_GAMES_LOOKBACK = int(os.getenv("RECENT_GAMES_LOOKBACK", "5"))

ENABLE_RESULT_POSTS = os.getenv("ENABLE_RESULT_POSTS", "true").lower() == "true"
POST_NEWS_PUBLICLY = os.getenv("POST_NEWS_PUBLICLY", "false").lower() == "true"
BASEBALL_STARTER_WEIGHT = float(os.getenv("BASEBALL_STARTER_WEIGHT", "0.30"))
BASEBALL_OFFENSE_WEIGHT = float(os.getenv("BASEBALL_OFFENSE_WEIGHT", "0.25"))
BASEBALL_BULLPEN_WEIGHT = float(os.getenv("BASEBALL_BULLPEN_WEIGHT", "0.20"))
BASEBALL_FORM_WEIGHT = float(os.getenv("BASEBALL_FORM_WEIGHT", "0.15"))
BASEBALL_LINEUP_WEIGHT = float(os.getenv("BASEBALL_LINEUP_WEIGHT", "0.10"))
LINEUP_MIN_PLAYERS = int(os.getenv("LINEUP_MIN_PLAYERS", "7"))
ENABLE_ASIA_LINEUP_FALLBACK = os.getenv("ENABLE_ASIA_LINEUP_FALLBACK", "true").lower() == "true"
KBO_NAVER_GATEWAY = os.getenv("KBO_NAVER_GATEWAY", "https://api-gw.sports.naver.com")
KBO_NAVER_MOBILE = os.getenv("KBO_NAVER_MOBILE", "https://m.sports.naver.com/kbaseball")
SPORTRADAR_API_KEY = os.getenv("SPORTRADAR_API_KEY", "").strip()
SPORTRADAR_ACCESS_LEVEL = os.getenv("SPORTRADAR_ACCESS_LEVEL", "trial").strip()
SPORTRADAR_LANGUAGE = os.getenv("SPORTRADAR_LANGUAGE", "en").strip()
SPORTRADAR_BASE_URL = os.getenv("SPORTRADAR_BASE_URL", "https://api.sportradar.com").rstrip("/")
ENABLE_SPORTRADAR_KBO = os.getenv("ENABLE_SPORTRADAR_KBO", "true").lower() == "true"

SR_SCHEDULE_REFRESH_SECONDS = int(os.getenv("SR_SCHEDULE_REFRESH_SECONDS", "1800"))
SR_LINEUP_RECHECK_SECONDS = int(os.getenv("SR_LINEUP_RECHECK_SECONDS", "600"))
SR_LINEUP_LOOKAHEAD_MINUTES = int(os.getenv("SR_LINEUP_LOOKAHEAD_MINUTES", "360"))
LINEUP_WAIT_UNTIL_MINUTES = int(os.getenv("LINEUP_WAIT_UNTIL_MINUTES", "30"))
SR_REQUEST_SPACING_SECONDS = float(os.getenv("SR_REQUEST_SPACING_SECONDS", "1.25"))
SR_429_BACKOFF_SECONDS = int(os.getenv("SR_429_BACKOFF_SECONDS", "900"))
TELEGRAM_POST_INTERVAL_SECONDS = int(os.getenv("TELEGRAM_POST_INTERVAL_SECONDS", "120"))

NPB_YAHOO_BASE = os.getenv("NPB_YAHOO_BASE", "https://baseball.yahoo.co.jp")

ENABLE_COMBO_PICKS = False
PROMO_URL = os.getenv("PROMO_URL", "https://om-1224.com/?code=usdt")
PROMO_BUTTON_TEXT = os.getenv("PROMO_BUTTON_TEXT", "🎰 오마카세 바로가기")
PROMO_CODE = os.getenv("PROMO_CODE", "USDT")





DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = os.getenv("DB_PATH", "sports_news.db")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sportnow")


# =========================
# NEWS SOURCES
# =========================
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
    ("🌍 해외축구", '"Premier League" OR "Champions League" OR soccer transfer injury lineup'),
    ("🌍 해외야구", 'MLB baseball injury lineup starting pitcher'),
    ("🌍 해외농구", 'NBA basketball injury lineup'),
    ("🌍 해외미식축구", 'NFL football injury lineup'),
    ("🌍 해외아이스하키", 'NHL hockey injury lineup'),
    ("🌍 해외격투기", 'UFC MMA boxing'),
    ("🌍 해외모터스포츠", '"Formula 1" OR F1'),
]


def google_news_rss(query: str, lang="ko"):
    q = quote_plus(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


FEEDS = [(cat, google_news_rss(q, "ko"), False) for cat, q in SEARCHES_KO]
FEEDS += [(cat, google_news_rss(q, "en"), True) for cat, q in SEARCHES_EN]


# =========================
# DB
# =========================
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
            sent_at TEXT NOT NULL,
            posted INTEGER NOT NULL DEFAULT 1
        )
    """)

    # 이전 버전 DB가 이미 만들어진 경우 컬럼 보강
    if conn.pg:
        conn.execute("""
            ALTER TABLE sent_articles
            ADD COLUMN IF NOT EXISTS posted INTEGER NOT NULL DEFAULT 1
        """)
    else:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sent_articles)").fetchall()]
        if "posted" not in cols:
            conn.execute("ALTER TABLE sent_articles ADD COLUMN posted INTEGER NOT NULL DEFAULT 1")

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

    if conn.pg:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pick_logs (
                id BIGSERIAL PRIMARY KEY,
                matchup_key TEXT NOT NULL,
                pick_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pick_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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


    conn.execute("""
        CREATE TABLE IF NOT EXISTS sport_schedule_cache (
            event_id TEXT PRIMARY KEY,
            sport TEXT NOT NULL,
            league TEXT NOT NULL,
            pick_group TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            start_utc TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT,
            home_score INTEGER,
            away_score INTEGER,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lineup_cache (
            event_id TEXT PRIMARY KEY,
            home_lineup TEXT,
            away_lineup TEXT,
            source TEXT,
            confirmed INTEGER NOT NULL DEFAULT 0,
            checked_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS combo_pick_logs (
            combo_key TEXT PRIMARY KEY,
            pick_group TEXT NOT NULL,
            combo_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    return conn


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def hours_ago_iso(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# =========================
# NEWS HELPERS
# =========================
def fingerprint(title: str, link: str) -> str:
    raw = f"{title.strip().lower()}|{link.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    return strip_tags(entry.get("summary", ""))[:1800]


def normalize_words(title):
    stop = {
        "속보", "단독", "공식", "breaking", "official",
        "sports", "news", "the", "and", "for", "with"
    }
    words = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", (title or "").lower()))
    return {w for w in words if w not in stop}


def title_similarity(a, b):
    aa, bb = normalize_words(a), normalize_words(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def importance_score(item):
    t = item["title"].lower()
    keys = [
        "속보", "공식", "확정", "이적", "영입", "부상", "선발", "라인업",
        "우승", "신기록", "계약", "복귀", "결장", "징계",
        "breaking", "official", "confirmed", "transfer", "trade", "injury",
        "lineup", "starting", "champion", "record", "contract", "return", "suspended"
    ]
    return sum(3 for k in keys if k in t)


def already_sent(conn, fp):
    row = conn.execute(
        "SELECT 1 FROM sent_articles WHERE fingerprint=? LIMIT 1", (fp,)
    ).fetchone()
    return row is not None


def mark_sent(conn, fp, title, link, posted):
    now = utcnow_iso()
    if conn.pg:
        conn.execute(
            """INSERT INTO sent_articles (fingerprint,title,link,sent_at,posted)
               VALUES (?,?,?,?,?)
               ON CONFLICT (fingerprint) DO UPDATE SET
                 title=EXCLUDED.title,
                 link=EXCLUDED.link,
                 sent_at=EXCLUDED.sent_at,
                 posted=EXCLUDED.posted""",
            (fp, title, link, now, int(posted)),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO sent_articles
               (fingerprint,title,link,sent_at,posted)
               VALUES (?,?,?,?,?)""",
            (fp, title, link, now, int(posted)),
        )
    conn.commit()


def is_similar_recent(conn, title):
    cutoff = hours_ago_iso(18)
    rows = conn.execute(
        """SELECT title FROM sent_articles
           WHERE sent_at >= ? AND posted=1
           ORDER BY sent_at DESC LIMIT 250""",
        (cutoff,),
    ).fetchall()
    return any(title_similarity(title, r[0]) >= 0.58 for r in rows)


def posts_last_24h(conn):
    cutoff = hours_ago_iso(24)
    return conn.execute(
        "SELECT COUNT(*) FROM sent_articles WHERE sent_at >= ? AND posted=1",
        (cutoff,),
    ).fetchone()[0]


def cache_article(conn, item):
    fp = fingerprint(item["title"], item["link"])
    entry = item["entry"]
    values = (
        fp,
        item["title"],
        clean_source(entry),
        article_context(entry),
        item["category"],
        item["link"],
        1 if item["is_foreign"] else 0,
        str(entry.get("published", "")),
        utcnow_iso(),
    )

    if conn.pg:
        conn.execute(
            """INSERT INTO article_cache
               (fingerprint,title,source,summary,category,link,is_foreign,published_at,cached_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT (fingerprint) DO UPDATE SET
                 title=EXCLUDED.title,
                 source=EXCLUDED.source,
                 summary=EXCLUDED.summary,
                 category=EXCLUDED.category,
                 link=EXCLUDED.link,
                 is_foreign=EXCLUDED.is_foreign,
                 published_at=EXCLUDED.published_at,
                 cached_at=EXCLUDED.cached_at""",
            values,
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO article_cache
               (fingerprint,title,source,summary,category,link,is_foreign,published_at,cached_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            values,
        )
    conn.commit()


def collect_entries():
    items = []

    for category, url, is_foreign in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = clean_title(entry)
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue

                published_ts = 0
                if entry.get("published_parsed"):
                    try:
                        published_ts = time.mktime(entry.published_parsed)
                    except Exception:
                        published_ts = 0

                items.append({
                    "category": category,
                    "entry": entry,
                    "title": title,
                    "link": link,
                    "is_foreign": is_foreign,
                    "published_ts": published_ts,
                })
        except Exception:
            log.exception("Feed read failed | %s", url)

    unique = {}
    for item in items:
        key = (item["title"].lower(), item["link"])
        if key not in unique or item["published_ts"] > unique[key]["published_ts"]:
            unique[key] = item

    return sorted(
        unique.values(),
        key=lambda x: (importance_score(x), x["published_ts"]),
        reverse=True,
    )



# =========================
# FREE TEAM / GAME DATA
# =========================
def espn_scoreboard_url(sport, league):
    return f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"

def league_code_from_name(name):
    for sport, league, league_name, pick_group in MAJOR_LEAGUES:
        if league_name == name:
            return sport, league
    # 국내/일본 리그는 ESPN recent-game helper를 쓰지 않음
    return None, None

def fetch_recent_team_games(team_name, sport, league, lookback=5):
    """최근 며칠 scoreboard에서 해당 팀 경기 결과를 찾아 간단한 폼 지표를 만든다."""
    now = datetime.now(timezone.utc)
    found = []

    # 넉넉하게 과거 20일 확인
    for d in range(1, 21):
        if len(found) >= lookback:
            break

        date_key = (now - timedelta(days=d)).strftime("%Y%m%d")
        try:
            r = requests.get(
                espn_scoreboard_url(sport, league),
                params={"dates": date_key},
                timeout=12
            )
            if r.status_code != 200:
                continue
            data = r.json()

            for ev in data.get("events", []):
                comps = ev.get("competitions") or []
                if not comps:
                    continue
                comp = comps[0]
                status = ((comp.get("status") or {}).get("type") or {})
                if not status.get("completed"):
                    continue

                competitors = comp.get("competitors") or []
                team_row = opp_row = None

                for c in competitors:
                    t = c.get("team") or {}
                    name = t.get("displayName") or t.get("name") or ""
                    if same_team(name, team_name):
                        team_row = c
                    else:
                        opp_row = c

                if not team_row or not opp_row:
                    continue

                try:
                    team_score = int(float(team_row.get("score", 0)))
                    opp_score = int(float(opp_row.get("score", 0)))
                except Exception:
                    continue

                found.append({
                    "date": ev.get("date", ""),
                    "team_score": team_score,
                    "opp_score": opp_score,
                    "win": team_score > opp_score,
                    "loss": team_score < opp_score,
                    "draw": team_score == opp_score,
                })

                if len(found) >= lookback:
                    break

        except Exception:
            log.exception("Recent games fetch failed | %s | %s", team_name, date_key)

    wins = sum(1 for g in found if g["win"])
    losses = sum(1 for g in found if g["loss"])
    draws = sum(1 for g in found if g["draw"])
    pts_for = sum(g["team_score"] for g in found)
    pts_against = sum(g["opp_score"] for g in found)

    return {
        "games": len(found),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "points_for": pts_for,
        "points_against": pts_against,
        "avg_for": round(pts_for / len(found), 2) if found else 0,
        "avg_against": round(pts_against / len(found), 2) if found else 0,
    }

def fetch_summary_team_context(event_id, sport, league):
    """summary 응답에서 가능한 범위의 부상/로스터/선수 정보 텍스트를 추출."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary"
    try:
        r = requests.get(url, params={"event": event_id}, timeout=12)
        if r.status_code != 200:
            return {}

        data = r.json()
        context = {
            "injuries": [],
            "leaders": [],
            "notes": [],
        }

        injuries = data.get("injuries") or []
        for group in injuries[:10]:
            team = (group.get("team") or {}).get("displayName") or ""
            for inj in group.get("injuries", [])[:10]:
                athlete = (inj.get("athlete") or {}).get("displayName") or ""
                status = inj.get("status") or ""
                detail = ((inj.get("details") or {}).get("detail")) or ""
                if athlete:
                    context["injuries"].append({
                        "team": team,
                        "athlete": athlete,
                        "status": status,
                        "detail": detail[:180],
                    })

        leaders = data.get("leaders") or []
        for group in leaders[:10]:
            team = (group.get("team") or {}).get("displayName") or ""
            for cat in group.get("leaders", [])[:6]:
                name = cat.get("name") or cat.get("displayName") or ""
                leaders_list = cat.get("leaders") or []
                if leaders_list:
                    ath = (leaders_list[0].get("athlete") or {}).get("displayName") or ""
                    value = leaders_list[0].get("displayValue") or ""
                    if ath:
                        context["leaders"].append({
                            "team": team,
                            "category": name,
                            "athlete": ath,
                            "value": value,
                        })

        notes = data.get("news") or []

        # ESPN summary의 news 필드는 리그/경기에 따라 list 또는 dict 형태가 올 수 있음.
        if isinstance(notes, dict):
            # 흔한 구조: {"articles": [...]} / {"items": [...]}
            notes = notes.get("articles") or notes.get("items") or notes.get("news") or []
        elif not isinstance(notes, list):
            notes = []

        for n in notes[:5]:
            if not isinstance(n, dict):
                continue
            h = n.get("headline") or n.get("title") or ""
            if h:
                context["notes"].append(str(h)[:220])

        return context
    except Exception:
        log.exception("Summary context fetch failed | %s", event_id)
        return {}

def build_free_game_context(games):
    if not ENABLE_FREE_TEAM_DATA:
        return games

    out = []
    for g in games:
        sport, league = league_code_from_name(g["league"])
        item = dict(g)

        if sport and league:
            try:
                item["home_recent"] = fetch_recent_team_games(
                    g["home"], sport, league, RECENT_GAMES_LOOKBACK
                )
            except Exception:
                item["home_recent"] = {}

            try:
                item["away_recent"] = fetch_recent_team_games(
                    g["away"], sport, league, RECENT_GAMES_LOOKBACK
                )
            except Exception:
                item["away_recent"] = {}

            try:
                item["event_context"] = fetch_summary_team_context(
                    g["event_id"], sport, league
                )
            except Exception:
                item["event_context"] = {}
        else:
            # KBO/NPB/K리그는 공식 일정 + 최근 뉴스 중심
            item["home_recent"] = {}
            item["away_recent"] = {}
            item["event_context"] = {"source": g.get("source", "official")}

        out.append(item)

    return out

def basic_model_score(game):
    """AI 전에 최소한의 수치 기반 기준점을 만든다.
    최근 경기 성적과 득실만 사용. 50 기준, -12~+12 범위."""
    home = game.get("home_recent") or {}
    away = game.get("away_recent") or {}

    if not home.get("games") or not away.get("games"):
        return 50.0

    home_games = max(home["games"], 1)
    away_games = max(away["games"], 1)

    home_winrate = home["wins"] / home_games
    away_winrate = away["wins"] / away_games

    home_diff = home.get("avg_for", 0) - home.get("avg_against", 0)
    away_diff = away.get("avg_for", 0) - away.get("avg_against", 0)

    raw = 50
    raw += (home_winrate - away_winrate) * 12
    raw += max(-5, min(5, (home_diff - away_diff) * 1.5))

    # 홈 어드밴티지 소폭
    raw += 1.5

    return round(max(38, min(62, raw)), 1)

# =========================
# OPENAI NEWS
# =========================
def parse_json_output(raw):
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def ai_translate_and_summarize(title, summary, source):
    prompt = f"""
너는 한국 스포츠 속보 채널 편집자다.

아래 해외 스포츠 뉴스 정보를 한국어로 바꿔라.
규칙:
- 원문에 없는 사실을 만들지 말 것.
- 제목은 자연스럽고 짧게.
- 요약은 최대 2~3문장.
- RSS 정보가 부족하면 확인되는 사실만 작성.
- JSON 하나만 출력.

{{"title":"한국어 제목","summary":"한국어 요약"}}

출처: {source}
원문 제목: {title}
RSS 설명: {summary}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    data = parse_json_output(response.output_text)
    return data["title"].strip(), data["summary"].strip()


def ai_summarize_korean(title, summary, source):
    if not SUMMARIZE_KOREAN:
        return title, ""

    prompt = f"""
아래 한국 스포츠 뉴스의 제목과 RSS 설명만 이용해 최대 2문장으로 요약해라.
없는 사실은 추가하지 마라.
JSON 하나만 출력:
{{"summary":"요약"}}

출처: {source}
제목: {title}
RSS 설명: {summary}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    data = parse_json_output(response.output_text)
    return title, data.get("summary", "").strip()


# =========================
# TELEGRAM
# =========================
def send_telegram(text, promo_button=False):
    _telegram_post_spacing()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    if promo_button:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {
                    "text": PROMO_BUTTON_TEXT,
                    "url": PROMO_URL
                }
            ]]
        }

    for _ in range(3):
        r = requests.post(url, json=payload, timeout=25)

        if r.status_code == 429:
            retry_after = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(int(retry_after) + 1)
            continue

        r.raise_for_status()
        data = r.json()

        if data.get("ok"):
            return

        raise RuntimeError(data)

    raise RuntimeError("Telegram 전송 재시도 횟수 초과")


def process_article(item):
    entry = item["entry"]
    source = clean_source(entry)
    summary = article_context(entry)

    if item["is_foreign"]:
        title_ko, summary_ko = ai_translate_and_summarize(
            item["title"], summary, source
        )
    else:
        try:
            title_ko, summary_ko = ai_summarize_korean(
                item["title"], summary, source
            )
        except Exception:
            log.exception("국내뉴스 요약 실패, 제목만 사용 | %s", item["title"])
            title_ko, summary_ko = item["title"], ""

    return title_ko, summary_ko, source


def format_news_post(item, title_ko, summary_ko, source):
    badge = "\n🌐 해외 기사 자동번역" if item["is_foreign"] else ""
    summary_block = f"\n\n{html.escape(summary_ko)}" if summary_ko else ""

    return (
        f"{item['category']}{badge}\n\n"
        f"<b>{html.escape(title_ko)}</b>"
        f"{summary_block}\n\n"
        f"📰 출처: {html.escape(source)}\n"
        f'🔗 <a href="{html.escape(item["link"], quote=True)}">원문 기사 보기</a>\n\n'
        f"#스포츠뉴스 #실시간스포츠"
    )


def seed_existing(conn):
    items = collect_entries()

    for item in items:
        try:
            cache_article(conn, item)
        except Exception:
            log.exception("Seed cache failed | %s", item["title"])

        fp = fingerprint(item["title"], item["link"])
        if not already_sent(conn, fp):
            mark_sent(conn, fp, item["title"], item["link"], posted=0)

    log.info("첫 실행: 기존 기사 %d건 스킵 처리", len(items))


def run_news_cycle(conn):
    items = collect_entries()

    # 분석 재료로는 계속 수집/캐시
    for item in items:
        try:
            cache_article(conn, item)
        except Exception:
            log.exception("Article cache failed | %s", item["title"])

    # v9: 채널에는 픽/결과만 게시. 뉴스는 내부 분석용.
    if not POST_NEWS_PUBLICLY:
        log.info("News cache updated | public posting disabled | candidates=%d", len(items))
        return

    daily_count = posts_last_24h(conn)
    sent_count = 0

    for item in items:

        fp = fingerprint(item["title"], item["link"])

        if already_sent(conn, fp):
            continue

        if is_similar_recent(conn, item["title"]):
            mark_sent(conn, fp, item["title"], item["link"], posted=0)
            log.info("SIMILAR SKIP | %s", item["title"])
            continue

        try:
            title_ko, summary_ko, source = process_article(item)
            send_telegram(format_news_post(item, title_ko, summary_ko, source))
            mark_sent(conn, fp, item["title"], item["link"], posted=1)
            sent_count += 1
            log.info("NEWS POSTED | %s", title_ko)
            time.sleep(2)
        except Exception:
            log.exception("News processing/send failed | %s", item["title"])

    log.info("News cycle complete | posted=%d | candidates=%d", sent_count, len(items))


# =========================
# MAJOR LEAGUE SCHEDULE
# =========================
MAJOR_LEAGUES = [
    # ESPN에서 안정적으로 조회할 해외 메이저 리그만
    ("baseball", "mlb", "MLB", "mlb"),

    ("soccer", "eng.1", "EPL", "soccer"),
    ("soccer", "esp.1", "La Liga", "soccer"),
    ("soccer", "ger.1", "Bundesliga", "soccer"),
    ("soccer", "ita.1", "Serie A", "soccer"),
    ("soccer", "fra.1", "Ligue 1", "soccer"),
    ("soccer", "uefa.champions", "UEFA Champions League", "soccer"),

    ("basketball", "nba", "NBA", "basketball"),
]

DOMESTIC_LEAGUES = {
    "KBO": "kbo",
    "NPB": "npb",
    "K League 1": "soccer",
    "KBL": "basketball",
}



def _within_prematch_window(start_utc):
    """Legacy helper kept for old schedule parsers: future games only, no minute window."""
    now = datetime.now(timezone.utc)
    mins = (start_utc - now).total_seconds() / 60
    return mins > 0, round(mins)


def _clean_team_text(x):
    return re.sub(r"\s+", " ", (x or "")).strip()

def fetch_kbo_official_games():
    """KBO 공식 영문 Daily Schedule에서 당일 경기를 읽는다."""
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    year = now_kst.year
    month = now_kst.month
    day = now_kst.day

    url = "https://eng.koreabaseball.com/Schedule/DailySchedule.aspx"
    games = []

    try:
        r = requests.get(
            url,
            params={"searchDate": f"{year}.{month:02d}"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            log.warning("KBO official schedule HTTP %s", r.status_code)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.find_all("tr")
        current_day = None

        for tr in rows:
            cells = [_clean_team_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th","td"])]
            if not cells:
                continue

            joined = " | ".join(cells)

            # 날짜가 있는 행
            mdate = re.search(r'(\d{2})\.(\d{2})', joined)
            if mdate:
                try:
                    current_day = int(mdate.group(2))
                except Exception:
                    pass

            if current_day != day:
                continue

            # 시간
            mt = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', joined)
            if not mt:
                continue

            # KBO 영문 팀명 후보
            teams = []
            KBO_NAMES = [
                "LG","HANWHA","SSG","SAMSUNG","NC","KT","LOTTE","KIA","DOOSAN","KIWOOM"
            ]
            for name in KBO_NAMES:
                if re.search(rf'\b{name}\b', joined, re.I):
                    teams.append(name)

            # 중복 제거 + 2팀 필요
            uniq = []
            for t in teams:
                if t not in uniq:
                    uniq.append(t)
            if len(uniq) < 2:
                continue

            hh, mm = int(mt.group(1)), int(mt.group(2))
            start_kst = datetime(year, month, day, hh, mm, tzinfo=timezone(timedelta(hours=9)))
            start_utc = start_kst.astimezone(timezone.utc)
            ok, mins = _within_prematch_window(start_utc)
            if not ok:
                continue

            away, home = uniq[0], uniq[1]
            games.append({
                "event_id": f"KBO-{start_kst.strftime('%Y%m%d')}-{away}-{home}",
                "sport": "baseball",
                "league": "KBO",
                "pick_group": "kbo",
                "home": home,
                "away": away,
                "start_utc": start_utc.isoformat(),
                "minutes_to_start": mins,
                "source": "KBO official",
            })

    except Exception:
        log.exception("KBO official schedule fetch failed")

    return games

def fetch_npb_official_games():
    """NPB 공식 월간 상세 일정 페이지에서 오늘 경기를 읽는다."""
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    year, month, day = now_jst.year, now_jst.month, now_jst.day
    url = f"https://npb.jp/games/{year}/schedule_{month:02d}_detail.html"
    games = []

    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning("NPB official schedule HTTP %s", r.status_code)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]

        NPB_TEAMS = [
            "巨人","DeNA","ヤクルト","阪神","広島","中日",
            "日本ハム","ロッテ","楽天","ソフトバンク","西武","オリックス"
        ]

        date_re = re.compile(rf'^{month}/{day}(?:（.*?）)?$')
        active = False

        for i, line in enumerate(lines):
            if date_re.search(line):
                active = True
                continue
            if active and re.match(r'^\d{1,2}/\d{1,2}', line):
                break
            if not active:
                continue

            mt = re.search(r'([01]?\d|2[0-3]):([0-5]\d)', line)
            if not mt:
                continue

            found = [t for t in NPB_TEAMS if t in line]

            # 팀명이 다른 줄에 나뉜 경우 주변 줄까지 결합
            if len(found) < 2:
                chunk = " ".join(lines[max(0,i-2):min(len(lines),i+3)])
                found = [t for t in NPB_TEAMS if t in chunk]

            uniq = []
            for t in found:
                if t not in uniq:
                    uniq.append(t)

            if len(uniq) < 2:
                continue

            hh, mm = int(mt.group(1)), int(mt.group(2))
            start_jst = datetime(year, month, day, hh, mm, tzinfo=timezone(timedelta(hours=9)))
            start_utc = start_jst.astimezone(timezone.utc)
            ok, mins = _within_prematch_window(start_utc)
            if not ok:
                continue

            away, home = uniq[0], uniq[1]
            games.append({
                "event_id": f"NPB-{start_jst.strftime('%Y%m%d')}-{away}-{home}",
                "sport": "baseball",
                "league": "NPB",
                "pick_group": "npb",
                "home": home,
                "away": away,
                "start_utc": start_utc.isoformat(),
                "minutes_to_start": mins,
                "source": "NPB official",
            })

    except Exception:
        log.exception("NPB official schedule fetch failed")

    # 같은 경기 중복 제거
    out = {}
    for g in games:
        out[g["event_id"]] = g
    return list(out.values())

def fetch_kleague_official_games():
    """K리그 공식 일정 페이지를 우선 파싱. 페이지 구조 변경 시 조용히 건너뜀."""
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    year, month, day = now_kst.year, now_kst.month, now_kst.day
    url = "https://www.kleague.com/schedule.do"
    games = []

    try:
        r = requests.get(
            url,
            params={"leagueId": "1", "year": str(year), "month": f"{month:02d}"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            log.warning("K League official schedule HTTP %s", r.status_code)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        # 표 행 중심 파싱
        for tr in soup.find_all("tr"):
            cells = [_clean_team_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th","td"])]
            if not cells:
                continue
            joined = " | ".join(cells)

            # 날짜 확인
            if not (
                re.search(rf'\b{month}[./-]0?{day}\b', joined) or
                re.search(rf'\b0?{day}\b', cells[0] if cells else "")
            ):
                continue

            mt = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', joined)
            if not mt:
                continue

            # VS 주변 텍스트에서 팀명 추정
            # 셀 중 너무 짧거나 시간/날짜/장소만인 값 제외
            candidates = []
            for c in cells:
                if re.search(r'\d{1,2}:\d{2}', c):
                    continue
                if re.fullmatch(r'[\d./\-()월일\s]+', c):
                    continue
                if len(c) >= 2 and len(c) <= 30:
                    candidates.append(c)

            # 알려진 K리그1 팀명으로 필터
            K1 = ["강원","광주","김천","대전","부천","서울","안양","울산","인천","전북","제주","포항"]
            found = []
            for team in K1:
                if team in joined:
                    found.append(team)

            if len(found) < 2:
                continue

            hh, mm = int(mt.group(1)), int(mt.group(2))
            start_kst = datetime(year, month, day, hh, mm, tzinfo=timezone(timedelta(hours=9)))
            start_utc = start_kst.astimezone(timezone.utc)
            ok, mins = _within_prematch_window(start_utc)
            if not ok:
                continue

            away, home = found[0], found[1]
            games.append({
                "event_id": f"KLEAGUE1-{start_kst.strftime('%Y%m%d')}-{away}-{home}",
                "sport": "soccer",
                "league": "K League 1",
                "pick_group": "soccer",
                "home": home,
                "away": away,
                "start_utc": start_utc.isoformat(),
                "minutes_to_start": mins,
                "source": "K League official",
            })

    except Exception:
        log.exception("K League official schedule fetch failed")

    out = {}
    for g in games:
        out[g["event_id"]] = g
    return list(out.values())




_TG_LAST_POST_AT = 0.0

def _telegram_post_spacing():
    """Serialize public channel posts so simultaneous picks do not collide."""
    global _TG_LAST_POST_AT
    now = time.monotonic()
    wait = TELEGRAM_POST_INTERVAL_SECONDS - (now - _TG_LAST_POST_AT)
    if _TG_LAST_POST_AT and wait > 0:
        log.info("Telegram post queue | waiting %.1fs", wait)
        time.sleep(wait)
    _TG_LAST_POST_AT = time.monotonic()


_SR_LAST_REQUEST_AT = 0.0
_SR_BACKOFF_UNTIL = 0.0


def app_state_get(conn, key, default=""):
    row = conn.execute(
        "SELECT state_value FROM app_state WHERE state_key=?",
        (key,)
    ).fetchone()
    return row[0] if row else default


def app_state_set(conn, key, value):
    now = utcnow_iso()
    if conn.pg:
        conn.execute(
            """INSERT INTO app_state (state_key,state_value,updated_at)
               VALUES (?,?,?)
               ON CONFLICT (state_key)
               DO UPDATE SET state_value=EXCLUDED.state_value,
                             updated_at=EXCLUDED.updated_at""",
            (key, str(value), now),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO app_state
               (state_key,state_value,updated_at)
               VALUES (?,?,?)""",
            (key, str(value), now),
        )
    conn.commit()


def _sr_get_json(url, params=None, label="Sportradar"):
    """Single shared Sportradar request path with spacing + 429 backoff."""
    global _SR_LAST_REQUEST_AT, _SR_BACKOFF_UNTIL

    now_mono = time.monotonic()
    if now_mono < _SR_BACKOFF_UNTIL:
        wait = round(_SR_BACKOFF_UNTIL - now_mono)
        log.warning("%s skipped | Sportradar backoff active | %ss", label, wait)
        return None

    delta = now_mono - _SR_LAST_REQUEST_AT
    if delta < SR_REQUEST_SPACING_SECONDS:
        time.sleep(SR_REQUEST_SPACING_SECONDS - delta)

    try:
        r = requests.get(url, params=params, headers=_sportradar_headers(), timeout=20)
        _SR_LAST_REQUEST_AT = time.monotonic()

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            try:
                backoff = max(int(retry_after), SR_429_BACKOFF_SECONDS)
            except Exception:
                backoff = SR_429_BACKOFF_SECONDS
            _SR_BACKOFF_UNTIL = time.monotonic() + backoff
            log.warning("%s | HTTP 429 Too Many Requests | backoff=%ss", label, backoff)
            return None

        if r.status_code != 200:
            log.warning("%s | HTTP %s | %s", label, r.status_code, r.text[:220])
            return None

        return r.json()

    except Exception:
        _SR_LAST_REQUEST_AT = time.monotonic()
        log.exception("%s request failed", label)
        return None


def _parse_int_score(v):
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _summary_status_scores(summary):
    st = summary.get("sport_event_status") or {}
    status = str(st.get("status") or st.get("match_status") or "").lower()
    home_score = _parse_int_score(st.get("home_score"))
    away_score = _parse_int_score(st.get("away_score"))
    return status, home_score, away_score


def _classify_sr_baseball(summary):
    """Classify one Global Baseball summary into KBO / NPB / MLB."""
    text = _sr_context_text(summary)

    if "kbo" in text or "korea baseball" in text:
        league = "KBO"
        aliases = KBO_SR_ALIASES
        group = "kbo"
    elif "npb" in text or "nippon professional" in text or (
        ("japan" in text or "'jpn'" in text) and "baseball" in text
    ):
        league = "NPB"
        aliases = NPB_SR_ALIASES
        group = "npb"
    elif "mlb" in text or "major league baseball" in text or "major league" in text:
        league = "MLB"
        aliases = MLB_SR_ALIASES
        group = "mlb"
    else:
        return None

    comps = _sr_event_competitors(summary)
    home_comp = next((c for c in comps if c.get("qualifier") == "home"), None)
    away_comp = next((c for c in comps if c.get("qualifier") == "away"), None)
    if not home_comp or not away_comp:
        return None

    home_name = next(
        (code for code in aliases if _sr_team_matches(code, home_comp, league)),
        None
    )
    away_name = next(
        (code for code in aliases if _sr_team_matches(code, away_comp, league)),
        None
    )
    if not home_name or not away_name:
        return None

    return league, group, home_name, away_name, home_comp, away_comp


def _upsert_schedule_game(conn, game):
    now = utcnow_iso()
    vals = (
        game["event_id"], game["sport"], game["league"], game["pick_group"],
        game["home"], game["away"], game["start_utc"], game["source"],
        game.get("status"), game.get("home_score"), game.get("away_score"), now,
    )

    if conn.pg:
        conn.execute(
            """INSERT INTO sport_schedule_cache
               (event_id,sport,league,pick_group,home_team,away_team,start_utc,
                source,status,home_score,away_score,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (event_id)
               DO UPDATE SET sport=EXCLUDED.sport,
                             league=EXCLUDED.league,
                             pick_group=EXCLUDED.pick_group,
                             home_team=EXCLUDED.home_team,
                             away_team=EXCLUDED.away_team,
                             start_utc=EXCLUDED.start_utc,
                             source=EXCLUDED.source,
                             status=EXCLUDED.status,
                             home_score=EXCLUDED.home_score,
                             away_score=EXCLUDED.away_score,
                             updated_at=EXCLUDED.updated_at""",
            vals,
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO sport_schedule_cache
               (event_id,sport,league,pick_group,home_team,away_team,start_utc,
                source,status,home_score,away_score,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            vals,
        )


def refresh_baseball_schedule_cache(conn, force=False):
    """One shared Global Baseball schedule refresh for KBO/NPB/MLB."""
    last_raw = app_state_get(conn, "sr_baseball_schedule_refresh", "")
    if not force and last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age < SR_SCHEDULE_REFRESH_SECONDS:
                return
        except Exception:
            pass

    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc + timedelta(hours=9)

    # Minimal schedule calls:
    # - KST today catches KBO/NPB and today's Korea-facing schedule.
    # - UTC today is added only when it differs, which catches MLB across the
    #   Korea/UTC date boundary (e.g. early-morning KST games).
    # Never prefetch tomorrow just to fill the cache.
    dates = sorted({
        now_kst.strftime("%Y-%m-%d"),
        now_utc.strftime("%Y-%m-%d"),
    })

    found = 0
    successful_calls = 0
    log.info("Baseball schedule refresh dates | %s", ",".join(dates))

    for date_str in dates:
        url = (
            f"{SPORTRADAR_BASE_URL}/baseball/{SPORTRADAR_ACCESS_LEVEL}/v2/"
            f"{SPORTRADAR_LANGUAGE}/schedules/{date_str}/summaries.json"
        )
        data = _sr_get_json(url, label=f"Baseball schedule {date_str}")
        if data is None:
            continue
        successful_calls += 1

        for summary in _iter_sr_summaries(data):
            ev = summary.get("sport_event") or {}
            eid = ev.get("id")
            start_raw = ev.get("start_time")
            if not eid or not start_raw:
                continue

            classified = _classify_sr_baseball(summary)
            if not classified:
                continue

            league, group, home, away, home_comp, away_comp = classified

            try:
                start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            except Exception:
                continue

            status, hs, aws = _summary_status_scores(summary)

            _upsert_schedule_game(conn, {
                "event_id": str(eid),
                "sport": "baseball",
                "league": league,
                "pick_group": group,
                "home": home,
                "away": away,
                "start_utc": start.isoformat(),
                "source": "Sportradar",
                "status": status,
                "home_score": hs,
                "away_score": aws,
            })
            found += 1

    if successful_calls:
        conn.commit()
        app_state_set(conn, "sr_baseball_schedule_refresh", utcnow_iso())
        log.info(
            "Baseball schedule cache refreshed | calls=%d | events=%d",
            successful_calls, found
        )
    else:
        log.warning("Baseball schedule refresh produced no successful API call")


def load_cached_baseball_games(conn):
    now = utcnow_iso()
    rows = conn.execute(
        """SELECT event_id,league,pick_group,home_team,away_team,start_utc,source
           FROM sport_schedule_cache
           WHERE sport='baseball'
             AND start_utc > ?
           ORDER BY start_utc ASC""",
        (now,),
    ).fetchall()

    out = []
    current = datetime.now(timezone.utc)
    for eid, league, group, home, away, start_utc, source in rows:
        try:
            start = datetime.fromisoformat(start_utc)
            mins = round((start - current).total_seconds() / 60)
        except Exception:
            mins = 0

        out.append({
            "event_id": eid,
            "sportradar_event_id": eid,
            "sport": "baseball",
            "league": league,
            "pick_group": group,
            "home": home,
            "away": away,
            "start_utc": start_utc,
            "minutes_to_start": mins,
            "source": source,
        })
    return out


def _lineup_cache_get(conn, event_id):
    row = conn.execute(
        """SELECT home_lineup,away_lineup,source,confirmed,checked_at
           FROM lineup_cache WHERE event_id=?""",
        (event_id,),
    ).fetchone()
    if not row:
        return None

    home_raw, away_raw, source, confirmed, checked_at = row
    try:
        home = json.loads(home_raw or "[]")
    except Exception:
        home = []
    try:
        away = json.loads(away_raw or "[]")
    except Exception:
        away = []

    return {
        "home": home,
        "away": away,
        "source": source or "",
        "confirmed": bool(confirmed),
        "checked_at": checked_at,
    }


def _lineup_cache_set(conn, event_id, info):
    now = utcnow_iso()
    vals = (
        event_id,
        json.dumps(info.get("home") or [], ensure_ascii=False),
        json.dumps(info.get("away") or [], ensure_ascii=False),
        info.get("source") or "",
        1 if info.get("confirmed") else 0,
        now,
    )
    if conn.pg:
        conn.execute(
            """INSERT INTO lineup_cache
               (event_id,home_lineup,away_lineup,source,confirmed,checked_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT (event_id)
               DO UPDATE SET home_lineup=EXCLUDED.home_lineup,
                             away_lineup=EXCLUDED.away_lineup,
                             source=EXCLUDED.source,
                             confirmed=EXCLUDED.confirmed,
                             checked_at=EXCLUDED.checked_at""",
            vals,
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO lineup_cache
               (event_id,home_lineup,away_lineup,source,confirmed,checked_at)
               VALUES (?,?,?,?,?,?)""",
            vals,
        )
    conn.commit()


def _looks_like_mlb_summary(summary):
    text = _sr_context_text(summary)
    return "mlb" in text or "major league baseball" in text or "major league" in text


def fetch_sportradar_mlb_upcoming_games():
    """MLB schedule comes directly from Sportradar so event_id is valid for lineups."""
    if not SPORTRADAR_API_KEY:
        log.info("MLB Sportradar schedule skipped | API key missing")
        return []

    now_utc = datetime.now(timezone.utc)

    # MLB games can cross UTC dates, so scan yesterday/today/tomorrow.
    dates = {
        (now_utc + timedelta(days=d)).strftime("%Y-%m-%d")
        for d in (-1, 0, 1)
    }
    games = {}

    try:
        for date_str in sorted(dates):
            url = (
                f"{SPORTRADAR_BASE_URL}/baseball/{SPORTRADAR_ACCESS_LEVEL}/v2/"
                f"{SPORTRADAR_LANGUAGE}/schedules/{date_str}/summaries.json"
            )
            r = requests.get(url, headers=_sportradar_headers(), timeout=20)

            if r.status_code != 200:
                log.warning(
                    "MLB Sportradar schedule failed | %s | HTTP %s | %s",
                    date_str, r.status_code, r.text[:160]
                )
                continue

            for summary in _iter_sr_summaries(r.json()):
                ev = summary.get("sport_event") or {}
                eid = ev.get("id")
                if not eid:
                    continue

                comps = _sr_event_competitors(summary)
                if len(comps) < 2:
                    continue

                home_comp = next((c for c in comps if c.get("qualifier") == "home"), None)
                away_comp = next((c for c in comps if c.get("qualifier") == "away"), None)
                if not home_comp or not away_comp:
                    continue

                # Strongest filter: both competitors must match our MLB alias table.
                home_name = None
                away_name = None

                for code in MLB_SR_ALIASES:
                    if _sr_team_matches(code, home_comp, "MLB"):
                        home_name = code
                        break
                for code in MLB_SR_ALIASES:
                    if _sr_team_matches(code, away_comp, "MLB"):
                        away_name = code
                        break

                if not home_name or not away_name:
                    continue

                if not _looks_like_mlb_summary(summary):
                    log.debug(
                        "MLB context weak but teams matched | %s | %s vs %s",
                        eid, away_comp.get("name"), home_comp.get("name")
                    )

                start_raw = ev.get("start_time")
                if not start_raw:
                    continue

                try:
                    start_utc = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
                except Exception:
                    continue

                # No prematch minute window, but exclude already-started games.
                if start_utc <= now_utc:
                    continue

                games[eid] = {
                    "event_id": eid,
                    "sport": "baseball",
                    "league": "MLB",
                    "pick_group": "mlb",
                    "home": home_name,
                    "away": away_name,
                    "start_utc": start_utc.isoformat(),
                    "minutes_to_start": round((start_utc - now_utc).total_seconds() / 60),
                    "source": "Sportradar",
                    "sportradar_event_id": eid,
                    "sportradar_home_id": home_comp.get("id", ""),
                    "sportradar_away_id": away_comp.get("id", ""),
                    "sportradar_home_name": home_comp.get("name", ""),
                    "sportradar_away_name": away_comp.get("name", ""),
                }

        if games:
            log.info("MLB Sportradar schedule candidates | count=%d", len(games))
        else:
            log.info("MLB Sportradar schedule | no upcoming games")

    except Exception:
        log.exception("MLB Sportradar schedule fetch failed")

    return list(games.values())


def fetch_sportradar_kbo_upcoming_games():
    """KBO 일정 자체를 Sportradar Daily Summaries에서 생성한다."""
    if not SPORTRADAR_API_KEY:
        log.info("KBO Sportradar schedule skipped | API key missing")
        return []

    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(timezone(timedelta(hours=9)))
    date_keys = {
        now_jst.strftime("%Y-%m-%d"),
        (now_jst + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    out = {}

    try:
        for date_str in sorted(date_keys):
            url = (
                f"{SPORTRADAR_BASE_URL}/baseball/{SPORTRADAR_ACCESS_LEVEL}/v2/"
                f"{SPORTRADAR_LANGUAGE}/schedules/{date_str}/summaries.json"
            )
            r = requests.get(url, headers=_sportradar_headers(), timeout=20)
            if r.status_code != 200:
                log.warning("KBO Sportradar schedule failed | %s | HTTP %s | %s",
                            date_str, r.status_code, r.text[:160])
                continue

            for summary in _iter_sr_summaries(r.json()):
                ev = summary.get("sport_event") or {}
                eid = ev.get("id")
                comps = _sr_event_competitors(summary)
                if not eid or len(comps) < 2:
                    continue

                hc = next((c for c in comps if c.get("qualifier") == "home"), None)
                ac = next((c for c in comps if c.get("qualifier") == "away"), None)
                if not hc or not ac:
                    continue

                home_code = next((code for code in KBO_SR_ALIASES if _sr_team_matches(code, hc, "KBO")), None)
                away_code = next((code for code in KBO_SR_ALIASES if _sr_team_matches(code, ac, "KBO")), None)
                if not home_code or not away_code:
                    continue

                start_raw = ev.get("start_time")
                if not start_raw:
                    continue
                try:
                    start_utc = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
                except Exception:
                    continue

                mins = (start_utc - now_utc).total_seconds() / 60
                out[eid] = {
                    "event_id": eid,
                    "sport": "baseball",
                    "league": "KBO",
                    "pick_group": "kbo",
                    "home": home_code,
                    "away": away_code,
                    "start_utc": start_utc.isoformat(),
                    "minutes_to_start": round(mins),
                    "source": "Sportradar",
                    "sportradar_event_id": eid,
                    "sportradar_home_id": hc.get("id", ""),
                    "sportradar_away_id": ac.get("id", ""),
                    "sportradar_home_name": hc.get("name", ""),
                    "sportradar_away_name": ac.get("name", ""),
                }

        log.info("KBO Sportradar schedule candidates | count=%d", len(out))
    except Exception:
        log.exception("KBO Sportradar schedule fetch failed")

    return list(out.values())


def fetch_sportradar_npb_upcoming_games():
    """NPB 일정 자체를 Sportradar Daily Summaries에서 생성한다."""
    if not SPORTRADAR_API_KEY:
        log.info("NPB Sportradar schedule skipped | API key missing")
        return []

    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(timezone(timedelta(hours=9)))
    date_keys = {
        now_jst.strftime("%Y-%m-%d"),
        (now_jst + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    out = {}

    try:
        for date_str in sorted(date_keys):
            url = (
                f"{SPORTRADAR_BASE_URL}/baseball/{SPORTRADAR_ACCESS_LEVEL}/v2/"
                f"{SPORTRADAR_LANGUAGE}/schedules/{date_str}/summaries.json"
            )
            r = requests.get(url, headers=_sportradar_headers(), timeout=20)
            if r.status_code != 200:
                log.warning("NPB Sportradar schedule failed | %s | HTTP %s | %s",
                            date_str, r.status_code, r.text[:160])
                continue

            for summary in _iter_sr_summaries(r.json()):
                ev = summary.get("sport_event") or {}
                eid = ev.get("id")
                comps = _sr_event_competitors(summary)
                if not eid or len(comps) < 2:
                    continue

                hc = next((c for c in comps if c.get("qualifier") == "home"), None)
                ac = next((c for c in comps if c.get("qualifier") == "away"), None)
                if not hc or not ac:
                    continue

                home_code = next((code for code in NPB_SR_ALIASES if _sr_team_matches(code, hc, "NPB")), None)
                away_code = next((code for code in NPB_SR_ALIASES if _sr_team_matches(code, ac, "NPB")), None)
                if not home_code or not away_code:
                    continue

                start_raw = ev.get("start_time")
                if not start_raw:
                    continue
                try:
                    start_utc = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
                except Exception:
                    continue

                mins = (start_utc - now_utc).total_seconds() / 60
                out[eid] = {
                    "event_id": eid,
                    "sport": "baseball",
                    "league": "NPB",
                    "pick_group": "npb",
                    "home": home_code,
                    "away": away_code,
                    "start_utc": start_utc.isoformat(),
                    "minutes_to_start": round(mins),
                    "source": "Sportradar",
                    "sportradar_event_id": eid,
                    "sportradar_home_id": hc.get("id", ""),
                    "sportradar_away_id": ac.get("id", ""),
                    "sportradar_home_name": hc.get("name", ""),
                    "sportradar_away_name": ac.get("name", ""),
                }

        log.info("NPB Sportradar schedule candidates | count=%d", len(out))
    except Exception:
        log.exception("NPB Sportradar schedule fetch failed")

    return list(out.values())

def fetch_domestic_official_games():
    games = []

    # KBO: Sportradar schedule first, old source only as fallback.
    kbo_games = fetch_sportradar_kbo_upcoming_games()
    if kbo_games:
        games.extend(kbo_games)
    else:
        log.info("KBO Sportradar returned no candidate | fallback=official")
        games.extend(fetch_kbo_official_games())

    # NPB: Sportradar schedule first.
    npb_games = fetch_sportradar_npb_upcoming_games()
    if npb_games:
        games.extend(npb_games)
    else:
        log.info("NPB Sportradar returned no candidate | fallback=official")
        games.extend(fetch_npb_official_games())

    games.extend(fetch_kleague_official_games())
    return games


def fetch_major_upcoming_games(conn):
    now = datetime.now(timezone.utc)

    # 1) Try shared Sportradar cache first.
    refresh_baseball_schedule_cache(conn)
    games = load_cached_baseball_games(conn)

    cached_leagues = {g.get("league") for g in games if g.get("sport") == "baseball"}

    # 2) Sportradar unavailable/429/no cache -> league-specific schedule fallback.
    if "KBO" not in cached_leagues:
        try:
            kbo_fb = fetch_kbo_official_games()
            if kbo_fb:
                log.warning("KBO schedule fallback active | games=%d", len(kbo_fb))
                games.extend(kbo_fb)
        except Exception:
            log.exception("KBO schedule fallback failed")

    if "NPB" not in cached_leagues:
        try:
            npb_fb = fetch_npb_official_games()
            if npb_fb:
                log.warning("NPB schedule fallback active | games=%d", len(npb_fb))
                games.extend(npb_fb)
        except Exception:
            log.exception("NPB schedule fallback failed")

    # K League official source remains separate.
    try:
        games.extend(fetch_kleague_official_games())
    except Exception:
        log.exception("K League schedule fetch failed")

    # 3) ESPN handles non-baseball major sports and MLB schedule fallback.
    date_keys = {
        (now + timedelta(days=d)).strftime("%Y%m%d")
        for d in (-1, 0, 1)
    }

    for sport, league, league_name, pick_group in MAJOR_LEAGUES:
        # If MLB is already in Sportradar cache, don't duplicate it from ESPN.
        if league_name == "MLB" and "MLB" in cached_leagues:
            continue

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
                    status = ((comp.get("status") or {}).get("type") or {})
                    if status.get("completed"):
                        continue

                    start_raw = ev.get("date") or comp.get("date")
                    if not start_raw:
                        continue

                    start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                    if start <= now:
                        continue

                    competitors = comp.get("competitors") or []
                    if len(competitors) < 2:
                        continue

                    teams = []
                    for c in competitors:
                        t = c.get("team") or {}
                        teams.append({
                            "name": t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "",
                            "abbr": t.get("abbreviation") or "",
                            "homeAway": c.get("homeAway") or "",
                            "raw": c,
                        })

                    home = next((x for x in teams if x["homeAway"] == "home"), None)
                    away = next((x for x in teams if x["homeAway"] == "away"), None)
                    if not home or not away or not home["name"] or not away["name"]:
                        continue

                    game = {
                        "event_id": str(ev.get("id", "")),
                        "sport": sport,
                        "league": league_name,
                        "pick_group": pick_group,
                        "home": home["name"],
                        "away": away["name"],
                        "start_utc": start.isoformat(),
                        "minutes_to_start": round((start-now).total_seconds()/60),
                        "source": "ESPN",
                        "home_competitor": home["raw"],
                        "away_competitor": away["raw"],
                    }

                    if league_name == "MLB":
                        log.warning(
                            "MLB schedule fallback active | %s vs %s | start=%s",
                            away["name"], home["name"], start.isoformat()
                        )

                    games.append(game)

            except Exception:
                log.exception("Schedule fetch failed | %s %s", sport, league)

    # Deduplicate by league/home/away/start when source IDs differ.
    unique = {}
    for g in games:
        key = (
            g.get("league"),
            str(g.get("home") or "").lower(),
            str(g.get("away") or "").lower(),
            str(g.get("start_utc") or "")[:16],
        )
        # Prefer Sportradar copy over fallback source for same game.
        if key not in unique or g.get("source") == "Sportradar":
            unique[key] = g

    result = list(unique.values())
    log.info(
        "Upcoming candidates | total=%d | baseball=%d",
        len(result),
        sum(1 for g in result if g.get("sport") == "baseball")
    )
    return result


def recent_news_for_picks(conn, hours=48, limit=100):
    cutoff = hours_ago_iso(hours)
    rows = conn.execute(
        """SELECT title,source,summary,category,link,is_foreign,cached_at
           FROM article_cache
           WHERE cached_at >= ?
           ORDER BY cached_at DESC
           LIMIT ?""",
        (cutoff, limit),
    ).fetchall()

    return [{
        "title": r[0],
        "source": r[1] or "",
        "summary": r[2] or "",
        "category": r[3] or "",
        "link": r[4],
        "is_foreign": bool(r[5]),
        "cached_at": r[6],
    } for r in rows]


def picks_last_24h(conn):
    cutoff = hours_ago_iso(24)
    return conn.execute(
        "SELECT COUNT(*) FROM pick_logs WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()[0]


def event_pick_exists(conn, event_id):
    row = conn.execute(
        "SELECT 1 FROM prematch_picks WHERE event_id=? LIMIT 1",
        (str(event_id),),
    ).fetchone()
    return row is not None



def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))

def recent_form_component(game, side):
    recent = game.get(f"{side}_recent") or {}
    games = recent.get("games", 0) or 0
    if not games:
        return 50.0
    winrate = recent.get("wins", 0) / games
    run_diff = (recent.get("avg_for", 0) or 0) - (recent.get("avg_against", 0) or 0)
    return clamp(50 + (winrate - 0.5) * 40 + max(-12, min(12, run_diff * 3)))

def news_side_signals(game, news_items):
    result = {
        "home": {"starter": 50, "offense": 50, "bullpen": 50, "lineup": 50},
        "away": {"starter": 50, "offense": 50, "bullpen": 50, "lineup": 50},
    }

    positive = ["복귀", "호투", "선발 확정", "라인업 복귀", "타격감", "홈런",
                "return", "healthy", "starting", "hot streak", "activated"]
    negative = ["부상", "결장", "이탈", "휴식", "부진", "불펜 소모", "등판 불가",
                "injury", "out", "rest", "sidelined", "fatigue", "unavailable"]
    categories = {
        "starter": ["선발", "투수", "starter", "pitcher"],
        "bullpen": ["불펜", "마무리", "bullpen", "closer", "reliever"],
        "offense": ["타선", "타자", "타격", "홈런", "offense", "hitter", "batting"],
        "lineup": ["라인업", "결장", "복귀", "선발명단", "lineup", "injury", "return", "rest"],
    }

    for n in news_items[:100]:
        text = f'{n.get("title","")} {n.get("summary","")}'.lower()
        for side in ("home", "away"):
            team = str(game.get(side, "")).lower()
            if not team or team not in text:
                continue

            delta = 0
            if any(k.lower() in text for k in positive):
                delta += 5
            if any(k.lower() in text for k in negative):
                delta -= 7

            for cat, keys in categories.items():
                if any(k.lower() in text for k in keys):
                    result[side][cat] = clamp(result[side][cat] + delta)

    return result



def _extract_names_from_json(obj):
    names = []
    if isinstance(obj, dict):
        for key in ("name", "playerName", "displayName", "fullName", "shortName"):
            v = obj.get(key)
            if isinstance(v, str):
                v = re.sub(r"\s+", " ", v).strip()
                if v and v not in names:
                    names.append(v)
        for v in obj.values():
            for n in _extract_names_from_json(v):
                if n not in names:
                    names.append(n)
    elif isinstance(obj, list):
        for x in obj:
            for n in _extract_names_from_json(x):
                if n not in names:
                    names.append(n)
    return names

def _find_lineup_lists(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = f"{path}.{k}" if path else str(k)
            kl = str(k).lower()
            if isinstance(v, list) and any(t in kl for t in ("lineup", "starter", "starting", "battingorder", "batters")):
                ns = _extract_names_from_json(v)
                if LINEUP_MIN_PLAYERS <= len(ns) <= 18:
                    found.append((kp, ns))
            found.extend(_find_lineup_lists(v, kp))
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            found.extend(_find_lineup_lists(x, f"{path}[{i}]"))
    return found



KBO_SR_ALIASES = {
    "LG": ["lg", "lg twins", "twins"],
    "HANWHA": ["hanwha", "hanwha eagles", "eagles"],
    "SSG": ["ssg", "ssg landers", "landers"],
    "SAMSUNG": ["samsung", "samsung lions", "lions"],
    "NC": ["nc", "nc dinos", "dinos"],
    "KT": ["kt", "kt wiz", "wiz"],
    "LOTTE": ["lotte", "lotte giants", "giants"],
    "KIA": ["kia", "kia tigers", "tigers"],
    "DOOSAN": ["doosan", "doosan bears", "bears"],
    "KIWOOM": ["kiwoom", "kiwoom heroes", "heroes"],
}

NPB_SR_ALIASES = {
    "巨人": ["yomiuri", "yomiuri giants", "giants"],
    "DeNA": ["yokohama", "dena", "yokohama dena baystars", "baystars"],
    "ヤクルト": ["yakult", "tokyo yakult swallows", "swallows"],
    "阪神": ["hanshin", "hanshin tigers", "tigers"],
    "広島": ["hiroshima", "hiroshima carp", "carp"],
    "中日": ["chunichi", "chunichi dragons", "dragons"],
    "日本ハム": ["nippon ham", "hokkaido nippon-ham fighters", "fighters"],
    "ロッテ": ["lotte marines", "chiba lotte marines", "marines"],
    "楽天": ["rakuten", "tohoku rakuten golden eagles", "golden eagles"],
    "ソフトバンク": ["softbank", "fukuoka softbank hawks", "hawks"],
    "西武": ["seibu", "saitama seibu lions", "lions"],
    "オリックス": ["orix", "orix buffaloes", "buffaloes"],
}

MLB_SR_ALIASES = {
    "Arizona Diamondbacks": ["arizona diamondbacks", "diamondbacks"],
    "Atlanta Braves": ["atlanta braves", "braves"],
    "Baltimore Orioles": ["baltimore orioles", "orioles"],
    "Boston Red Sox": ["boston red sox", "red sox"],
    "Chicago Cubs": ["chicago cubs", "cubs"],
    "Chicago White Sox": ["chicago white sox", "white sox"],
    "Cincinnati Reds": ["cincinnati reds", "reds"],
    "Cleveland Guardians": ["cleveland guardians", "guardians"],
    "Colorado Rockies": ["colorado rockies", "rockies"],
    "Detroit Tigers": ["detroit tigers", "tigers"],
    "Houston Astros": ["houston astros", "astros"],
    "Kansas City Royals": ["kansas city royals", "royals"],
    "Los Angeles Angels": ["los angeles angels", "angels"],
    "Los Angeles Dodgers": ["los angeles dodgers", "dodgers"],
    "Miami Marlins": ["miami marlins", "marlins"],
    "Milwaukee Brewers": ["milwaukee brewers", "brewers"],
    "Minnesota Twins": ["minnesota twins", "twins"],
    "New York Mets": ["new york mets", "mets"],
    "New York Yankees": ["new york yankees", "yankees"],
    "Athletics": ["athletics", "oakland athletics"],
    "Philadelphia Phillies": ["philadelphia phillies", "phillies"],
    "Pittsburgh Pirates": ["pittsburgh pirates", "pirates"],
    "San Diego Padres": ["san diego padres", "padres"],
    "San Francisco Giants": ["san francisco giants", "giants"],
    "Seattle Mariners": ["seattle mariners", "mariners"],
    "St. Louis Cardinals": ["st louis cardinals", "st. louis cardinals", "cardinals"],
    "Tampa Bay Rays": ["tampa bay rays", "rays"],
    "Texas Rangers": ["texas rangers", "rangers"],
    "Toronto Blue Jays": ["toronto blue jays", "blue jays"],
    "Washington Nationals": ["washington nationals", "nationals"],
}


def _norm_sr_team(value):
    return re.sub(r"[^a-z0-9가-힣]", "", str(value or "").lower())

def _sportradar_headers():
    return {
        "accept": "application/json",
        "x-api-key": SPORTRADAR_API_KEY,
        "User-Agent": "SportNow/12",
    }

def _sr_team_matches(team_name, competitor, league=None):
    if league == "KBO":
        aliases = KBO_SR_ALIASES.get(str(team_name or "").upper(), [str(team_name or "").lower()])
    elif league == "NPB":
        aliases = NPB_SR_ALIASES.get(str(team_name or ""), [str(team_name or "").lower()])
    else:
        aliases = MLB_SR_ALIASES.get(str(team_name or ""), [str(team_name or "").lower()])

    fields = []
    if isinstance(competitor, dict):
        for k in ("name", "abbreviation", "short_name"):
            if competitor.get(k):
                fields.append(str(competitor.get(k)))
    blob = " ".join(fields).lower()
    compact = _norm_sr_team(blob)

    for alias in aliases:
        a = str(alias).lower()
        if a in blob or _norm_sr_team(a) in compact:
            return True
    return False


def _iter_sr_summaries(data):
    """Daily Summaries의 구조가 조금 달라도 sport_event 포함 객체를 찾음."""
    found = []
    def walk(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get("sport_event"), dict):
                found.append(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(data)

    # event id 기준 중복 제거
    uniq = {}
    for x in found:
        eid = (x.get("sport_event") or {}).get("id")
        if eid:
            uniq[eid] = x
    return list(uniq.values())

def _sr_event_is_kbo(summary):
    ctx = summary.get("sport_event_context") or {}
    if not ctx and isinstance(summary.get("sport_event"), dict):
        ctx = summary["sport_event"].get("sport_event_context") or {}

    comp = ctx.get("competition") or {}
    cat = ctx.get("category") or {}
    text = " ".join([
        str(comp.get("name", "")),
        str(comp.get("id", "")),
        str(cat.get("name", "")),
        str(cat.get("country_code", "")),
    ]).lower()

    # competition 이름에 KBO가 가장 신뢰도 높은 기준.
    return ("kbo" in text) or ("korea baseball" in text)

def _sr_event_competitors(summary):
    ev = summary.get("sport_event") or {}
    comps = ev.get("competitors") or summary.get("competitors") or []
    if isinstance(comps, dict):
        comps = comps.get("competitor") or comps.get("competitors") or []
    return comps if isinstance(comps, list) else []

def fetch_sportradar_baseball_event_id(game):
    """KBO/NPB/MLB 공통: 양 팀 + home/away + 경기시각으로 sport_event_id 매칭."""
    if not SPORTRADAR_API_KEY:
        return None

    league = game.get("league")
    if league not in ("KBO", "NPB", "MLB"):
        return None

    try:
        target = datetime.fromisoformat(game["start_utc"])
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)

        # 각 리그 현지 날짜 기준으로 Daily Summaries 호출
        tz = timezone(timedelta(hours=9)) if league in ("KBO", "NPB") else timezone.utc
        date_str = target.astimezone(tz).strftime("%Y-%m-%d")

        url = (
            f"{SPORTRADAR_BASE_URL}/baseball/{SPORTRADAR_ACCESS_LEVEL}/v2/"
            f"{SPORTRADAR_LANGUAGE}/schedules/{date_str}/summaries.json"
        )
        r = requests.get(url, headers=_sportradar_headers(), timeout=20)
        if r.status_code != 200:
            log.warning("Sportradar Daily Summaries failed | %s | HTTP %s | %s",
                        league, r.status_code, r.text[:180])
            return None

        candidates = []
        for summary in _iter_sr_summaries(r.json()):
            ev = summary.get("sport_event") or {}
            comps = _sr_event_competitors(summary)
            if len(comps) < 2:
                continue

            hm = [c for c in comps if _sr_team_matches(game.get("home"), c, league)]
            aw = [c for c in comps if _sr_team_matches(game.get("away"), c, league)]
            if not hm or not aw:
                continue

            for hc in hm:
                for ac in aw:
                    if hc.get("id") and hc.get("id") == ac.get("id"):
                        continue

                    score = 100
                    if hc.get("qualifier") == "home":
                        score += 25
                    if ac.get("qualifier") == "away":
                        score += 25

                    diff = 9999
                    try:
                        sd = datetime.fromisoformat(str(ev.get("start_time", "")).replace("Z", "+00:00"))
                        diff = abs((sd - target).total_seconds()) / 60
                        if diff <= 5:
                            score += 50
                        elif diff <= 30:
                            score += 25
                        elif diff <= 120:
                            score += 5
                    except Exception:
                        pass

                    ctx = summary.get("sport_event_context") or ev.get("sport_event_context") or {}
                    txt = str(ctx).lower()
                    if league.lower() in txt:
                        score += 20
                    if league == "KBO" and ("korea" in txt or "'kor'" in txt):
                        score += 10
                    if league == "NPB" and ("japan" in txt or "'jpn'" in txt):
                        score += 10
                    if league == "MLB" and ("mlb" in txt or "major league" in txt):
                        score += 10

                    candidates.append((score, diff, ev.get("id"), hc, ac))

        if not candidates:
            log.info("Sportradar %s event not matched | %s vs %s",
                     league, game.get("away"), game.get("home"))
            return None

        candidates.sort(key=lambda x: (-x[0], x[1]))
        score, diff, eid, hc, ac = candidates[0]
        if not eid:
            return None

        game["sportradar_home_id"] = hc.get("id", "")
        game["sportradar_away_id"] = ac.get("id", "")
        game["sportradar_home_name"] = hc.get("name", "")
        game["sportradar_away_name"] = ac.get("name", "")

        log.info(
            "Sportradar %s event matched | %s vs %s | %s | SR=%s vs %s | diff=%.1fm",
            league, game.get("away"), game.get("home"), eid,
            ac.get("name", ""), hc.get("name", ""), diff
        )
        return eid

    except Exception:
        log.exception("Sportradar %s event lookup failed", league)
        return None


def _sr_lineup_competitors(data):
    """Global Baseball lineups의 direct/nested competitor 구조를 모두 처리."""
    results=[]
    def walk(obj):
        if isinstance(obj,dict):
            if obj.get("qualifier") in ("home","away") and isinstance(obj.get("players"),list):
                results.append(obj)
            comp=obj.get("competitor")
            if isinstance(comp,dict) and isinstance(obj.get("players"),list):
                merged=dict(comp); merged["players"]=obj["players"]
                if merged.get("qualifier") in ("home","away"): results.append(merged)
            for v in obj.values(): walk(v)
        elif isinstance(obj,list):
            for v in obj: walk(v)
    walk(data)
    uniq={}
    for c in results:
        key=c.get("id") or f'{c.get("qualifier")}:{c.get("name")}'
        if key not in uniq or len(c.get("players") or [])>len(uniq[key].get("players") or []):
            uniq[key]=c
    return list(uniq.values())

def _sr_starters(competitor):
    players = competitor.get("players") or []
    starters = []

    for pl in players:
        if not isinstance(pl, dict) or pl.get("starter") is not True:
            continue

        starters.append({
            "name": str(pl.get("name") or "").strip(),
            "order": pl.get("order"),
            "type": pl.get("type"),
            "id": pl.get("id"),
        })

    # 타순 1~9 우선, 선발투수(order 없음)는 뒤.
    starters.sort(
        key=lambda x: (
            99 if x.get("order") is None else int(x.get("order")),
            x.get("name") or ""
        )
    )
    return starters

def fetch_sportradar_baseball_lineup(game):
    """Global Baseball lineups. Shared rate-limited request path."""
    league = str(game.get("league") or "").upper()
    event_id = str(game.get("sportradar_event_id") or game.get("event_id") or "")
    if not event_id:
        return {"home": [], "away": [], "source": ""}

    url = (
        f"{SPORTRADAR_BASE_URL}/baseball/{SPORTRADAR_ACCESS_LEVEL}/v2/"
        f"{SPORTRADAR_LANGUAGE}/sport_events/{event_id}/lineups.json"
    )
    data = _sr_get_json(url, label=f"{league} lineup {event_id}")
    if data is None:
        return {"home": [], "away": [], "source": ""}

    try:
        node = data.get("lineups")
        competitors = []
        if isinstance(node, dict):
            competitors = node.get("competitors") or node.get("competitor") or []
        elif isinstance(node, list):
            competitors = node
        if not competitors:
            competitors = data.get("competitors") or []
        if isinstance(competitors, dict):
            competitors = competitors.get("competitor") or list(competitors.values())

        home_all = []
        away_all = []
        home = []
        away = []

        for comp in competitors or []:
            if not isinstance(comp, dict):
                continue
            q = str(comp.get("qualifier") or "").lower()
            players = comp.get("players") or []
            if isinstance(players, dict):
                players = players.get("player") or list(players.values())
            players = [x for x in players if isinstance(x, dict)]
            starters = [x for x in players if x.get("starter") is True]
            starters.sort(key=lambda x: (x.get("order") is None, x.get("order", 999)))

            if q == "home":
                home_all, home = players, starters
            elif q == "away":
                away_all, away = players, starters

        log.info(
            "Sportradar %s lineup parsed | event=%s | competitors=%d | "
            "home_players=%d away_players=%d | home_starters=%d away_starters=%d",
            league, event_id, len(competitors or []),
            len(home_all), len(away_all), len(home), len(away)
        )

        def names(players):
            return [
                str(x.get("name") or "").strip()
                for x in players
                if str(x.get("name") or "").strip()
            ]

        return {
            "home": names(home),
            "away": names(away),
            "source": "Sportradar",
        }

    except Exception:
        log.exception("Sportradar %s lineup parse failed | event=%s", league, event_id)
        return {"home": [], "away": [], "source": ""}


def _team_aliases_kbo(name):
    m={
        "LG":["LG","LG 트윈스"],"HANWHA":["HANWHA","한화","한화 이글스"],
        "SSG":["SSG","SSG 랜더스"],"SAMSUNG":["SAMSUNG","삼성","삼성 라이온즈"],
        "NC":["NC","NC 다이노스"],"KT":["KT","KT 위즈"],"LOTTE":["LOTTE","롯데","롯데 자이언츠"],
        "KIA":["KIA","KIA 타이거즈"],"DOOSAN":["DOOSAN","두산","두산 베어스"],
        "KIWOOM":["KIWOOM","키움","키움 히어로즈"],
    }
    return m.get(str(name or "").upper(), [str(name or "")])

def _extract_json_blobs_from_html(txt):
    out=[]
    soup=BeautifulSoup(txt,"html.parser")
    for sc in soup.find_all("script"):
        raw=(sc.string or sc.get_text("",strip=False) or "").strip()
        if not raw: continue
        if sc.get("type")=="application/json" or sc.get("id")=="__NEXT_DATA__":
            try: out.append(json.loads(raw))
            except: pass
    return out

def fetch_kbo_naver_mobile_lineup(game):
    try:
        start=datetime.fromisoformat(game["start_utc"]).astimezone(timezone(timedelta(hours=9)))
        ds=start.strftime("%Y%m%d")
        url=f"{KBO_NAVER_MOBILE.rstrip('/')}/schedule/index"
        r=requests.get(url,params={"date":ds},headers={"User-Agent":"Mozilla/5.0","Referer":"https://m.sports.naver.com/"},timeout=15)
        if r.status_code!=200:
            return {"home":[],"away":[],"source":""}
        ha=_team_aliases_kbo(game.get("home")); aa=_team_aliases_kbo(game.get("away"))
        candidates=[]
        soup=BeautifulSoup(r.text,"html.parser")
        for a in soup.find_all("a",href=True):
            blob=re.sub(r"\s+"," ",a.get_text(" ",strip=True))+" "+a["href"]
            if any(x in blob for x in ha) and any(x in blob for x in aa):
                href=a["href"]
                candidates.append(href if href.startswith("http") else "https://m.sports.naver.com"+href)
        blobs=_extract_json_blobs_from_html(r.text)
        for b in blobs:
            hits=_find_lineup_lists(b)
            uniq=[]
            for _,ns in hits:
                if LINEUP_MIN_PLAYERS<=len(ns)<=18 and ns not in uniq: uniq.append(ns)
            if len(uniq)>=2:
                return {"home":uniq[0][:9],"away":uniq[1][:9],"source":"Naver Sports Mobile JSON"}
        for cu in candidates[:8]:
            try:
                gr=requests.get(cu,headers={"User-Agent":"Mozilla/5.0","Referer":url},timeout=15)
                if gr.status_code!=200: continue
                for b in _extract_json_blobs_from_html(gr.text):
                    hits=_find_lineup_lists(b); uniq=[]
                    for _,ns in hits:
                        if LINEUP_MIN_PLAYERS<=len(ns)<=18 and ns not in uniq: uniq.append(ns)
                    if len(uniq)>=2:
                        return {"home":uniq[0][:9],"away":uniq[1][:9],"source":"Naver Sports Mobile JSON"}
            except: pass
        return {"home":[],"away":[],"source":""}
    except Exception:
        log.exception("KBO Naver mobile lineup fetch failed")
        return {"home":[],"away":[],"source":""}

def fetch_kbo_naver_lineup(game):
    if not ENABLE_ASIA_LINEUP_FALLBACK:
        return {"home": [], "away": [], "source": ""}

    try:
        start = datetime.fromisoformat(game["start_utc"]).astimezone(timezone(timedelta(hours=9)))
        ds = start.strftime("%Y-%m-%d")
        r = requests.get(
            f"{KBO_NAVER_GATEWAY.rstrip('/')}/schedule/games",
            params={"upperCategoryId": "kbaseball", "fromDate": ds, "toDate": ds},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.sports.naver.com/"},
            timeout=15,
        )
        if r.status_code != 200:
            return {"home": [], "away": [], "source": ""}

        data = r.json()
        game_id = None

        def walk_games(obj):
            nonlocal game_id
            if game_id:
                return
            if isinstance(obj, dict):
                blob = json.dumps(obj, ensure_ascii=False).lower()
                if str(game.get("home", "")).lower() in blob and str(game.get("away", "")).lower() in blob:
                    gid = obj.get("gameId") or obj.get("game_id") or obj.get("id")
                    if gid:
                        game_id = gid
                        return
                for v in obj.values():
                    walk_games(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk_games(v)

        walk_games(data)
        if not game_id:
            return {"home": [], "away": [], "source": ""}

        rr = requests.get(
            f"{KBO_NAVER_GATEWAY.rstrip('/')}/schedule/games/{game_id}/relay",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.sports.naver.com/"},
            timeout=15,
        )
        if rr.status_code != 200:
            return {"home": [], "away": [], "source": ""}

        hits = _find_lineup_lists(rr.json())
        uniq = []
        for _, ns in hits:
            if ns not in uniq:
                uniq.append(ns)

        if len(uniq) >= 2:
            return {"home": uniq[0][:9], "away": uniq[1][:9], "source": "Naver Sports"}

        return {"home": [], "away": [], "source": ""}
    except Exception:
        log.exception("KBO Naver lineup fetch failed")
        return {"home": [], "away": [], "source": ""}

def fetch_npb_yahoo_lineup(game):
    if not ENABLE_ASIA_LINEUP_FALLBACK:
        return {"home": [], "away": [], "source": ""}

    try:
        r = requests.get(
            f"{NPB_YAHOO_BASE.rstrip('/')}/npb/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if r.status_code != 200:
            return {"home": [], "away": [], "source": ""}

        soup = BeautifulSoup(r.text, "html.parser")
        team_alias = {
            "巨人": ["巨人", "読売"], "DeNA": ["DeNA", "横浜"], "ヤクルト": ["ヤクルト", "東京ヤクルト"],
            "阪神": ["阪神"], "広島": ["広島"], "中日": ["中日"], "日本ハム": ["日本ハム", "北海道日本ハム"],
            "ロッテ": ["ロッテ", "千葉ロッテ"], "楽天": ["楽天", "東北楽天"],
            "ソフトバンク": ["ソフトバンク", "福岡ソフトバンク"], "西武": ["西武", "埼玉西武"],
            "オリックス": ["オリックス"],
        }
        hv = team_alias.get(game.get("home"), [str(game.get("home", ""))])
        av = team_alias.get(game.get("away"), [str(game.get("away", ""))])

        href = None
        for a in soup.find_all("a", href=True):
            h = a.get("href", "")
            if "/npb/game/" not in h:
                continue
            txt = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            if any(x in txt for x in hv) and any(x in txt for x in av):
                href = h
                break

        if not href:
            return {"home": [], "away": [], "source": ""}

        url = href if href.startswith("http") else NPB_YAHOO_BASE.rstrip("/") + href
        gr = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if gr.status_code != 200:
            return {"home": [], "away": [], "source": ""}

        gsoup = BeautifulSoup(gr.text, "html.parser")
        page = gsoup.get_text("\n", strip=True)
        if "スタメン" not in page and "オーダー" not in page:
            return {"home": [], "away": [], "source": ""}

        lineups = []
        for table in gsoup.find_all("table"):
            txt = table.get_text(" ", strip=True)
            if "スタメン" not in txt and "オーダー" not in txt:
                continue

            names = []
            for tr in table.find_all("tr"):
                cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
                if not cells:
                    continue
                joined = " ".join(cells)
                if re.search(r"(^|\s)[1-9](\s|$)", joined):
                    for c in cells:
                        if re.search(r"[一-龥ぁ-んァ-ンA-Za-z]", c) and len(c) <= 24:
                            if c not in names:
                                names.append(c)
                                break
            if len(names) >= LINEUP_MIN_PLAYERS:
                lineups.append(names[:9])

        if len(lineups) >= 2:
            return {"home": lineups[0], "away": lineups[1], "source": "SportsNavi"}

        return {"home": [], "away": [], "source": ""}
    except Exception:
        log.exception("NPB SportsNavi lineup fetch failed")
        return {"home": [], "away": [], "source": ""}

def enrich_asia_lineup(game, conn=None):
    league = game.get("league")
    if league not in ("KBO", "NPB", "MLB"):
        return game

    event_id = str(game.get("sportradar_event_id") or game.get("event_id") or "")

    # 1) confirmed DB cache = zero API calls forever for this event
    if conn and event_id:
        cached = _lineup_cache_get(conn, event_id)
        if cached and cached.get("confirmed"):
            game["home_lineup"] = cached["home"]
            game["away_lineup"] = cached["away"]
            game["lineup_source"] = cached["source"]
            return game

        # Unconfirmed cache: don't hammer the API every cycle.
        if cached and cached.get("checked_at"):
            try:
                checked = datetime.fromisoformat(cached["checked_at"])
                age = (datetime.now(timezone.utc) - checked).total_seconds()
                if age < SR_LINEUP_RECHECK_SECONDS:
                    return game
            except Exception:
                pass

    # 2) Don't waste lineup calls on very distant games.
    # This is NOT a pick time window: the game stays cached/candidate.
    try:
        start = datetime.fromisoformat(game["start_utc"])
        mins = (start - datetime.now(timezone.utc)).total_seconds() / 60
        if mins > SR_LINEUP_LOOKAHEAD_MINUTES:
            return game
        if mins <= 0:
            return game
    except Exception:
        pass

    # Only call Sportradar lineups when this game has a real Sportradar event id.
    if game.get("source") == "Sportradar" or game.get("sportradar_event_id"):
        info = fetch_sportradar_baseball_lineup(game)
    else:
        info = {"home": [], "away": [], "source": ""}

    # League-specific fallback only after Sportradar.
    if (not info.get("home") or not info.get("away")) and league == "KBO":
        info = fetch_kbo_naver_mobile_lineup(game)
        if not info.get("home") or not info.get("away"):
            info = fetch_kbo_naver_lineup(game)

    elif (not info.get("home") or not info.get("away")) and league == "NPB":
        info = fetch_npb_yahoo_lineup(game)

    confirmed = (
        len(info.get("home") or []) >= LINEUP_MIN_PLAYERS
        and len(info.get("away") or []) >= LINEUP_MIN_PLAYERS
    )

    if conn and event_id:
        _lineup_cache_set(conn, event_id, {
            "home": info.get("home") or [],
            "away": info.get("away") or [],
            "source": info.get("source") or "",
            "confirmed": confirmed,
        })

    if info.get("home"):
        game["home_lineup"] = info["home"]
    if info.get("away"):
        game["away_lineup"] = info["away"]
    if info.get("source"):
        game["lineup_source"] = info["source"]

    return game


def detect_confirmed_lineup(game):
    """실제 응답에 양 팀 선발 명단이 있을 때만 확정으로 판단."""
    def names(side):
        found = list(game.get(f"{side}_lineup") or [])
        comp = game.get(f"{side}_competitor") or {}
        pools = []
        if isinstance(comp, dict):
            for key in ("roster","lineup","starters","athletes"):
                v=comp.get(key)
                if isinstance(v,list): pools += v
                elif isinstance(v,dict):
                    for sub in ("athletes","entries","players","items"):
                        if isinstance(v.get(sub),list): pools += v[sub]
        for x in pools:
            if not isinstance(x,dict): continue
            a=x.get("athlete") if isinstance(x.get("athlete"),dict) else x
            n=a.get("displayName") or a.get("fullName") or a.get("shortName") or x.get("name")
            if n: found.append(str(n))
        return list(dict.fromkeys(found))
    h,a=names("home"),names("away")
    return {"confirmed":len(h)>=LINEUP_MIN_PLAYERS and len(a)>=LINEUP_MIN_PLAYERS,
            "home_players":h,"away_players":a,"home_count":len(h),"away_count":len(a)}

def baseball_model_score(game, news_items):
    """KBO/NPB/MLB 전용 상대 우세 모델.
    값은 승률이 아니라 0~100 PRIME SCORE.
    라인업 정보가 없으면 lineup/news signal의 기본값 50(중립)을 사용한다."""
    signals = news_side_signals(game, news_items)
    hr = game.get("home_recent") or {}
    ar = game.get("away_recent") or {}

    def offense_component(recent, news_score):
        base = 50
        if recent.get("games"):
            base += ((recent.get("avg_for", 0) or 0) - 4.0) * 4
        return clamp(base * 0.65 + news_score * 0.35)

    def bullpen_component(recent, news_score):
        base = 50
        if recent.get("games"):
            base += (4.0 - (recent.get("avg_against", 0) or 0)) * 3
        return clamp(base * 0.55 + news_score * 0.45)

    home_components = {
        "starter": signals["home"]["starter"],
        "offense": offense_component(hr, signals["home"]["offense"]),
        "bullpen": bullpen_component(hr, signals["home"]["bullpen"]),
        "form": recent_form_component(game, "home"),
        "lineup": signals["home"]["lineup"],
    }
    away_components = {
        "starter": signals["away"]["starter"],
        "offense": offense_component(ar, signals["away"]["offense"]),
        "bullpen": bullpen_component(ar, signals["away"]["bullpen"]),
        "form": recent_form_component(game, "away"),
        "lineup": signals["away"]["lineup"],
    }

    weights = {
        "starter": BASEBALL_STARTER_WEIGHT,
        "offense": BASEBALL_OFFENSE_WEIGHT,
        "bullpen": BASEBALL_BULLPEN_WEIGHT,
        "form": BASEBALL_FORM_WEIGHT,
        "lineup": BASEBALL_LINEUP_WEIGHT,
    }

    home_raw = sum(home_components[k] * weights[k] for k in weights)
    away_raw = sum(away_components[k] * weights[k] for k in weights)
    diff = home_raw - away_raw

    home_score = clamp(50 + diff * 0.9, 35, 75)
    away_score = 100 - home_score

    return {
        "home_prime_score": round(home_score, 1),
        "away_prime_score": round(away_score, 1),
        "home_components": {k: round(v, 1) for k, v in home_components.items()},
        "away_components": {k: round(v, 1) for k, v in away_components.items()},
    }


def select_prematch_top_picks(games, news_items, conn=None):
    # v14.6:
    # Baseball waits for a confirmed lineup until 30 minutes before first pitch.
    # If lineup is still unavailable at <=30 minutes, analysis proceeds without it.
    enriched = []
    for g in games:
        if g.get("league") in ("KBO", "NPB", "MLB"):
            g = enrich_asia_lineup(g, conn)
            st = g.get("lineup_status") or detect_confirmed_lineup(g)
            g["lineup_status"] = st

            try:
                start_dt = datetime.fromisoformat(str(g.get("start_utc") or "").replace("Z", "+00:00"))
                mins_to_start = (start_dt - datetime.now(timezone.utc)).total_seconds() / 60
            except Exception:
                mins_to_start = g.get("minutes_to_start")
                try:
                    mins_to_start = float(mins_to_start)
                except Exception:
                    mins_to_start = 999999

            if st.get("confirmed"):
                log.info(
                    "Lineup available -> analyze | %s | %s vs %s | %.1f min | home=%d away=%d | source=%s",
                    g.get("league"), g.get("away"), g.get("home"), mins_to_start,
                    st.get("home_count", 0), st.get("away_count", 0),
                    g.get("lineup_source", "none")
                )
            elif mins_to_start > LINEUP_WAIT_UNTIL_MINUTES:
                log.info(
                    "Waiting lineup until T-%dm | %s | %s vs %s | %.1f min | home=%d away=%d | source=%s",
                    LINEUP_WAIT_UNTIL_MINUTES,
                    g.get("league"), g.get("away"), g.get("home"), mins_to_start,
                    st.get("home_count", 0), st.get("away_count", 0),
                    g.get("lineup_source", "none")
                )
                continue
            else:
                g["lineup_deadline_fallback"] = True
                log.warning(
                    "T-%dm reached without lineup -> analyze without lineup | %s | %s vs %s | %.1f min",
                    LINEUP_WAIT_UNTIL_MINUTES,
                    g.get("league"), g.get("away"), g.get("home"), mins_to_start
                )

        enriched.append(g)

    games = enriched
    if not games:
        log.info("No games ready for analysis yet")
        return []

    games = build_free_game_context(games)
    for g in games:
        g["base_home_edge"] = basic_model_score(g)
        if g.get("sport") == "baseball" or g.get("league") in ("KBO", "NPB", "MLB"):
            g["lineup_status"] = detect_confirmed_lineup(g)
            g["baseball_model"] = baseball_model_score(g, news_items)

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
너는 SPORT NOW 프리매치 인텔리전스 엔진이다.

분석 대상은 아래 메이저리그 예정 경기만 허용한다.
제공된 경기 데이터와 라인업을 우선 사용하고, 최근 뉴스는 보조 근거로 사용한다. 외부 지식이나 기억으로 사실을 만들어내지 않는다.

우선순위:
1. 부상/결장
2. 선발투수 또는 선발 변경
3. 확정/예상 라인업과 주전 휴식
4. 징계/복귀/로스터 변경
5. 감독의 출전 관련 공식 발언

규칙:
- 경기 데이터와 라인업 등 분석 근거가 있으면 뉴스가 없어도 분석할 수 있다.
- 절대 개수를 채우려고 하지 않는다.
- 픽 개수 상한은 없다. 기준을 충족하는 경기는 모두 반환할 수 있다.
- 각 pick_group에서 기준을 충족하는 모든 강한 경기를 검토한다.
- KBO와 NPB는 서로 다른 그룹이며 절대 합산하지 않는다.
- 같은 그룹이라도 기준을 충족하는 경기는 개수 제한 없이 유지한다.
- 분석 가능한 경기는 점수와 관계없이 출력할 수 있다.
- 데이터가 부족하거나 분석 자체가 불가능한 경기만 제외한다.
- probability는 실제 통계 승률이 아니라 '무료 경기 데이터 + 뉴스 기반 AI 추정 우세도'.
- base_home_edge는 최근 경기 성적/득실만으로 계산한 홈팀 기준점이다.
- 야구 경기에는 baseball_model이 제공된다.
- 야구 라인업은 확보되면 반드시 반영한다. 경기 30분 전까지 라인업이 확보되지 않은 경우에만 라인업 없이 나머지 검증된 데이터로 분석한다.
- 확인되지 않은 출전 명단은 임의로 추측하지 않는다. 라인업이 없으면 그 항목은 중립 정보로 두고 다른 검증된 데이터로 판단한다.
- KBO/NPB/MLB는 반드시 baseball_model을 우선 참고한다.
- 야구 가중치는 선발 30%, 타선 25%, 불펜 20%, 최근 팀 흐름 15%, 라인업/결장/기타 10%다.
- 선발투수 하나만으로 승부를 판단하지 않는다.
- 경기 시작 전 확인할 수 없는 '오늘 컨디션'은 추측하지 않는다.
- component 차이가 작으면 강한 확신을 내리지 않는다.
- 확정 부상/결장/라인업 정보가 강하면 조정할 수 있다.
- probability는 1~99 정수이며 참고용 우세도다.
- confidence는 high 또는 medium.
- high 신뢰도를 우선하고, medium은 근거가 충분할 때만 허용한다.
- 종목/리그 인기도보다 실제 데이터 근거 강도를 우선한다.
- pick_side는 반드시 "home" 또는 "away".
- source_ids는 제공된 기사 ID만.
- comment는 2~3문장으로, 왜 이 팀이 우세하다고 판단했는지 자연스럽게 설명한다.
- 분석 근거와 comment에 baseball_model, base_home_edge, source_ids, event_id 같은 내부 변수명을 절대 쓰지 않는다.
- 기사 번호(ID 1, ID 59 같은 표기)를 문장에 절대 쓰지 않는다.
- 팀명을 문장에 쓸 때는 가능한 한 자연스러운 한국어 팀명으로 표현한다.
- 과장 표현, 확정적 표현, 결과 보장 표현은 금지.
- PRIME SCORE는 참고용이다. 점수와 관계없이 분석 가능한 경기는 반환한다.
- 점수 때문에 경기를 제외하지 않는다.
- JSON 배열만 출력.

[
  {{
    "event_id":"123",
    "pick_side":"home",
    "probability":64,
    "confidence":"medium",
    "reasons":["근거1","근거2"],
    "comment":"모델 코멘트 2~3문장",
    "source_ids":[1,5]
  }}
]

예정 경기:
{json.dumps(games, ensure_ascii=False)}

최근 뉴스:
{json.dumps(news, ensure_ascii=False)}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    data = parse_json_output(response.output_text)

    if not isinstance(data, list):
        return []

    game_map = {str(g["event_id"]): g for g in games}
    result = []

    for x in data:
        eid = str(x.get("event_id", ""))
        if eid not in game_map:
            continue

        side = x.get("pick_side")
        if side not in ("home", "away"):
            continue

        try:
            prob = int(x.get("probability", 0))
        except Exception:
            continue

        if not (1 <= prob <= 99):
            continue

        if x.get("confidence") not in ("high", "medium"):
            continue

        source_ids = x.get("source_ids") or []
        reasons = x.get("reasons") or []

        if not reasons:
            continue

        g = game_map[eid]
        x["_game"] = g
        x["pick_group"] = g.get("pick_group", "other")
        x["pick_team"] = g[side]
        x["probability"] = prob
        result.append(x)

    result.sort(
        key=lambda x: (x["confidence"] == "high", x["probability"]),
        reverse=True,
    )

    # v13.3.9: 개수 제한 없음. 분석 가능한 모든 픽 반환.
    result.sort(
        key=lambda x: (x["confidence"] == "high", x["probability"]),
        reverse=True,
    )
    return result



TEAM_KO = {
    "Arizona Diamondbacks":"애리조나 다이아몬드백스","Atlanta Braves":"애틀랜타 브레이브스",
    "Baltimore Orioles":"볼티모어 오리올스","Boston Red Sox":"보스턴 레드삭스",
    "Chicago Cubs":"시카고 컵스","Chicago White Sox":"시카고 화이트삭스",
    "Cincinnati Reds":"신시내티 레즈","Cleveland Guardians":"클리블랜드 가디언스",
    "Colorado Rockies":"콜로라도 로키스","Detroit Tigers":"디트로이트 타이거스",
    "Houston Astros":"휴스턴 애스트로스","Kansas City Royals":"캔자스시티 로열스",
    "Los Angeles Angels":"LA 에인절스","Los Angeles Dodgers":"LA 다저스",
    "Miami Marlins":"마이애미 말린스","Milwaukee Brewers":"밀워키 브루어스",
    "Minnesota Twins":"미네소타 트윈스","New York Mets":"뉴욕 메츠",
    "New York Yankees":"뉴욕 양키스","Athletics":"애슬레틱스",
    "Philadelphia Phillies":"필라델피아 필리스","Pittsburgh Pirates":"피츠버그 파이리츠",
    "San Diego Padres":"샌디에이고 파드리스","San Francisco Giants":"샌프란시스코 자이언츠",
    "Seattle Mariners":"시애틀 매리너스","St. Louis Cardinals":"세인트루이스 카디널스",
    "Tampa Bay Rays":"탬파베이 레이스","Texas Rangers":"텍사스 레인저스",
    "Toronto Blue Jays":"토론토 블루제이스","Washington Nationals":"워싱턴 내셔널스",
    "Arsenal":"아스널","Chelsea":"첼시","Liverpool":"리버풀","Manchester City":"맨체스터 시티",
    "Manchester United":"맨체스터 유나이티드","Tottenham Hotspur":"토트넘 홋스퍼",
    "Real Madrid":"레알 마드리드","Barcelona":"바르셀로나","Bayern Munich":"바이에른 뮌헨",
    "Borussia Dortmund":"보루시아 도르트문트","Inter Milan":"인터 밀란","AC Milan":"AC 밀란",
    "Juventus":"유벤투스","Napoli":"나폴리","Paris Saint-Germain":"파리 생제르맹",
    "LG":"LG 트윈스","HANWHA":"한화 이글스","SSG":"SSG 랜더스","SAMSUNG":"삼성 라이온즈",
    "NC":"NC 다이노스","KT":"KT 위즈","LOTTE":"롯데 자이언츠","KIA":"KIA 타이거즈",
    "DOOSAN":"두산 베어스","KIWOOM":"키움 히어로즈",
    "巨人":"요미우리 자이언츠","DeNA":"요코하마 DeNA 베이스타스","ヤクルト":"야쿠르트 스왈로스",
    "阪神":"한신 타이거스","広島":"히로시마 도요 카프","中日":"주니치 드래곤즈",
    "日本ハム":"닛폰햄 파이터스","ロッテ":"지바 롯데 마린스","楽天":"라쿠텐 골든이글스",
    "ソフトバンク":"소프트뱅크 호크스","西武":"세이부 라이온즈","オリックス":"오릭스 버팔로스",
    "강원":"강원FC","광주":"광주FC","김천":"김천상무","대전":"대전하나시티즌",
    "부천":"부천FC 1995","서울":"FC서울","안양":"FC안양","울산":"울산 HD",
    "인천":"인천 유나이티드","전북":"전북 현대","제주":"제주 SK","포항":"포항 스틸러스",
}

def ko_team(name):
    name = str(name or "").strip()
    return TEAM_KO.get(name, name)

def clean_analysis_text(text):
    text = str(text or "")
    for a,b in {
        "baseball_model":"야구 종합 지표",
        "base_home_edge":"최근 흐름 지표",
        "home_prime_score":"홈팀 우세 지표",
        "away_prime_score":"원정팀 우세 지표",
    }.items():
        text = text.replace(a,b)
    text = re.sub(r"\s*\(?ID\s*\d+\)?", "", text, flags=re.I)
    text = re.sub(r"\s*\[ID\s*\d+\]", "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()

def format_prematch_pick(pick, news_items):
    g = pick["_game"]
    start_kst = datetime.fromisoformat(g["start_utc"]).astimezone(
        timezone(timedelta(hours=9))
    )

    reasons = "\n".join(
        f"• {html.escape(clean_analysis_text(str(r)))}"
        for r in pick.get("reasons", [])[:3]
    )

    source_lines = []
    for sid in pick.get("source_ids", [])[:3]:
        try:
            idx = int(sid) - 1
            if 0 <= idx < len(news_items):
                n = news_items[idx]
                source_lines.append(
                    f'• <a href="{html.escape(n["link"], quote=True)}">'
                    f'{html.escape(n["source"] or "관련 기사")}</a>'
                )
        except Exception:
            pass

    sources = "\n".join(source_lines) or "• 관련 스포츠 뉴스"
    conf = "높음" if pick["confidence"] == "high" else "보통"

    return (
        f"⚡ <b>SPORT NOW PRIME PICK</b>\n\n"
        f"🏆 {html.escape(g['league'])}\n"
        f"🏟 <b>{html.escape(ko_team(g['away']))} vs {html.escape(ko_team(g['home']))}</b>\n"
        f"⏰ 경기 시작: {start_kst.strftime('%m/%d %H:%M')} KST\n\n"
        + (
            "✅ <b>LINEUP CONFIRMED</b>\n"
            if (g.get("lineup_status") or {}).get("confirmed") else ""
        )
        + f"🎯 모델 선택: <b>{html.escape(ko_team(pick['pick_team']))}</b>\n"
        f"📈 PRIME SCORE: <b>{pick['probability']}%</b>\n"
        f"🔎 신뢰도: <b>{conf}</b>\n⏱ 경기 약 1시간 전 최종 분석\n\n"
        f"<b>분석 근거</b>\n{reasons}\n\n"
        f"💬 <b>PRIME COMMENT</b>\n"
        f"{html.escape(clean_analysis_text(str(pick.get('comment') or '현재 확인 가능한 경기 전 정보 기준으로 우세한 흐름이 감지됩니다.')))}\n\n"
        + (
            f"⚾ <b>BASEBALL MODEL</b>\n"
            f"선발 30% · 타선 25% · 불펜 20% · 최근 흐름 15% · 라인업/결장 10%\n\n"
            if g.get("sport") == "baseball" or g.get("league") in ("KBO","NPB","MLB")
            else ""
        )
        + f"<b>관련 기사</b>\n{sources}\n\n"
        f"⚠️ PRIME SCORE는 실제 승률이 아니라 경기 전 공개 데이터와 최신 팀 정보를 종합한 상대우세 지표이며 결과를 보장하지 않습니다.\n"
        f"🔑 가입코드: <b>{html.escape(PROMO_CODE)}</b>"
    )



def max_picks_for_group(grp):
    return {
        "mlb": MAX_PICKS_MLB,
        "kbo": MAX_PICKS_KBO,
        "npb": MAX_PICKS_NPB,
        "soccer": MAX_PICKS_SOCCER,
        "basketball": MAX_PICKS_BASKETBALL,
    }.get(grp, MAX_PICKS_PER_GROUP)

def group_pick_counts_last_24h(conn):
    cutoff = hours_ago_iso(24)
    rows = conn.execute(
        """SELECT league FROM prematch_picks
           WHERE posted_at >= ?""",
        (cutoff,),
    ).fetchall()

    counts = {"kbo": 0, "npb": 0, "mlb": 0, "soccer": 0, "basketball": 0}

    for (league,) in rows:
        if league == "KBO":
            counts["kbo"] += 1
        elif league == "NPB":
            counts["npb"] += 1
        elif league == "MLB":
            counts["mlb"] += 1
        elif league in ("KBL", "NBA"):
            counts["basketball"] += 1
        else:
            counts["soccer"] += 1

    return counts


def combo_recently_posted(conn, combo_key):
    row = conn.execute(
        "SELECT 1 FROM combo_pick_logs WHERE combo_key=? LIMIT 1",
        (combo_key,)
    ).fetchone()
    return row is not None

def build_combo_posts(picks):
    """같은 그룹 내 고득점 픽을 2개씩 묶는다.
    최대 COMBO_MAX_PER_GROUP개 조합."""
    groups = {}
    for p in picks:
        g = p.get("_game") or {}
        grp = g.get("pick_group", "other")
        if int(p.get("probability", 0)) < COMBO_MIN_SCORE:
            continue
        groups.setdefault(grp, []).append(p)

    combos = []
    for grp, arr in groups.items():
        arr.sort(
            key=lambda x: (x.get("confidence") == "high", int(x.get("probability", 0))),
            reverse=True
        )

        # 1+2, 3+4 식으로 2폴 구성
        pair_no = 1
        for i in range(0, len(arr) - 1, 2):
            if pair_no > COMBO_MAX_PER_GROUP:
                break
            a, b = arr[i], arr[i+1]
            ga, gb = a["_game"], b["_game"]

            combo_key = f"{grp}:{ga['event_id']}+{gb['event_id']}"
            combos.append({
                "combo_key": combo_key,
                "pick_group": grp,
                "pair_no": pair_no,
                "a": a,
                "b": b,
                "avg_score": round((int(a["probability"]) + int(b["probability"])) / 2, 1),
            })
            pair_no += 1

    return combos

def format_combo_post(combo):
    a, b = combo["a"], combo["b"]
    ga, gb = a["_game"], b["_game"]

    group_name = {
        "kbo": "KBO",
        "npb": "NPB",
        "mlb": "MLB",
        "soccer": "축구",
        "basketball": "농구",
    }.get(combo["pick_group"], "SPORTS")

    return (
        f"🔥 <b>SPORT NOW FINAL COMBO</b>\\n\\n"
        f"🏷 {html.escape(group_name)} · 조합 {combo['pair_no']}\\n\\n"
        f"1️⃣ <b>{html.escape(ko_team(ga['away']))} vs {html.escape(ko_team(ga['home']))}</b>\\n"
        f"🎯 {html.escape(ko_team(a['pick_team']))} · PRIME SCORE {a['probability']}\\n\\n"
        f"2️⃣ <b>{html.escape(ko_team(gb['away']))} vs {html.escape(ko_team(gb['home']))}</b>\\n"
        f"🎯 {html.escape(ko_team(b['pick_team']))} · PRIME SCORE {b['probability']}\\n\\n"
        f"📊 조합 평균 SCORE: <b>{combo['avg_score']}</b>\\n"
        f"💬 개별 분석을 통과한 상위 픽 중 같은 그룹의 두 경기를 조합했습니다.\\n\\n"
        f"⚠️ 조합은 참고용 분석이며 결과를 보장하지 않습니다.\n"
        f"🔑 가입코드: <b>{html.escape(PROMO_CODE)}</b>"
    )

def post_combo_picks(conn, picks):
    if not ENABLE_COMBO_PICKS:
        return

    for combo in build_combo_posts(picks):
        if combo_recently_posted(conn, combo["combo_key"]):
            continue

        try:
            text = format_combo_post(combo)
            send_telegram(text, promo_button=True)
            conn.execute(
                """INSERT INTO combo_pick_logs
                   (combo_key, pick_group, combo_text, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    combo["combo_key"],
                    combo["pick_group"],
                    text,
                    utcnow_iso(),
                )
            )
            conn.commit()
            log.info("COMBO PICK POSTED | %s", combo["combo_key"])
            time.sleep(2)
        except Exception:
            log.exception("Combo pick post failed | %s", combo["combo_key"])

def maybe_post_prematch_picks(conn):
    if not ENABLE_NEWS_PICKS:
        return

    games = [
        g for g in fetch_major_upcoming_games(conn)
        if not event_pick_exists(conn, g["event_id"])
    ]

    if not games:
        log.info("No eligible upcoming games")
        return

    news_items = recent_news_for_picks(conn, 48, 100)
    if len(news_items) < 3:
        log.info("Limited cached news | count=%d | continuing with game/lineup data", len(news_items))

    try:
        picks = select_prematch_top_picks(games, news_items, conn)
    except Exception:
        log.exception("Prematch AI analysis failed")
        return

    for pick in picks:
        g = pick["_game"]
        event_id = str(g["event_id"])

        try:
            text = format_prematch_pick(pick, news_items)
            send_telegram(text, promo_button=True)

            now = utcnow_iso()

            conn.execute(
                "INSERT INTO pick_logs (matchup_key,pick_text,created_at) VALUES (?,?,?)",
                (f"event:{event_id}", text, now),
            )

            if conn.pg:
                conn.execute(
                    """INSERT INTO prematch_picks
                       (event_id,league,sport,home_team,away_team,pick_team,
                        probability,confidence,start_utc,posted_at,result_status,result_posted)
                       VALUES (?,?,?,?,?,?,?,?,?,?,'pending',0)
                       ON CONFLICT (event_id) DO NOTHING""",
                    (
                        event_id, g["league"], g["sport"], g["home"], g["away"],
                        pick["pick_team"], pick["probability"], pick["confidence"],
                        g["start_utc"], now,
                    ),
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO prematch_picks
                       (event_id,league,sport,home_team,away_team,pick_team,
                        probability,confidence,start_utc,posted_at,result_status,result_posted)
                       VALUES (?,?,?,?,?,?,?,?,?,?,'pending',0)""",
                    (
                        event_id, g["league"], g["sport"], g["home"], g["away"],
                        pick["pick_team"], pick["probability"], pick["confidence"],
                        g["start_utc"], now,
                    ),
                )

            conn.commit()

            log.info(
                "PREMATCH PICK POSTED | %s vs %s | pick=%s | %s%%",
                g["away"], g["home"], pick["pick_team"], pick["probability"]
            )
            time.sleep(2)

        except Exception:
            log.exception("Prematch pick send/store failed | %s", event_id)



# =========================
# RESULTS
# =========================
LEAGUE_ENDPOINTS = {
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



def fetch_cached_baseball_result(conn, event_id):
    # Refresh may also update scores/status, but TTL prevents hammering.
    refresh_baseball_schedule_cache(conn)

    row = conn.execute(
        """SELECT league,home_team,away_team,status,home_score,away_score
           FROM sport_schedule_cache WHERE event_id=?""",
        (event_id,),
    ).fetchone()
    if not row:
        return None

    league, home, away, status, hs, aws = row
    status = str(status or "").lower()

    completed = status in (
        "closed", "ended", "finished", "complete", "completed", "after_penalties"
    )
    if not completed or hs is None or aws is None:
        return None

    winner = ""
    if hs > aws:
        winner = home
    elif aws > hs:
        winner = away

    return {
        "home_team": home,
        "away_team": away,
        "home_score": int(hs),
        "away_score": int(aws),
        "winner_team": winner,
    }


def fetch_event_result(event_id, league_name):
    pair = LEAGUE_ENDPOINTS.get(league_name)
    if not pair:
        return None

    sport, league = pair
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary"

    try:
        r = requests.get(url, params={"event": event_id}, timeout=15)
        if r.status_code != 200:
            return None

        data = r.json()
        comps = (data.get("header") or {}).get("competitions") or []
        if not comps:
            return None

        comp = comps[0]
        status = ((comp.get("status") or {}).get("type") or {})
        if not status.get("completed"):
            return None

        home = away = None

        for c in comp.get("competitors", []):
            team = (c.get("team") or {}).get("displayName") or ""
            score_raw = c.get("score", "0")

            try:
                score = int(float(score_raw))
            except Exception:
                score = 0

            row = {
                "team": team,
                "score": score,
                "winner": bool(c.get("winner", False)),
            }

            if c.get("homeAway") == "home":
                home = row
            elif c.get("homeAway") == "away":
                away = row

        if not home or not away:
            return None

        winner_team = ""
        if home["score"] > away["score"]:
            winner_team = home["team"]
        elif away["score"] > home["score"]:
            winner_team = away["team"]

        return {
            "home_team": home["team"],
            "away_team": away["team"],
            "home_score": home["score"],
            "away_score": away["score"],
            "winner_team": winner_team,
        }

    except Exception:
        log.exception("Result fetch failed | event=%s", event_id)
        return None


def norm_team(name):
    return re.sub(r"[^a-z0-9가-힣]", "", (name or "").lower())


def same_team(a, b):
    aa, bb = norm_team(a), norm_team(b)
    return bool(aa and bb and (aa == bb or aa in bb or bb in aa))


def result_stats(conn):
    rows = conn.execute(
        """SELECT result_status,settled_at
           FROM prematch_picks
           WHERE result_status IN ('hit','miss')"""
    ).fetchall()

    cutoff = hours_ago_iso(24)

    total_hit = total_miss = today_hit = today_miss = 0

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
    event_id, league, home_team, away_team, pick_team, probability = row

    icon = "✅ 적중" if final_status == "hit" else "❌ 미적중"

    result_comment = (
        "사전 분석에서 포착한 우세 흐름이 실제 결과로 이어졌습니다."
        if final_status == "hit"
        else "사전 우세 판단과 실제 결과가 엇갈렸습니다. 다음 분석에서는 해당 변수를 재점검합니다."
    )

    today_total = stats["today_hit"] + stats["today_miss"]
    total_total = stats["total_hit"] + stats["total_miss"]

    today_rate = (
        stats["today_hit"] / today_total * 100
        if today_total else 0
    )
    total_rate = (
        stats["total_hit"] / total_total * 100
        if total_total else 0
    )

    return (
        f"🏁 <b>SPORT NOW PRIME RESULT</b>\n\n"
        f"🏆 {html.escape(league)}\n"
        f"🏟 <b>{html.escape(ko_team(away_team))} {result['away_score']} : "
        f"{result['home_score']} {html.escape(ko_team(home_team))}</b>\n\n"
        f"🎯 PRIME PICK: <b>{html.escape(ko_team(pick_team))}</b>\n"
        f"📈 PRIME SCORE: <b>{probability}%</b>\n"
        f"📌 결과: <b>{icon}</b>\n\n"
        f"💬 <b>RESULT COMMENT</b>\n{result_comment}\n\n"
        f"📈 최근 24시간: {stats['today_hit']}승 {stats['today_miss']}패 "
        f"({today_rate:.1f}%)\n"
        f"📚 누적: {stats['total_hit']}승 {stats['total_miss']}패 "
        f"({total_rate:.1f}%)\n\n"
        f"⚠️ 프리매치 모델 기록이며 결과를 보장하지 않습니다."
    )


def settle_finished_picks(conn):
    if not ENABLE_RESULT_POSTS:
        return

    rows = conn.execute(
        """SELECT event_id,league,home_team,away_team,pick_team,probability
           FROM prematch_picks
           WHERE result_status='pending'
             AND start_utc <= ?
           ORDER BY start_utc ASC
           LIMIT 20""",
        (utcnow_iso(),),
    ).fetchall()

    for row in rows:
        event_id, league, home_team, away_team, pick_team, probability = row

        if league in ("KBO", "NPB", "MLB"):
            result = fetch_cached_baseball_result(conn, event_id)
        elif league in ("K League 1", "KBL"):
            continue
        else:
            result = fetch_event_result(event_id, league)
        if not result:
            continue

        # 팀 승리 픽이므로 무승부도 미적중 처리
        final_status = (
            "hit"
            if result["winner_team"] and same_team(pick_team, result["winner_team"])
            else "miss"
        )

        settled = utcnow_iso()

        conn.execute(
            """UPDATE prematch_picks
               SET result_status=?,home_score=?,away_score=?,winner_team=?,
                   settled_at=?,result_posted=0
               WHERE event_id=?""",
            (
                final_status,
                result["home_score"],
                result["away_score"],
                result["winner_team"],
                settled,
                event_id,
            ),
        )
        conn.commit()

        stats = result_stats(conn)
        text = format_result_post(row, result, final_status, stats)

        try:
            send_telegram(text)
            conn.execute(
                "UPDATE prematch_picks SET result_posted=1 WHERE event_id=?",
                (event_id,),
            )
            conn.commit()
            log.info("RESULT POSTED | event=%s | %s", event_id, final_status)
            time.sleep(2)
        except Exception:
            log.exception("Result Telegram post failed | event=%s", event_id)


# =========================
# MAIN
# =========================
def main():
    conn = db()

    article_count = conn.execute(
        "SELECT COUNT(*) FROM sent_articles"
    ).fetchone()[0]

    if article_count == 0 and FIRST_RUN_SKIP_EXISTING:
        seed_existing(conn)

    log.info(
        "SportNow v14.6 started | channel=%s | interval=%ss | postgres=%s",
        CHANNEL_ID,
        CHECK_INTERVAL,
        bool(DATABASE_URL),
    )

    while True:
        try:
            run_news_cycle(conn)
        except Exception:
            log.exception("News cycle error")

        try:
            maybe_post_prematch_picks(conn)
        except Exception:
            log.exception("Prematch cycle error")

        try:
            settle_finished_picks(conn)
        except Exception:
            log.exception("Result cycle error")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
