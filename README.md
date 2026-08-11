# SPORT NOW v12 — Sportradar KBO Lineups

KBO 라인업 우선순위:
1. Sportradar Global Baseball v2
   - Daily Summaries에서 sport_event_id 탐색
   - Sport Event Lineups 호출
   - starter=true 선수만 실제 선발로 인정
2. 네이버 모바일 fallback
3. 네이버 gateway fallback

라인업이 아직 발표되지 않았거나 API 응답에 starter=true가 충분히 없으면
LINEUP CONFIRMED로 처리하지 않습니다.

Railway Variables:
SPORTRADAR_API_KEY=발급받은키
SPORTRADAR_ACCESS_LEVEL=trial
SPORTRADAR_LANGUAGE=en
ENABLE_SPORTRADAR_KBO=true

기존:
- KBO/NPB/MLB 분석
- PRIME SCORE
- FINAL COMBO
- 한글 팀명
- 오마카세 버튼
- PostgreSQL 결과 기록
모두 유지됩니다.
