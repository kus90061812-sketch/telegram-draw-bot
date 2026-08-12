# SportNow v14.8 API Stability

- Sportradar 429 cooldown을 PostgreSQL app_state에 저장: Railway 재시작/재배포 후에도 유지
- 429 때 Retry-After 및 rate-limit 관련 응답 헤더와 짧은 body 로그
- Trial QPS 1 보호: 요청 기본 간격 1.5초
- 일정/라인업 요청 모두 동일한 persistent cooldown 사용
- 기존 sport_schedule_cache의 sr:sport_event ID 재사용
- 공식 Global Baseball lineups 파서(starter=true) 유지
- 경기 30분 전까지 라인업 대기, 이후 라인업 없이 분석 유지

권장 Railway 변수:
SR_REQUEST_SPACING_SECONDS=1.5
SR_429_BACKOFF_SECONDS=900
SR_429_MAX_BACKOFF_SECONDS=3600
SR_LINEUP_RECHECK_SECONDS=300
LINEUP_WAIT_UNTIL_MINUTES=30
