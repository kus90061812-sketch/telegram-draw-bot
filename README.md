# SPORT NOW v13.3.7 — Lineup Argument Fix

수정:
- `fetch_sportradar_baseball_lineup(game)` 함수가
  내부에서 정의되지 않은 `league`, `event_id`를 참조하던 버그 수정
- league는 `game["league"]`
- event_id는 `game["sportradar_event_id"]` 우선, 없으면 `game["event_id"]`
- 기존 lineups 응답 진단 로그 유지
- KBO/NPB Sportradar 일정 수집 유지
- 픽 개수 제한 없음
- FINAL COMBO 없음
