import os, re, json, time, hashlib, logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import psycopg
from psycopg.rows import dict_row

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@sportnow0")
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "180"))
POST_INTERVAL = int(os.getenv("TELEGRAM_POST_INTERVAL_SECONDS", "120"))
LOOKAHEAD_HOURS = int(os.getenv("LOOKAHEAD_HOURS", "24"))
LINEUP_RECHECK_SECONDS = int(os.getenv("LINEUP_RECHECK_SECONDS", "180"))
NEW_PICK_CUTOFF_MINUTES = int(os.getenv("NEW_PICK_CUTOFF_MINUTES", "30"))
NEWS_LOOKBACK_HOURS = int(os.getenv("NEWS_LOOKBACK_HOURS", "72"))
PROMO_URL = os.getenv("PROMO_URL", "")
PROMO_BUTTON_TEXT = os.getenv("PROMO_BUTTON_TEXT", "바로가기")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
REVIEW_LOOKBACK = int(os.getenv("REVIEW_LOOKBACK", "20"))

client = OpenAI(api_key=OPENAI_API_KEY)
log = logging.getLogger("sportnow-v15")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 SPORT-NOW/15"})

# No odds. No Sportradar.
ESPN_LEAGUES = [
    ("soccer","eng.1","EPL","football"),
    ("soccer","esp.1","LaLiga","football"),
    ("soccer","ger.1","Bundesliga","football"),
    ("soccer","ita.1","Serie A","football"),
    ("soccer","fra.1","Ligue 1","football"),
    ("soccer","uefa.champions","UCL","football"),
    ("soccer","kor.1","K League 1","football"),
    ("basketball","nba","NBA","basketball"),
]

BASEBALL_LEAGUES = {"MLB","KBO","NPB"}

TEAM_KO = {
    "Arizona Diamondbacks":"애리조나 다이아몬드백스",
    "Athletics":"애슬레틱스",
    "Atlanta Braves":"애틀랜타 브레이브스",
    "Baltimore Orioles":"볼티모어 오리올스",
    "Boston Red Sox":"보스턴 레드삭스",
    "Chicago Cubs":"시카고 컵스",
    "Chicago White Sox":"시카고 화이트삭스",
    "Cincinnati Reds":"신시내티 레즈",
    "Cleveland Guardians":"클리블랜드 가디언스",
    "Colorado Rockies":"콜로라도 로키스",
    "Detroit Tigers":"디트로이트 타이거스",
    "Houston Astros":"휴스턴 애스트로스",
    "Kansas City Royals":"캔자스시티 로열스",
    "Los Angeles Angels":"LA 에인절스",
    "Los Angeles Dodgers":"LA 다저스",
    "Miami Marlins":"마이애미 말린스",
    "Milwaukee Brewers":"밀워키 브루어스",
    "Minnesota Twins":"미네소타 트윈스",
    "New York Mets":"뉴욕 메츠",
    "New York Yankees":"뉴욕 양키스",
    "Philadelphia Phillies":"필라델피아 필리스",
    "Pittsburgh Pirates":"피츠버그 파이리츠",
    "San Diego Padres":"샌디에이고 파드리스",
    "San Francisco Giants":"샌프란시스코 자이언츠",
    "Seattle Mariners":"시애틀 매리너스",
    "St. Louis Cardinals":"세인트루이스 카디널스",
    "Tampa Bay Rays":"탬파베이 레이스",
    "Texas Rangers":"텍사스 레인저스",
    "Toronto Blue Jays":"토론토 블루제이스",
    "Washington Nationals":"워싱턴 내셔널스",
}
def ko_team(x): return TEAM_KO.get(str(x or "").strip(), str(x or "").strip())
def now_utc(): return datetime.now(timezone.utc)
def parse_dt(x): return datetime.fromisoformat(str(x).replace("Z","+00:00"))
def norm(x): return re.sub(r"[^a-z0-9가-힣ぁ-んァ-ン一-龥]","",str(x or "").lower())
def game_key(g):
    return "|".join([g["league"], norm(g["home"]), norm(g["away"]), g["start_utc"][:16]])

def get_json(url, params=None, timeout=15):
    """GET JSON with ESPN domain fallback.

    ESPN's public-facing endpoints can return 403 selectively from hosted
    environments. For ESPN Site API requests, retry the equivalent
    site.web.api.espn.com host before giving up.
    """
    urls=[url]
    if "://site.api.espn.com/" in url:
        urls.append(url.replace("://site.api.espn.com/", "://site.web.api.espn.com/", 1))

    last_status=None
    for i,u in enumerate(urls):
        try:
            r=S.get(u,params=params,timeout=timeout)
            last_status=r.status_code
            if r.status_code==200:
                if i:
                    log.info("ESPN FALLBACK OK | %s",u)
                return r.json()
            if r.status_code in (403,404,429,500,502,503,504):
                log.warning("HTTP %s | %s%s",r.status_code,u,
                            " | trying fallback" if i+1<len(urls) else "")
                continue
            log.warning("HTTP %s | %s",r.status_code,u)
        except Exception:
            log.exception("GET failed | %s",u)
    return None

# ---------- schedules ----------
def espn_games():
    out=[]
    now=now_utc()
    dates={(now+timedelta(days=d)).strftime("%Y%m%d") for d in (0,1)}
    for sport,slug,label,kind in ESPN_LEAGUES:
        for ds in dates:
            data=get_json(
                f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard",
                {"dates":ds}
            )
            if not data:
                # ESPN CDN is a separate fallback path for scoreboard data.
                cdn=get_json(
                    f"https://cdn.espn.com/core/{sport}/scoreboard",
                    {"xhr":"1","league":slug,"dates":ds}
                )
                if cdn:
                    data=(cdn.get("content") or {}).get("sbData") or cdn.get("scoreboard") or cdn
            if not data:
                log.warning("ESPN SCOREBOARD UNAVAILABLE | %s | %s",slug,ds)
                continue
            for ev in data.get("events",[]):
                comp=(ev.get("competitions") or [{}])[0]
                status=((comp.get("status") or {}).get("type") or {})
                if status.get("completed"): continue
                try: start=parse_dt(ev.get("date") or comp.get("date"))
                except: continue
                if not (now < start <= now+timedelta(hours=LOOKAHEAD_HOURS)): continue
                home=away=None
                for c in comp.get("competitors") or []:
                    t=c.get("team") or {}
                    name=t.get("displayName") or t.get("shortDisplayName") or t.get("name")
                    if c.get("homeAway")=="home": home=name
                    if c.get("homeAway")=="away": away=name
                if home and away:
                    out.append({"event_id":f"espn:{sport}:{slug}:{ev.get('id')}",
                        "provider_id":str(ev.get("id")),"provider":"espn","sport":kind,
                        "league":label,"home":home,"away":away,"start_utc":start.isoformat()})
    return out

def mlb_games():
    out=[]; now=now_utc()
    for d in (0,1):
        day=(now+timedelta(days=d)).date().isoformat()
        data=get_json("https://statsapi.mlb.com/api/v1/schedule",
            {"sportId":1,"date":day,"hydrate":"probablePitcher"})
        if not data: continue
        for block in data.get("dates",[]):
            for x in block.get("games",[]):
                try:start=parse_dt(x["gameDate"])
                except:continue
                if not(now < start <= now+timedelta(hours=LOOKAHEAD_HOURS)):continue
                teams=x.get("teams") or {}
                home=((teams.get("home") or {}).get("team") or {}).get("name")
                away=((teams.get("away") or {}).get("team") or {}).get("name")
                hp=(((teams.get("home") or {}).get("probablePitcher") or {}).get("fullName") or "")
                ap=(((teams.get("away") or {}).get("probablePitcher") or {}).get("fullName") or "")
                if home and away:
                    out.append({"event_id":f"mlb:{x['gamePk']}","provider_id":str(x["gamePk"]),
                        "provider":"mlb","sport":"baseball","league":"MLB","home":home,"away":away,
                        "start_utc":start.isoformat(),"home_starter":hp,"away_starter":ap})
    return out


def mlb_odds(game):
    """ESPN MLB pregame odds matched by exact home/away teams.
    No extra API key required. Returns empty dict when the market is absent.
    """
    try:
        start=parse_dt(game["start_utc"])
        ds=start.strftime("%Y%m%d")
        data=get_json(
            "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
            {"dates":ds}
        )
        if not data:
            cdn=get_json(
                "https://cdn.espn.com/core/baseball/scoreboard",
                {"xhr":"1","league":"mlb","dates":ds}
            )
            if cdn:
                data=(cdn.get("content") or {}).get("sbData") or cdn.get("scoreboard") or cdn
        if not data:
            return {}

        for ev in data.get("events",[]):
            comp=(ev.get("competitions") or [{}])[0]
            home=away=""
            for c in comp.get("competitors") or []:
                t=c.get("team") or {}
                name=t.get("displayName") or t.get("shortDisplayName") or t.get("name") or ""
                if c.get("homeAway")=="home": home=name
                elif c.get("homeAway")=="away": away=name

            if not (same_team(game["home"],home) and same_team(game["away"],away)):
                continue

            markets=comp.get("odds") or []
            if not markets:
                return {}

            o=markets[0] or {}
            provider=(o.get("provider") or {}).get("name") or ""
            h=o.get("homeTeamOdds") or {}
            a=o.get("awayTeamOdds") or {}

            def price(x):
                for k in ("moneyLine","moneyline","value","close"):
                    if x.get(k) not in (None,""):
                        try:return int(float(x.get(k)))
                        except:return x.get(k)
                return None

            return {
                "provider":provider,
                "home_moneyline":price(h),
                "away_moneyline":price(a),
                "spread":o.get("spread"),
                "over_under":o.get("overUnder"),
                "details":o.get("details") or "",
            }
    except Exception:
        log.exception("MLB odds fetch failed | %s", game_key(game))
    return {}


def american_implied_probability(ml):
    try:
        ml=float(ml)
    except Exception:
        return None
    if ml < 0:
        return round((-ml)/((-ml)+100)*100,1)
    if ml > 0:
        return round(100/(ml+100)*100,1)
    return None

def kbo_games():
    """KBO official Daily Schedule parser. No Naver private/undocumented gateway."""
    out=[]; now=now_utc(); kst=timezone(timedelta(hours=9))
    team_codes={"LG","HANWHA","SSG","SAMSUNG","NC","KT","LOTTE","KIA","DOOSAN","KIWOOM"}
    for d in (0,1):
        day=(now.astimezone(kst)+timedelta(days=d))
        # Official English KBO schedule supports year/month and exposes date/time/game table.
        url=f"https://eng.koreabaseball.com/Schedule/DailySchedule.aspx?gameDate={day.strftime('%Y%m%d')}"
        try:
            r=S.get(url,timeout=15)
            if r.status_code!=200:
                log.warning("KBO official schedule HTTP %s",r.status_code); continue
            soup=BeautifulSoup(r.text,"html.parser")
            rows=soup.find_all("tr")
            current_date=None
            for tr in rows:
                cells=[" ".join(td.stripped_strings) for td in tr.find_all(["th","td"])]
                if not cells: continue
                joined=" | ".join(cells)
                dm=re.search(r"(\d{2})\.(\d{2})",joined)
                if dm: current_date=(int(dm.group(1)),int(dm.group(2)))
                tm=re.search(r"\b(\d{1,2}):(\d{2})\b",joined)
                if not tm: continue
                tokens=[]
                for c in cells:
                    for t in re.findall(r"\b(?:LG|HANWHA|SSG|SAMSUNG|NC|KT|LOTTE|KIA|DOOSAN|KIWOOM)\b",c):
                        if t in team_codes: tokens.append(t)
                # preserve order and remove duplicates
                teams=[]
                for t in tokens:
                    if t not in teams: teams.append(t)
                if len(teams)<2: continue
                month,daynum=current_date or (day.month,day.day)
                start=datetime(day.year,month,daynum,int(tm.group(1)),int(tm.group(2)),tzinfo=kst).astimezone(timezone.utc)
                if not(now < start <= now+timedelta(hours=LOOKAHEAD_HOURS)): continue
                away,home=teams[0],teams[1]
                gid=hashlib.sha1(f"KBO|{start.isoformat()}|{away}|{home}".encode()).hexdigest()[:18]
                out.append({"event_id":f"kbo:{gid}","provider_id":gid,"provider":"kbo-official",
                    "sport":"baseball","league":"KBO","home":home,"away":away,"start_utc":start.isoformat(),
                    "schedule_source":"KBO official"})
        except Exception:
            log.exception("KBO official schedule failed")
    return out

def npb_games():
    """NPB official homepage/schedule text parser. No paid API."""
    out=[]; now=now_utc(); jst=timezone(timedelta(hours=9))
    # NPB daily schedule pages are stable enough to use as discovery.
    for d in (0,1):
        day=(now.astimezone(jst)+timedelta(days=d))
        ymd=day.strftime("%Y%m%d")
        url=f"https://npb.jp/games/{day.year}/schedule_{day.strftime('%m%d')}_detail.html"
        try:
            r=S.get(url,timeout=15)
            if r.status_code!=200: continue
            soup=BeautifulSoup(r.text,"html.parser")
            for a in soup.find_all("a",href=True):
                href=a["href"]
                if "/scores/" not in href: continue
                text=" ".join(a.parent.get_text(" ",strip=True).split())
                m=re.search(r"(\d{1,2}):(\d{2})",text)
                if not m: continue
                teams=[t for t in ["巨人","DeNA","ヤクルト","阪神","広島","中日","日本ハム","ロッテ","楽天","ソフトバンク","西武","オリックス"] if t in text]
                if len(teams)<2: continue
                start=datetime(day.year,day.month,day.day,int(m.group(1)),int(m.group(2)),tzinfo=jst).astimezone(timezone.utc)
                if not(now < start <= now+timedelta(hours=LOOKAHEAD_HOURS)): continue
                full=href if href.startswith("http") else "https://npb.jp"+href
                gid=hashlib.sha1(full.encode()).hexdigest()[:16]
                out.append({"event_id":f"npb:{gid}","provider_id":full,"provider":"npb",
                    "sport":"baseball","league":"NPB","away":teams[0],"home":teams[1],"start_utc":start.isoformat()})
        except Exception: log.exception("NPB schedule failed")
    return out

# ---------- lineups / starters ----------
def mlb_lineup(g):
    data=get_json(f"https://statsapi.mlb.com/api/v1/game/{g['provider_id']}/boxscore")
    if not data:return {}
    teams=data.get("teams") or {}
    ans={}
    for side in ("home","away"):
        t=teams.get(side) or {}; order=t.get("battingOrder") or []; players=t.get("players") or {}
        names=[]
        for pid in order:
            p=players.get(f"ID{pid}") or {}
            n=((p.get("person") or {}).get("fullName") or "")
            if n:names.append(n)
        ans[side+"_lineup"]=names[:9]
    ans["lineup_source"]="MLB Stats API"
    return ans

def espn_summary_context(g):
    if g["provider"]!="espn": return {}
    _,sport,slug,_=g["event_id"].split(":",3)
    data=get_json(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/summary",{"event":g["provider_id"]})
    if not data:return {}
    out={"lineup_source":"ESPN"}
    # Injuries
    injuries=[]
    for team in data.get("injuries") or []:
        for x in team.get("injuries") or []:
            athlete=x.get("athlete") or {}
            injuries.append({"player":athlete.get("displayName"),"status":x.get("status"),"details":x.get("details")})
    out["injuries"]=injuries[:20]
    # Rosters / starters when supplied
    starters=[]
    for r in data.get("rosters") or []:
        for a in r.get("roster") or []:
            if a.get("starter"):
                athlete=a.get("athlete") or {}
                starters.append(athlete.get("displayName"))
    out["starters"]=list(filter(None,starters))[:30]
    return out

def kbo_lineup(g):
    """KBO lineup: do not call dead/private endpoints.
    v15.4 fails closed until a verified public lineup source is available.
    """
    return {}

def npb_lineup(g):
    url=g["provider_id"]
    try:
        r=S.get(url,timeout=15)
        if r.status_code!=200:return {}
        soup=BeautifulSoup(r.text,"html.parser")
        text="\n".join(x.strip() for x in soup.stripped_strings)
        # Official page exposes 最新のオーダー once lineup exists.
        if "最新のオーダー" not in text:return {}
        # Keep raw official order text for AI rather than risky home/away inversion.
        i=text.find("最新のオーダー")
        return {"official_lineup_text":text[i:i+1800],"lineup_source":"NPB.jp"}
    except Exception:
        log.exception("NPB lineup failed"); return {}

def enrich(g):
    if g.get("league") != "MLB":
        return g
    g["odds"]=mlb_odds(g)
    if g.get("odds"):
        g["odds"]["home_implied_pct"]=american_implied_probability(g["odds"].get("home_moneyline"))
        g["odds"]["away_implied_pct"]=american_implied_probability(g["odds"].get("away_moneyline"))
    if g["league"]=="MLB": g.update(mlb_lineup(g))
    elif g["league"]=="KBO": g.update(kbo_lineup(g))
    elif g["league"]=="NPB": g.update(npb_lineup(g))
    else: g.update(espn_summary_context(g))
    return g

# ---------- news ----------
def news_for_game(g):
    q=f'"{g["home"]}" "{g["away"]}" {g["league"]} injury lineup starter preview'
    if g["league"]=="KBO": q=f'{g["home"]} {g["away"]} KBO 선발 라인업 부상'
    if g["league"]=="NPB": q=f'{g["home"]} {g["away"]} NPB 予告先発 スタメン 怪我'
    url=f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        feed=feedparser.parse(url)
        items=[]
        cutoff=now_utc()-timedelta(hours=NEWS_LOOKBACK_HOURS)
        for e in feed.entries[:12]:
            items.append({"title":e.get("title",""),"source":(e.get("source") or {}).get("title",""),"link":e.get("link","")})
        return items[:8]
    except Exception:return []


# ---------- SQL pick journal / post-game review ----------
def db():
    if not DATABASE_URL:
        return None
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def init_review_db():
    if not DATABASE_URL:
        log.warning("DATABASE_URL missing | review learning disabled")
        return
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sportnow_picks(
            game_key TEXT PRIMARY KEY,
            league TEXT NOT NULL,
            sport TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_id TEXT,
            start_utc TIMESTAMPTZ NOT NULL,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            pick_side TEXT NOT NULL,
            score INTEGER,
            reasons JSONB,
            news JSONB,
            game_json JSONB NOT NULL,
            posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            settled_at TIMESTAMPTZ,
            result TEXT,
            home_score INTEGER,
            away_score INTEGER
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sportnow_reviews(
            game_key TEXT PRIMARY KEY REFERENCES sportnow_picks(game_key) ON DELETE CASCADE,
            verdict TEXT NOT NULL,
            review_text TEXT NOT NULL,
            mistakes JSONB,
            lessons JSONB,
            reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sn_picks_recent ON sportnow_picks(league, posted_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sn_reviews_recent ON sportnow_reviews(reviewed_at DESC)")

def journal_pick(x):
    if not DATABASE_URL: return
    g=x["game"]; key=game_key(g)
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
        INSERT INTO sportnow_picks(
          game_key,league,sport,provider,provider_id,start_utc,home,away,
          pick_side,score,reasons,news,game_json,posted_at
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,NOW())
        ON CONFLICT(game_key) DO NOTHING
        """,(key,g["league"],g["sport"],g["provider"],str(g.get("provider_id","")),
             parse_dt(g["start_utc"]),g["home"],g["away"],x["pick_side"],int(x.get("score",50)),
             json.dumps(x.get("reasons") or [],ensure_ascii=False),
             json.dumps(x.get("news") or [],ensure_ascii=False),
             json.dumps(g,ensure_ascii=False)))

def recent_lessons(g):
    if not DATABASE_URL: return []
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
        SELECT p.league,p.result,p.score,r.mistakes,r.lessons
        FROM sportnow_reviews r JOIN sportnow_picks p USING(game_key)
        WHERE p.league='MLB'
        ORDER BY r.reviewed_at DESC LIMIT %s
        """,(REVIEW_LOOKBACK,))
        return [dict(x) for x in cur.fetchall()]

def review_one(row):
    g=row["game_json"]
    hs,as_=row["home_score"],row["away_score"]
    picked=g["home"] if row["pick_side"]=="home" else g["away"]
    winner=g["home"] if hs>as_ else g["away"]
    verdict=row["result"]
    prompt=f"""
너는 SPORT NOW 사후 복기 담당이다. 결과를 보고 과거 예측을 정당화하지 말고,
다음 경기에서 재사용 가능한 판단 규칙만 추출한다.
단순히 특정 팀을 다음에 낮게/높게 평가하라는 교훈은 금지한다.
예측 당시 제공됐던 근거와 데이터만 평가한다.

판정: {verdict}
최종스코어: {g['away']} {as_} - {hs} {g['home']}
픽: {picked}
예측 신뢰점수: {row['score']}
당시 근거: {json.dumps(row['reasons'] or [],ensure_ascii=False)}
당시 뉴스: {json.dumps(row['news'] or [],ensure_ascii=False)}

JSON만:
{{"review_text":"2~3문장 복기","mistakes":["최대 3개"],"lessons":["다음 분석에 적용할 일반 규칙 최대 3개"]}}
""".strip()
    try:
        r=client.responses.create(model=AI_MODEL,input=prompt)
        txt=r.output_text.strip()
        m=re.search(r"\{.*\}",txt,re.S)
        x=json.loads(m.group(0) if m else txt)
        return x
    except Exception:
        log.exception("post-game review failed | %s",row["game_key"])
        return {"review_text":"자동 복기 생성 실패","mistakes":[],"lessons":[]}

def settle_and_review():
    if not DATABASE_URL: return
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
        SELECT * FROM sportnow_picks
        WHERE settled_at IS NULL AND league='MLB'
          AND start_utc < NOW() - INTERVAL '2 hours'
        ORDER BY start_utc ASC LIMIT 30
        """)
        rows=[dict(x) for x in cur.fetchall()]
    for row in rows:
        g=row["game_json"]
        score=result_for(g)
        if score is None: continue
        hs,as_=score
        if hs==as_: verdict="PUSH"
        else:
            actual="home" if hs>as_ else "away"
            verdict="HIT" if actual==row["pick_side"] else "MISS"
        with db() as conn, conn.cursor() as cur:
            cur.execute("""
            UPDATE sportnow_picks SET settled_at=NOW(),result=%s,home_score=%s,away_score=%s
            WHERE game_key=%s AND settled_at IS NULL
            """,(verdict,hs,as_,row["game_key"]))
        row.update({"result":verdict,"home_score":hs,"away_score":as_})
        review=review_one(row)
        with db() as conn, conn.cursor() as cur:
            cur.execute("""
            INSERT INTO sportnow_reviews(game_key,verdict,review_text,mistakes,lessons)
            VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)
            ON CONFLICT(game_key) DO NOTHING
            """,(row["game_key"],verdict,str(review.get("review_text",""))[:2000],
                 json.dumps(review.get("mistakes") or [],ensure_ascii=False),
                 json.dumps(review.get("lessons") or [],ensure_ascii=False)))
        log.info("REVIEW SAVED | %s | %s",row["game_key"],verdict)


# ---------- analysis ----------
def analyze(g, already_enriched=False):
    if not already_enriched:
        g=enrich(g)
    news=news_for_game(g)
    lessons=recent_lessons(g)
    context={"game":g,"news":news,"recent_postgame_lessons":lessons}
    prompt=f"""
너는 SPORT NOW MLB 프리뷰 분석가다.
아래 제공 데이터만 사용하고, 기억으로 선수 상태나 기록을 만들어내지 않는다.

분석 우선순위:
1. 확정 선발 라인업과 핵심 타자 출전 여부
2. 선발투수 및 최근 관련 뉴스
3. 부상·말소·복귀·휴식·불펜 소모 관련 정보
4. 최근 팀 흐름과 홈/원정 요소
5. 배당 시장 정보(머니라인/스프레드/기준점)는 보조 신호로 분석

배당은 단독 근거로 픽을 정하지 않는다.
머니라인이 있으면 implied probability를 참고하되 북메이커 마진이 포함된 값임을 전제로 한다.
라인업·선발·부상 등 경기 데이터와 배당이 엇갈리면 그 차이를 이유에 짧게 설명한다.

라인업이 아직 없으면 '라인업 미확인'을 명시하고 절대 추측하지 않는다.
근거가 약하면 pick을 만들지 말고 publish=false.
근거가 충분하면 더 우세한 팀 하나만 선택한다.
score는 50~90의 '분석 신뢰 점수'이며 실제 승률이라고 표현하지 않는다.
reasons는 가장 중요한 사실 2~3개, 각각 한 문장 이내.
reasons 중 배당 정보가 존재하면 최소 1개는 배당과 실제 경기 데이터의 관계를 설명한다.
comment는 딱 한 문장, 45자 안팎. 장문 금지.
모든 reasons와 comment는 반드시 자연스러운 한국어로 작성한다.
영문 팀명은 한국어 팀명으로, 선수 이름은 가능한 자연스러운 한글 음역으로 표기한다.
영문 뉴스 제목을 그대로 출력하지 말고 핵심 내용만 한국어로 요약한다.
뉴스 제목만으로 과도한 결론을 내리지 않는다.
recent_postgame_lessons가 있으면 같은 유형의 판단 실수를 반복하지 않는 참고자료로만 사용한다.
복기 때문에 특정 팀을 자동 감점/가점하지 않는다. 현재 경기의 실제 데이터가 항상 우선이다.

JSON만:
{{"publish":true,"pick_side":"home","score":66,"reasons":["...","..."],"comment":"..."}}

DATA:
{json.dumps(context,ensure_ascii=False)}
""".strip()
    try:
        r=client.responses.create(model=AI_MODEL,input=prompt)
        txt=r.output_text.strip()
        m=re.search(r"\{.*\}",txt,re.S)
        x=json.loads(m.group(0) if m else txt)
        if not x.get("publish"): return None
        if x.get("pick_side") not in ("home","away"):return None
        x["game"]=g; x["news"]=news
        return x
    except Exception:
        log.exception("AI analysis failed | %s",game_key(g)); return None

# ---------- Telegram ----------
_last_post=0.0
def send(text):
    global _last_post
    wait=POST_INTERVAL-(time.time()-_last_post)
    if wait>0: time.sleep(wait)
    payload={"chat_id":CHANNEL_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
    if PROMO_URL:
        payload["reply_markup"]=json.dumps({"inline_keyboard":[[{"text":PROMO_BUTTON_TEXT,"url":PROMO_URL}]]})
    r=S.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",data=payload,timeout=20)
    r.raise_for_status(); _last_post=time.time()

def format_pick(x):
    g=x["game"]; side=x["pick_side"]; pick=ko_team(g[side])
    icon="⚾" if g["sport"]=="baseball" else ("⚽" if g["sport"]=="football" else "🏀")
    reasons=(x.get("reasons") or [])[:3]
    body="\n".join(f"• {re.sub(r'^[-•\\s]+','',str(r))}" for r in reasons)
    lineup=g.get("lineup_source")
    lineup_line=f"\n라인업: 확인" if lineup else "\n라인업: 미확인"

    odds=g.get("odds") or {}
    odds_line=""
    hm=odds.get("home_moneyline")
    am=odds.get("away_moneyline")
    if hm is not None or am is not None:
        odds_line=(
            f"\n배당: {ko_team(g['away'])} {am if am is not None else '-'}"
            f" / {ko_team(g['home'])} {hm if hm is not None else '-'}"
        )
        if odds.get("provider"):
            odds_line += f" ({odds['provider']})"

    return (f"{icon} <b>{g['league']}</b>\n"
            f"{ko_team(g['away'])} vs {ko_team(g['home'])}\n\n"
            f"<b>픽: {pick}</b>\n"
            f"PRIME SCORE {int(x.get('score',50))}\n"
            f"{lineup_line}{odds_line}\n\n{body}\n\n"
            f"💬 {x.get('comment','')}")

# ---------- strict result settlement ----------
def result_for(g):
    if g["provider"]=="mlb":
        data=get_json("https://statsapi.mlb.com/api/v1/schedule",{"gamePk":g["provider_id"]})
        try:
            x=data["dates"][0]["games"][0]
            if x["status"]["abstractGameState"]!="Final":return None
            return int(x["teams"]["home"]["score"]),int(x["teams"]["away"]["score"])
        except:return None
    if g["provider"]=="espn":
        _,sport,slug,_=g["event_id"].split(":",3)
        data=get_json(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/summary",{"event":g["provider_id"]})
        try:
            comp=data["header"]["competitions"][0]
            st=(comp["status"]["type"])
            if not st.get("completed"):return None
            h=a=None
            for c in comp["competitors"]:
                if c["homeAway"]=="home":h=int(float(c["score"]))
                if c["homeAway"]=="away":a=int(float(c["score"]))
            return h,a
        except:return None
    # KBO/NPB results deliberately require their original provider id;
    # never match yesterday by team names.
    return None

# ---------- state ----------
STATE_FILE=os.getenv("STATE_FILE","sportnow_state.json")
def load_state():
    try:return json.loads(open(STATE_FILE,encoding="utf-8").read())
    except:return {"posted":{},"results":{}}
def save_state(st):
    tmp=STATE_FILE+".tmp"
    open(tmp,"w",encoding="utf-8").write(json.dumps(st,ensure_ascii=False,indent=2))
    os.replace(tmp,STATE_FILE)

def collect_games():
    """MLB only. All other sports/leagues are disabled."""
    games=[]
    try:
        games.extend(mlb_games())
    except Exception:
        log.exception("MLB schedule collection failed")
    return [g for g in games if g.get("league")=="MLB" and g.get("sport")=="baseball"]

def cycle():
    settle_and_review()
    st=load_state(); games=collect_games()
    log.info("games=%d | %s",len(games),{l:sum(g["league"]==l for g in games) for l in sorted({g["league"] for g in games})})
    for g in games:
        if g.get('league') != 'MLB':
            continue
        key=game_key(g)
        if key in st["posted"]: continue
        mins=(parse_dt(g["start_utc"])-now_utc()).total_seconds()/60
        # Analyze only from T-120 to T-30.
        # T-30 or closer: never create a new pick, even if lineup appears late.
        if mins > 120:
            continue
        if mins <= NEW_PICK_CUTOFF_MINUTES:
            log.info("SKIP LATE GAME | %s | %.1fmin | cutoff=T-%d",
                     key, mins, NEW_PICK_CUTOFF_MINUTES)
            continue

        eg=enrich(dict(g))
        has_lineup=bool(
            eg.get("home_lineup")
            or eg.get("away_lineup")
            or eg.get("official_lineup_text")
            or eg.get("starters")
        )

        # Baseball requires lineup before publishing.
        # Keep polling between T-120 and T-30; if it never arrives, game expires.
        if g["sport"]=="baseball" and not has_lineup:
            log.info("WAIT LINEUP | %s | %.1fmin | expires=T-%d",
                     key, mins, NEW_PICK_CUTOFF_MINUTES)
            continue

        x=analyze(eg, already_enriched=True)
        if not x:continue
        send(format_pick(x))
        journal_pick(x)
        st["posted"][key]={"game":g,"pick_side":x["pick_side"],"posted_at":now_utc().isoformat()}
        save_state(st)
        log.info("PICK POSTED | %s",key)

def main():
    init_review_db()
    log.info("SPORT NOW v16.1 MLB ONLY started | odds=ON | korean-output=ON | review-learning=ON")
    while True:
        try:cycle()
        except Exception:log.exception("cycle failed")
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__": main()
