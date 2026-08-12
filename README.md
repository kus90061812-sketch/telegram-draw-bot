# SPORT NOW v14.6 — Lineup Deadline

야구(KBO/NPB/MLB):
- 경기 일정은 미리 캐시
- 라인업 확인을 계속 시도
- 라인업이 확보되면 즉시 분석 가능
- 라인업이 없고 경기 시작까지 30분 초과 남았으면 기다림
- 경기 시작 30분 이내가 됐는데도 라인업을 못 가져오면 라인업 없이 분석
- REQUIRE_CONFIRMED_LINEUP 없음

Railway:
LINEUP_WAIT_UNTIL_MINUTES=30

기존 유지:
- Sportradar 429 fallback
- 픽 개수 제한 없음
- 점수 하한 컷 없음
- FINAL COMBO 없음
- 텔레그램 게시 간격 120초
