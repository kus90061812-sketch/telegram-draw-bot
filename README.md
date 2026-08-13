# SPORT NOW v14.9 — Result Settlement Fix

적중/미적중 후속 결과 처리 수정.

핵심:
- MLB 결과: Sportradar 캐시 우선
- Sportradar 캐시가 갱신되지 않거나 429 상태면 ESPN scoreboard에서
  `리그 + 홈팀 + 원정팀 + 경기시간`으로 최종 경기 재검색
- `sr:sport_event:*` ID를 ESPN event ID로 잘못 사용하는 문제 회피
- ESPN 지원 축구/농구/기타 리그도 direct event 결과 실패 시 matchup fallback
- 결과를 못 찾으면 RESULT PENDING 로그 출력
- 최종 결과 확보 시 자동으로 hit / miss 판정
- 결과글에 `📌 결과: ✅ 적중` 또는 `📌 결과: ❌ 미적중`
- Telegram 전송 실패한 결과는 result_posted=0으로 남겨 다음 사이클 재전송
- 기존 20개 pending 제한을 100개로 확대

기존 유지:
- Sportradar 429 persistent backoff
- 픽 개수 제한 없음
- 점수 컷 없음
- FINAL COMBO 없음
- 경기 30분 전 라인업 deadline
- 텔레그램 게시 간격 120초
