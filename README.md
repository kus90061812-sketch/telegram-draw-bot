# SportNow v8 — Quality First Picks

핵심 변경:
- 픽 개수를 억지로 채우지 않음
- 경기 약 1시간 전(45~70분 전)에만 최종 분석
- 우세도 55% 미만 자동 제외
- 그룹별 가장 강한 픽만 게시
- 각 그룹 최대 2개, 기준 미달이면 0개 가능

분석 그룹:
1. KBO + NPB (같은 그룹에서 서로 비교)
2. MLB
3. 축구 인기리그
   - K League 1
   - EPL
   - La Liga
   - Bundesliga
   - Serie A
   - Ligue 1
   - UEFA Champions League
4. 농구 인기리그
   - KBL
   - NBA

분석 재료:
- 최근 최대 5경기 성적/득실
- 공개 event context에서 가능한 부상/선수 정보
- 최근 48시간 스포츠 뉴스
- 홈/원정
- 최근 폼 기반 base score
- AI 종합 판단

결과:
- 경기 종료 후 적중/미적중 자동 게시
- PostgreSQL 누적 적중률 유지

Railway 권장 Variables:
PREMATCH_MIN_MINUTES=45
PREMATCH_MAX_MINUTES=70
MIN_NEWS_EDGE=55
MAX_PICKS_PER_GROUP=2
MAX_PICKS_PER_DAY=8
ENABLE_FREE_TEAM_DATA=true
RECENT_GAMES_LOOKBACK=5
ENABLE_NEWS_PICKS=true
ENABLE_RESULT_POSTS=true

주의:
KBO/NPB/KBL/K League 1은 현재 무료 공개 일정 엔드포인트를 우선 시도합니다.
해당 무료 소스가 응답하지 않는 날에는 그 리그를 자동으로 건너뛰며,
다른 메이저 리그 봇 동작에는 영향을 주지 않도록 구성했습니다.

표시되는 %는 무료 공개 경기 데이터 + 뉴스 기반 AI 추정 우세도이며
정식 배당 모델의 실제 승률은 아닙니다.
