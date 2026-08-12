# SPORT NOW v14.4 — Schedule Fallback

핵심:
- Sportradar 일정 API가 429/장애여도 야구 후보 전체가 0개로 끝나지 않음
- KBO 캐시 없음 -> 기존 KBO 공식 일정 fallback
- NPB 캐시 없음 -> 기존 NPB 공식 일정 fallback
- MLB 캐시 없음 -> ESPN 일정 fallback
- fallback event_id를 Sportradar lineups endpoint에 잘못 넣지 않음
- Sportradar가 다시 정상화되면 캐시 데이터 우선 사용

유지:
- 일정 API 호출 최소화
- 429 backoff
- lineup cache
- Telegram 2분 간격
- 픽 개수 제한 없음
- 점수 컷 없음
- FINAL COMBO 없음

주의:
REQUIRE_CONFIRMED_LINEUP=true이면 fallback 일정에서 실제 라인업을 확보하지 못한 야구 경기는
여전히 픽에서 제외됩니다. 이는 가짜 라인업으로 게시하는 것을 막기 위한 정상 동작입니다.
