# SportNow v6.2 CLEAN

이전 v6.x의 함수 누락 문제를 피하기 위해 main.py를 통째로 다시 정리한 버전입니다.

포함 기능:
- 국내/해외 스포츠 뉴스 RSS 수집
- 해외뉴스 한국어 자동번역 + 요약
- 국내뉴스 요약
- 유사 기사 중복 차단
- 하루 뉴스 최대 40건
- PostgreSQL 저장
- 메이저 리그만 프리매치 분석
  - EPL / La Liga / Bundesliga / Serie A / Ligue 1 / UCL
  - MLB / NBA / NFL / NHL
- 경기 시작 90~240분 전만 후보
- 뉴스 근거가 충분한 경기 중 하루 최대 4픽
- 경기 종료 후 자동 결과 조회
- 적중/미적중 자동 게시
- 최근 24시간 + 누적 적중률

Railway Variables:
DATABASE_URL=${{Postgres.DATABASE_URL}}
ENABLE_NEWS_PICKS=true
PREMATCH_MIN_MINUTES=90
PREMATCH_MAX_MINUTES=240
MAX_PICKS_PER_DAY=4
MIN_NEWS_EDGE=58
ENABLE_RESULT_POSTS=true

중요:
표시되는 %는 뉴스 기반 AI 추정 우세도이며, 배당/정식 통계모델의 실제 승률은 아닙니다.
