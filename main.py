import os, re, json, time, hashlib, logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

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
    "Los Angeles Dodgers":"LA 다저스","New York Yankees":"뉴욕 양키스",
    "Boston Red Sox":"보스턴 레드삭스","San Diego Padres":"샌디에이고 파드리스",
    "LG":"LG 트윈스","HANWHA":"한화 이글스","SSG":"SSG 랜더스",
    "SAMSUNG":"삼성 라이온즈","NC":"NC 다이노스","KT":"KT 위즈",
    "LOTTE":"롯데 자이언츠","KIA":"KIA 타이거즈","DOOSAN":"두산 베어스",
    "KIWOOM":"키움 히어로즈","阪神":"한신 타이거스","巨人":"요미우리 자이언츠",
    "DeNA":"요코하마 DeNA","ヤクルト":"야쿠르트","広島":"히로시마",
    "中日":"주니치","日本ハム":"닛폰햄","ロッテ":"지바 롯데",
    "楽天":"라쿠텐","ソフトバンク":"소프트뱅크","西武":"세이부","オリックス":"오릭스",
}
def ko_team(x): return TEAM_KO.get(str(x or "").strip(), str(x or "").strip())
def now_utc(): return datetime.now(timezone.utc)
def parse_dt(x): return datetime.fromisoformat(str(x).replace("Z","+00:00"))
def norm(x): return re.sub(r"[^a-z0-9가-힣ぁ-んァ-ン一-龥]","",str(x or "").lower())
def game_key(g):
    return "|".join([g["league"], norm(g["home"]), norm(g["away"]), g["start_utc"][:16]])

def get_json(url, params=None, timeout=15):
    try:
        r=S.get(url,params=params,timeout=timeout)
        if r.status_code==200: return r.json()
        log.warning("HTTP %s | %s",r.status_code,url)
    except Exception: log.exception("GET failed | %s",url)
    return None

# ---------- schedules ----------
def espn_games():
    out=[]
    now=now_utc()
    dates={(now+timedelta(days=d)).strftime("%Y%m%d") for d in (0,1)}
    for sport,slug,label,kind in ESPN_LEAGUES:
        for ds in dates:
            data=get_json(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard",{"dates":ds})
            if not data: continue
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

def kbo_games():
    """Naver Sports schedule gateway. KBO official pages are used later for context/news."""
    out=[]; now=now_utc(); kst=timezone(timedelta(hours=9))
    for d in (0,1):
        ds=(now.astimezone(kst)+timedelta(days=d)).strftime("%Y-%m-%d")
        data=get_json("https://api-gw.sports.naver.com/schedule/games",
            {"upperCategoryId":"kbaseball","fromDate":ds,"toDate":ds})
        if not data: continue
        def walk(o):
            if isinstance(o,dict):
                if (o.get("gameId") or o.get("id")) and any(k in o for k in ("homeTeamName","homeTeam","home")):
                    yield o
                for v in o.values(): yield from walk(v)
            elif isinstance(o,list):
                for v in o: yield from walk(v)
        for x in walk(data):
            blob=json.dumps(x,ensure_ascii=False)
            home=x.get("homeTeamName") or ((x.get("homeTeam") or {}).get("name") if isinstance(x.get("homeTeam"),dict) else "")
            away=x.get("awayTeamName") or ((x.get("awayTeam") or {}).get("name") if isinstance(x.get("awayTeam"),dict) else "")
            raw=x.get("gameDateTime") or x.get("startDateTime") or x.get("gameDate")
            gid=str(x.get("gameId") or x.get("id") or "")
            if not(home and away and raw and gid): continue
            try:
                start=parse_dt(raw) if ("T" in str(raw) and ("Z" in str(raw) or "+" in str(raw))) else datetime.fromisoformat(str(raw)).replace(tzinfo=kst).astimezone(timezone.utc)
            except: continue
            if now < start <= now+timedelta(hours=LOOKAHEAD_HOURS):
                out.append({"event_id":f"kbo:{gid}","provider_id":gid,"provider":"naver-kbo",
                    "sport":"baseball","league":"KBO","home":home,"away":away,"start_utc":start.isoformat()})
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
    # Naver game detail is primary fast lineup source.
    gid=g["provider_id"]
    candidates=[
        f"https://api-gw.sports.naver.com/game/lineup?gameId={gid}",
        f"https://api-gw.sports.naver.com/game/{gid}/lineup",
    ]
    for url in candidates:
        data=get_json(url)
        if not data: continue
        found=[]
        def walk(o):
            if isinstance(o,dict):
                for k,v in o.items():
                    if k.lower() in ("playername","name") and isinstance(v,str): found.append(v)
                    walk(v)
            elif isinstance(o,list):
                for v in o:walk(v)
        walk(data)
        names=[]
        for x in found:
            if x not in names:names.append(x)
        if len(names)>=14:
            return {"home_lineup":names[:9],"away_lineup":names[9:18],"lineup_source":"Naver Sports"}
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

# ---------- analysis ----------
def analyze(g, already_enriched=False):
    if not already_enriched:
        g=enrich(g)
    news=news_for_game(g)
    context={"game":g,"news":news}
    prompt=f"""
너는 SPORT NOW 경기 프리뷰 분석가다. 배당/베팅시장 정보는 절대 사용하지 않는다.
아래 제공 데이터만 사용한다. 기억으로 선수 상태나 기록을 만들어내지 않는다.

분석 우선순위:
야구: 확정 라인업 > 선발투수 > 부상/말소/복귀 > 최근 뉴스/팀 흐름 > 홈/원정.
축구: 확정 선발 XI > 부상/징계 > 로테이션/감독 발언 > 최근 일정/폼 > 홈/원정.
농구: 선발/출전상태 > 부상(OUT/Q/DTD) > 백투백/휴식 > 로테이션 뉴스 > 최근 흐름.

라인업이 아직 없으면 '라인업 미확인'을 명시하고 절대 추측하지 않는다.
근거가 약하면 pick을 만들지 말고 publish=false.
근거가 충분하면 더 우세한 팀 하나만 선택한다.
score는 50~90의 '분석 신뢰 점수'이며 실제 승률이라고 표현하지 않는다.
reasons는 가장 중요한 사실 2~3개, 각각 한 문장 이내.
comment는 딱 한 문장, 45자 안팎. 장문 금지.
뉴스 제목만으로 과도한 결론을 내리지 않는다.

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
    lineup_line=f"\n라인업: {lineup} 확인" if lineup else "\n라인업: 미확인"
    return (f"{icon} <b>{g['league']}</b>\n"
            f"{ko_team(g['away'])} vs {ko_team(g['home'])}\n\n"
            f"<b>픽: {pick}</b>\n"
            f"PRIME SCORE {int(x.get('score',50))}\n"
            f"{lineup_line}\n\n{body}\n\n"
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
    games=[]
    for fn in (mlb_games,kbo_games,npb_games,espn_games):
        try:games.extend(fn())
        except Exception:log.exception("schedule adapter failed | %s",fn.__name__)
    uniq={}
    for g in games: uniq[game_key(g)]=g
    return sorted(uniq.values(),key=lambda x:x["start_utc"])

def cycle():
    st=load_state(); games=collect_games()
    log.info("games=%d | %s",len(games),{l:sum(g["league"]==l for g in games) for l in sorted({g["league"] for g in games})})
    for g in games:
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
        st["posted"][key]={"game":g,"pick_side":x["pick_side"],"posted_at":now_utc().isoformat()}
        save_state(st)
        log.info("PICK POSTED | %s",key)

def main():
    log.info("SPORT NOW v15.1 started | odds=OFF | sportradar=REMOVED")
    while True:
        try:cycle()
        except Exception:log.exception("cycle failed")
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__": main()
