# SportNow v8.2 — 국내/일본 일정 400 오류 수정

핵심 수정:
- 존재하지 않는 ESPN KBO/NPB/KBL/K League 코드를 더 이상 호출하지 않음
- 따라서 HTTP 400 반복 로그 제거
- KBO: KBO 공식 영문 Daily Schedule 우선 사용
- NPB: NPB.jp 공식 월간 상세 일정 사용
- K League 1: K리그 공식 일정 페이지 우선 파싱
- MLB / NBA / 유럽 인기 축구리그: 기존 ESPN 공개 scoreboard 유지
- KBL: 8월 현재 비시즌이라 잘못된 ESPN 요청을 하지 않음

픽:
- 경기 45~70분 전
- 최소 우세도 55%
- KBO+NPB는 asia_baseball 그룹으로 함께 비교
- MLB 별도
- 축구 인기리그 그룹
- NBA 농구 그룹
- 기준 미달이면 픽 없음

주의:
- KBO/NPB/K리그 공식 사이트 HTML 구조가 바뀌면 해당 파서도 조정이 필요할 수 있음.
- KBO/NPB/K리그 결과 자동판정은 현재 해외 ESPN 리그와 달리 별도 결과 파서가 아직 없어 pending으로 남길 수 있음.
