# SPORT NOW v14.7.1 HOTFIX

- v14.7 resolver의 `_norm_team` NameError 수정 (`norm_team` 사용)
- Sportradar 공식 lineups 파서/429 backoff/30분 deadline 로직은 그대로 유지

# SPORT NOW v14.7 — Sportradar Official Lineups

공식 Sportradar Global Baseball v2 문서 기준으로 재정비.

핵심:
- Sport Event Lineups 공식 endpoint 사용:
  /baseball/{access_level}/v2/{language}/sport_events/{sport_event_id}/lineups.json
- 공식 JSON 구조인 lineups[] -> qualifier -> players[] 파싱
- 선발은 반드시 starter=true 기준
- order는 타순 정렬에만 사용
- sr:sport_event:* 형식이 아닌 ID는 Sportradar lineups에 절대 전송하지 않음
- fallback 일정 경기(ESPN 등)는 DB에 캐시된 Global Baseball 일정과
  리그/팀/시작시간을 매칭해 진짜 Sportradar event id를 복구한 뒤 lineups 호출
- 429 backoff가 켜지면 같은 refresh에서 추가 날짜 요청 즉시 중단
- QPS 1 trial에 맞춰 기본 요청 간격 1.5초
- 미확정 라인업 재확인 기본 5분

기존 유지:
- 경기 30분 전까지 라인업 대기
- 30분 전까지 못 잡으면 라인업 없이 분석
- 픽 개수 제한 없음
- 점수 컷 없음
- FINAL COMBO 없음
- 텔레그램 120초 간격
