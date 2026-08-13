# SPORT NOW v14.11 — Result Idempotency

이미 올린 적중/미적중 결과가 다시 올라오는 문제를 추가로 차단합니다.

중복 방지:
- event_id가 달라도 같은 리그 + 같은 홈/원정 + 같은 경기시간이면 같은 결과로 취급
- 경기시간은 15분 단위 semantic key로 정규화
- Telegram 전송 전에 result_delivery_log에 `sending` 예약을 DB commit
- Railway 재시작/재배포가 전송 직후 발생해도 동일 결과 재전송 방지
- 성공 시 `sent`로 변경
- 실제 Telegram send가 예외를 낸 경우에만 예약을 풀어 재시도 허용
- 과거 result_posted=1 데이터도 시작 시 dedupe table로 backfill
- 오래된 결과 8시간 제한과 ESPN 결과 ±3시간 매칭은 그대로 유지
