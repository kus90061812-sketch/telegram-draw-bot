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

    # 픽 분석용으로는 게시 한도와 무관하게 캐시
    for item in items:
        try:
            cache_article(conn, item)
        except Exception:
            log.exception("Article cache failed | %s", item["title"])

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
    # 야구: KBO + NPB는 같은 그룹, MLB 별도
    ("baseball", "kbo", "KBO", "asia_baseball"),
    ("baseball", "npb", "NPB", "asia_baseball"),
    ("baseball", "mlb", "MLB", "mlb"),

    # 축구: 인기 리그만
    ("soccer", "kor.1", "K League 1", "soccer"),
    ("soccer", "eng.1", "EPL", "soccer"),
    ("soccer", "esp.1", "La Liga", "soccer"),
    ("soccer", "ger.1", "Bundesliga", "soccer"),
    ("soccer", "ita.1", "Serie A", "soccer"),
    ("soccer", "fra.1", "Ligue 1", "soccer"),
    ("soccer", "uefa.champions", "UEFA Champions League", "soccer"),

    # 농구: 인기 리그만
    ("basketball", "kbl", "KBL", "basketball"),
    ("basketball", "nba", "NBA", "basketball"),
]


def fetch_major_upcoming_games():
    now = datetime.now(timezone.utc)
    games = []
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
                    if league_name in ("KBO", "NPB", "KBL", "K League 1"):
                        log.info("Schedule source unavailable | %s | HTTP %s", league_name, r.status_code)
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


def select_prematch_top_picks(games, news_items):
    if not games or len(news_items) < 3:
        return []

    games = build_free_game_context(games)
    for g in games:
        g["base_home_edge"] = basic_model_score(g)

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
너는 SPORT NOW 경기 전 뉴스 분석 엔진이다.

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
- base_home_edge를 무시하지 말되, 부상/결장/라인업 뉴스가 강하면 조정할 수 있다.
- probability는 {MIN_NEWS_EDGE}~75 정수.
- confidence는 high 또는 medium.
- probability가 55 미만인 경기는 절대 출력하지 않는다.
- high 신뢰도를 우선하고, medium은 근거가 충분할 때만 허용한다.
- 종목/리그 인기도보다 실제 데이터 근거 강도를 우선한다.
- pick_side는 반드시 "home" 또는 "away".
- source_ids는 제공된 기사 ID만.
- JSON 배열만 출력.

[
  {{
    "event_id":"123",
    "pick_side":"home",
    "probability":64,
    "confidence":"medium",
    "reasons":["근거1","근거2"],
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


def format_prematch_pick(pick, news_items):
    g = pick["_game"]
    start_kst = datetime.fromisoformat(g["start_utc"]).astimezone(
        timezone(timedelta(hours=9))
    )

    reasons = "\n".join(
        f"• {html.escape(str(r))}"
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
        f"🔥 <b>SPORT NOW BEST PICK</b>\n\n"
        f"🏆 {html.escape(g['league'])}\n"
        f"🏟 <b>{html.escape(g['away'])} vs {html.escape(g['home'])}</b>\n"
        f"⏰ 경기 시작: {start_kst.strftime('%m/%d %H:%M')} KST\n\n"
        f"✅ 예상 승리팀: <b>{html.escape(pick['pick_team'])}</b>\n"
        f"📊 데이터+뉴스 예상 우세도: <b>{pick['probability']}%</b>\n"
        f"🔎 신뢰도: <b>{conf}</b>\n⏱ 경기 약 1시간 전 최종 분석\n\n"
        f"<b>분석 근거</b>\n{reasons}\n\n"
        f"<b>관련 기사</b>\n{sources}\n\n"
        f"⚠️ 무료 경기 데이터와 뉴스 기반 AI 추정치이며 실제 배당모델의 확정 승률이나 결과 보장이 아닙니다."
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
        f"🏁 <b>SPORT NOW PICK RESULT</b>\n\n"
        f"🏆 {html.escape(league)}\n"
        f"🏟 <b>{html.escape(away_team)} {result['away_score']} : "
        f"{result['home_score']} {html.escape(home_team)}</b>\n\n"
        f"🎯 사전 PICK: <b>{html.escape(pick_team)}</b>\n"
        f"📊 데이터+뉴스 예상 우세도: <b>{probability}%</b>\n"
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
        "SportNow v8 started | channel=%s | interval=%ss | postgres=%s",
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
