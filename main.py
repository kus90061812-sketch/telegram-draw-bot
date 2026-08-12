import psycopg2
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
DATABASE_URL = os.getenv("DATABASE_URL", "")

def db():
    """Open Railway PostgreSQL connection."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL)


def main():
    conn = db()

    article_count = conn.execute(
        "SELECT COUNT(*) FROM sent_articles"
    ).fetchone()[0]

    if article_count == 0 and FIRST_RUN_SKIP_EXISTING:
        seed_existing(conn)

    log.info(
        "SportNow v13.3.1 started | channel=%s | interval=%ss | postgres=%s",
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
