# SPORT NOW v13 — Sportradar All Baseball

KBO / NPB / MLB 라인업 소스를 Sportradar 1순위로 통일.

공통 흐름:
1. Daily Summaries에서 팀 alias + home/away + 경기 시작시각으로 sport_event_id 매칭
2. Sport Event Lineups 호출
3. starter=true 선수만 실제 선발로 인정
4. 양 팀 최소 LINEUP_MIN_PLAYERS 이상이면 LINEUP CONFIRMED

Fallback:
- KBO: 네이버 모바일 → 네이버 gateway
- NPB: SportsNavi
- MLB: 기존 ESPN 데이터

기존 기능 유지:
- PRIME SCORE
- FINAL COMBO
- 한글 팀명
- 오마카세 버튼
- PostgreSQL 결과 기록

Railway 변수는 기존 v12/v12.1과 동일:
SPORTRADAR_API_KEY=...
SPORTRADAR_ACCESS_LEVEL=trial
SPORTRADAR_LANGUAGE=en
ENABLE_SPORTRADAR_KBO=true
