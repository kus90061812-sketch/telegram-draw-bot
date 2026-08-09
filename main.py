import os
import re
import time
import html
import json
import hashlib
import sqlite3
import logging
from datetime import datetime, timezone
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
FIRST_RUN_SKIP_EXISTING = os.getenv("FIRST_RUN_SKIP_EXISTING", "true").lower() == "true"
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DB_PATH = os.getenv("DB_PATH", "sports_news.db")

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

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_articles (
            fingerprint TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            sent_at TEXT NOT NULL
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
    rows = conn.execute(
        "SELECT title FROM sent_articles WHERE datetime(sent_at) >= datetime('now','-18 hours') ORDER BY sent_at DESC LIMIT 250"
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
        "INSERT OR IGNORE INTO sent_articles VALUES (?, ?, ?, ?)",
        (fp, title, link, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

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

def send_telegram(text: str):
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

def format_post(category, title_ko, summary_ko, source, link, is_foreign):
    safe_title = html.escape(title_ko)
    safe_summary = html.escape(summary_ko)
    safe_source = html.escape(source)
    safe_link = html.escape(link, quote=True)

    translation_badge = "\n🌐 해외 기사 자동번역" if is_foreign else ""

    summary_block = f"\n\n{safe_summary}" if safe_summary else ""

    return (
        f"{category}{translation_badge}\n\n"
        f"<b>{safe_title}</b>"
        f"{summary_block}\n\n"
        f"📰 출처: {safe_source}\n"
        f'🔗 <a href="{safe_link}">원문 기사 보기</a>\n\n'
        f"#스포츠뉴스 #실시간스포츠"
    )

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
                    published_ts = time.mktime(entry.published_parsed)

                items.append({
                    "category": category,
                    "entry": entry,
                    "title": title,
                    "link": link,
                    "is_foreign": is_foreign,
                    "published_ts": published_ts,
                })
        except Exception:
            log.exception("Feed read failed: %s", url)

    unique = {}
    for item in items:
        key = (item["title"].lower(), item["link"])
        if key not in unique or item["published_ts"] > unique[key]["published_ts"]:
            unique[key] = item

    return sorted(unique.values(), key=lambda x: (importance_score(x), x["published_ts"]), reverse=True)

def seed_existing(conn):
    items = collect_entries()
    for item in items:
        fp = fingerprint(item["title"], item["link"])
        mark_sent(conn, fp, item["title"], item["link"])
    log.info("첫 실행: 기존 기사 %d건 스킵 처리", len(items))

def process_article(item):
    entry = item["entry"]
    original_title = item["title"]
    source = clean_source(entry)
    summary = article_context(entry)

    if item["is_foreign"]:
        try:
            return (*ai_translate_and_summarize(original_title, summary, source), source)
        except Exception:
            log.exception("해외기사 AI 번역 실패: %s", original_title)
            # 번역 실패 시 영어 기사를 그대로 올리지 않고 건너뜀
            raise
    else:
        try:
            title_ko, summary_ko = ai_summarize_korean(original_title, summary, source)
            return title_ko, summary_ko, source
        except Exception:
            log.exception("국내기사 AI 요약 실패. 제목만 전송: %s", original_title)
            return original_title, "", source

def run_cycle(conn):
    items = collect_entries()
    sent_count = 0
    daily_count = posts_last_24h(conn)

    if daily_count >= DAILY_POST_LIMIT:
        log.info("Daily limit reached | %d/%d", daily_count, DAILY_POST_LIMIT)
        return

    for item in items:
        if sent_count >= MAX_POSTS_PER_CYCLE or daily_count + sent_count >= DAILY_POST_LIMIT:
            break

        fp = fingerprint(item["title"], item["link"])
        if already_sent(conn, fp):
            continue
        if is_similar_recent(conn, item["title"]):
            mark_sent(conn, fp, item["title"], item["link"])
            log.info("SIMILAR SKIP | %s", item["title"])
            continue

        try:
            title_ko, summary_ko, source = process_article(item)

            text = format_post(
                item["category"],
                title_ko,
                summary_ko,
                source,
                item["link"],
                item["is_foreign"]
            )

            send_telegram(text)
            mark_sent(conn, fp, item["title"], item["link"])
            sent_count += 1
            log.info("POSTED | %s", title_ko)
            time.sleep(2)

        except Exception:
            # 실패한 기사는 DB에 sent 처리하지 않으므로 다음 주기에 재시도
            log.exception("Article processing/send failed: %s", item["title"])

    log.info("Cycle complete | posted=%d | candidates=%d", sent_count, len(items))

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
        except Exception:
            log.exception("Cycle error")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
