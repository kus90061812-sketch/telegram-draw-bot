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
MAX_POSTS_PER_CYCLE = int(os.getenv("MAX_POSTS_PER_CYCLE", "5"))
DAILY_POST_LIMIT = int(os.getenv("DAILY_POST_LIMIT", "40"))
FIRST_RUN_SKIP_EXISTING = os.getenv("FIRST_RUN_SKIP_EXISTING", "true").lower() == "true"
SUMMARIZE_KOREAN = os.getenv("SUMMARIZE_KOREAN", "true").lower() == "true"
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

ENABLE_NEWS_PICKS = os.getenv("ENABLE_NEWS_PICKS", "true").lower() == "true"
PREMATCH_MIN_MINUTES = int(os.getenv("PREMATCH_MIN_MINUTES", "45"))
PREMATCH_MAX_MINUTES = int(os.getenv("PREMATCH_MAX_MINUTES", "70"))
MAX_PICKS_PER_DAY = int(os.getenv("MAX_PICKS_PER_DAY", "8"))
MIN_NEWS_EDGE = int(os.getenv("MIN_NEWS_EDGE", "55"))
ENABLE_FREE_TEAM_DATA = os.getenv("ENABLE_FREE_TEAM_DATA", "true").lower() == "true"
RECENT_GAMES_LOOKBACK = int(os.getenv("RECENT_GAMES_LOOKBACK", "5"))
MAX_PICKS_PER_GROUP = int(os.getenv("MAX_PICKS_PER_GROUP", "2"))

ENABLE_RESULT_POSTS = os.getenv("ENABLE_RESULT_POSTS", "true").lower() == "true"
POST_NEWS_PUBLICLY = os.getenv("POST_NEWS_PUBLICLY", "false").lower() == "true"
BASEBALL_STARTER_WEIGHT = float(os.getenv("BASEBALL_STARTER_WEIGHT", "0.30"))
BASEBALL_OFFENSE_WEIGHT = float(os.getenv("BASEBALL_OFFENSE_WEIGHT", "0.25"))
BASEBALL_BULLPEN_WEIGHT = float(os.getenv("BASEBALL_BULLPEN_WEIGHT", "0.20"))
BASEBALL_FORM_WEIGHT = float(os.getenv("BASEBALL_FORM_WEIGHT", "0.15"))
BASEBALL_LINEUP_WEIGHT = float(os.getenv("BASEBALL_LINEUP_WEIGHT", "0.10"))


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
        for n in notes[:5]:
            h = n.get("headline") or ""
            if h:
                context["notes"].append(h[:220])

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
def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
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
    if daily_count >= DAILY_POST_LIMIT:
        log.info("Daily news limit reached | %d/%d", daily_count, DAILY_POST_LIMIT)
        return

    sent_count = 0

    for item in items:
        if sent_count >= MAX_POSTS_PER_CYCLE:
            break
        if daily_count + sent_count >= DAILY_POST_LIMIT:
            break

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
    "KBO": "asia_baseball",
    "NPB": "asia_baseball",
    "K League 1": "soccer",
    "KBL": "basketball",
}



def _within_prematch_window(start_utc):
    now = datetime.now(timezone.utc)
    mins = (start_utc - now).total_seconds() / 60
    return PREMATCH_MIN_MINUTES <= mins <= PREMATCH_MAX_MINUTES, round(mins)

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
                "pick_group": "asia_baseball",
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
                "pick_group": "asia_baseball",
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

def fetch_domestic_official_games():
    games = []
    games.extend(fetch_kbo_official_games())
    games.extend(fetch_npb_official_games())
    games.extend(fetch_kleague_official_games())

    # KBL은 8월 현재 비시즌. 존재하지 않는 ESPN 코드 호출은 하지 않음.
    # 시즌 재개 후 공식 KBL 일정 소스를 별도 연결하면 된다.
    return games


def fetch_major_upcoming_games():
    now = datetime.now(timezone.utc)
    games = fetch_domestic_official_games()
    date_keys = {
        (now + timedelta(days=d)).strftime("%Y%m%d")
        for d in (-1, 0, 1, 2)
    }

    for sport, league, league_name, pick_group in MAJOR_LEAGUES:
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
                    mins = (start - now).total_seconds() / 60

                    if mins < PREMATCH_MIN_MINUTES or mins > PREMATCH_MAX_MINUTES:
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
                        })

                    home = next((x for x in teams if x["homeAway"] == "home"), None)
                    away = next((x for x in teams if x["homeAway"] == "away"), None)

                    if not home or not away or not home["name"] or not away["name"]:
                        continue

                    games.append({
                        "event_id": str(ev.get("id", "")),
                        "sport": sport,
                        "league": league_name,
                        "pick_group": pick_group,
                        "home": home["name"],
                        "away": away["name"],
                        "start_utc": start.isoformat(),
                        "minutes_to_start": round(mins),
                    })

            except Exception:
                log.exception("Schedule fetch failed | %s %s", sport, league)

    unique = {}
    for g in games:
        key = g["event_id"] or f'{g["league"]}|{g["home"]}|{g["away"]}|{g["start_utc"]}'
        unique[key] = g

    return list(unique.values())


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

def baseball_model_score(game, news_items):
    """KBO/NPB/MLB 전용 상대 우세 모델.
    값은 승률이 아니라 0~100 PRIME SCORE."""
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


def select_prematch_top_picks(games, news_items):
    if not games or len(news_items) < 3:
        return []

    games = build_free_game_context(games)
    for g in games:
        g["base_home_edge"] = basic_model_score(g)
        if g.get("sport") == "baseball" or g.get("league") in ("KBO", "NPB", "MLB"):
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
제공된 최근 뉴스만 근거로 사용하고 외부 지식, 기억, 임의 통계, 배당은 사용하지 않는다.

우선순위:
1. 부상/결장
2. 선발투수 또는 선발 변경
3. 확정/예상 라인업과 주전 휴식
4. 징계/복귀/로스터 변경
5. 감독의 출전 관련 공식 발언

규칙:
- 근거 없는 경기는 제외.
- 절대 개수를 채우려고 하지 않는다.
- 각 pick_group별로 가장 강한 경기만 고른다.
- asia_baseball(KBO+NPB), mlb, soccer, basketball 각각 최대 2개 이하.
- 같은 그룹 안에서 비슷하면 더 강한 1개만 선택한다.
- 가장 근거가 강하고 한쪽 우세가 뚜렷한 경기만 출력한다.
- 애매하면 빈 배열 []을 출력해도 된다.
- probability는 실제 통계 승률이 아니라 '무료 경기 데이터 + 뉴스 기반 AI 추정 우세도'.
- base_home_edge는 최근 경기 성적/득실만으로 계산한 홈팀 기준점이다.
- 야구 경기에는 baseball_model이 제공된다.
- KBO/NPB/MLB는 반드시 baseball_model을 우선 참고한다.
- 야구 가중치는 선발 30%, 타선 25%, 불펜 20%, 최근 팀 흐름 15%, 라인업/결장/기타 10%다.
- 선발투수 하나만으로 승부를 판단하지 않는다.
- 경기 시작 전 확인할 수 없는 '오늘 컨디션'은 추측하지 않는다.
- component 차이가 작으면 강한 확신을 내리지 않는다.
- 확정 부상/결장/라인업 정보가 강하면 조정할 수 있다.
- probability는 {MIN_NEWS_EDGE}~75 정수.
- confidence는 high 또는 medium.
- probability가 55 미만인 경기는 절대 출력하지 않는다.
- high 신뢰도를 우선하고, medium은 근거가 충분할 때만 허용한다.
- 종목/리그 인기도보다 실제 데이터 근거 강도를 우선한다.
- pick_side는 반드시 "home" 또는 "away".
- source_ids는 제공된 기사 ID만.
- comment는 2~3문장으로, 왜 이 팀이 우세하다고 판단했는지 자연스럽게 설명한다.
- 분석 근거와 comment에 baseball_model, base_home_edge, source_ids, event_id 같은 내부 변수명을 절대 쓰지 않는다.
- 기사 번호(ID 1, ID 59 같은 표기)를 문장에 절대 쓰지 않는다.
- 팀명을 문장에 쓸 때는 가능한 한 자연스러운 한국어 팀명으로 표현한다.
- 과장 표현, 확정적 표현, 결과 보장 표현은 금지.
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

    for x in data[:4]:
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

        if not (MIN_NEWS_EDGE <= prob <= 75):
            continue

        if x.get("confidence") not in ("high", "medium"):
            continue

        source_ids = x.get("source_ids") or []
        reasons = x.get("reasons") or []

        if not source_ids or not reasons:
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

    # 그룹별 품질 우선: 억지로 채우지 않고 각 그룹 상위만.
    grouped = {}
    for item in result:
        grp = item.get("pick_group", "other")
        grouped.setdefault(grp, [])
        if len(grouped[grp]) < MAX_PICKS_PER_GROUP:
            grouped[grp].append(item)

    final = []
    # 아시아 야구는 KBO+NPB를 한 묶음으로 먼저 비교
    for grp in ("asia_baseball", "mlb", "soccer", "basketball"):
        final.extend(grouped.get(grp, []))

    final.sort(
        key=lambda x: (x["confidence"] == "high", x["probability"]),
        reverse=True,
    )
    return final



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
        f"🎯 모델 선택: <b>{html.escape(ko_team(pick['pick_team']))}</b>\n"
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
        f"⚠️ PRIME SCORE는 실제 승률이 아니라 경기 전 공개 데이터와 최신 팀 정보를 종합한 상대우세 지표이며 결과를 보장하지 않습니다."
    )



def group_pick_counts_last_24h(conn):
    cutoff = hours_ago_iso(24)
    rows = conn.execute(
        """SELECT league FROM prematch_picks
           WHERE posted_at >= ?""",
        (cutoff,),
    ).fetchall()

    counts = {"asia_baseball": 0, "mlb": 0, "soccer": 0, "basketball": 0}

    for (league,) in rows:
        if league in ("KBO", "NPB"):
            counts["asia_baseball"] += 1
        elif league == "MLB":
            counts["mlb"] += 1
        elif league in ("KBL", "NBA"):
            counts["basketball"] += 1
        else:
            counts["soccer"] += 1

    return counts

def maybe_post_prematch_picks(conn):
    if not ENABLE_NEWS_PICKS:
        return

    used = picks_last_24h(conn)
    if used >= MAX_PICKS_PER_DAY:
        log.info("Pick daily limit reached | %d/%d", used, MAX_PICKS_PER_DAY)
        return

    games = [
        g for g in fetch_major_upcoming_games()
        if not event_pick_exists(conn, g["event_id"])
    ]

    if not games:
        log.info("No eligible major games in prematch window")
        return

    news_items = recent_news_for_picks(conn, 48, 100)
    if len(news_items) < 3:
        log.info("Not enough cached news for prematch analysis")
        return

    try:
        picks = select_prematch_top_picks(games, news_items)
    except Exception:
        log.exception("Prematch AI analysis failed")
        return

    remaining = MAX_PICKS_PER_DAY - used

    group_counts = group_pick_counts_last_24h(conn)

    for pick in picks:
        if remaining <= 0:
            break

        g = pick["_game"]
        grp = g.get("pick_group", "other")

        if grp in group_counts and group_counts[grp] >= MAX_PICKS_PER_GROUP:
            continue

        event_id = str(g["event_id"])

        try:
            text = format_prematch_pick(pick, news_items)
            send_telegram(text)

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
            remaining -= 1
            if grp in group_counts:
                group_counts[grp] += 1

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

        # 해외 메이저는 ESPN summary로 자동 결과판정.
        # KBO/NPB/K리그 공식 결과 파서는 다음 단계에서 별도 확장 가능.
        if league in ("KBO", "NPB", "K League 1", "KBL"):
            continue

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
        "SportNow v10.1 started | channel=%s | interval=%ss | postgres=%s",
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
