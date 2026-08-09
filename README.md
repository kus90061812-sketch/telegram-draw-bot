# SportNow v7 — 무료 경기데이터 + 뉴스 종합분석

v6.2의 뉴스/SQL/프리매치/결과추적 기능을 유지하면서
프리매치 분석에 무료로 가져올 수 있는 경기/팀 데이터를 추가했습니다.

## 분석 대상
- EPL
- La Liga
- Bundesliga
- Serie A
- Ligue 1
- UEFA Champions League
- MLB
- NBA
- NFL
- NHL

## v7 분석 흐름
1. 메이저리그 예정 경기 확인
2. 경기 시작 90~240분 전 경기만 후보
3. 양 팀 최근 경기 최대 5경기 결과/득실 수집
4. event summary에서 가능한 부상/선수/경기 문맥 수집
5. 최근 48시간 스포츠 뉴스 결합
6. 최근 폼 기반 간단한 base score 계산
7. AI가 뉴스/부상/최근 폼을 함께 평가
8. 하루 최대 4개만 게시
9. 경기 종료 후 자동 적중/미적중 판정
10. PostgreSQL에 누적 기록 저장

## Railway Variables 추가
ENABLE_FREE_TEAM_DATA=true
RECENT_GAMES_LOOKBACK=5

기존 변수는 그대로 유지:
DATABASE_URL=${{Postgres.DATABASE_URL}}
ENABLE_NEWS_PICKS=true
PREMATCH_MIN_MINUTES=90
PREMATCH_MAX_MINUTES=240
MAX_PICKS_PER_DAY=4
MIN_NEWS_EDGE=58
ENABLE_RESULT_POSTS=true

## 주의
표시되는 %는 무료 공개 경기 데이터 + 뉴스 기반 AI 추정 우세도입니다.
정식 배당/유료 스포츠 데이터 모델의 실제 승률과는 다릅니다.
