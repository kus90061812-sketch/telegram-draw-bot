# SPORT NOW v13.3.11 — MLB Sportradar Schedule Fix

원인:
- MLB 일정은 ESPN event_id를 사용하고 있었음
- 그 event_id를 Sportradar lineups endpoint에 그대로 넣어 라인업 조회가 실패할 수 있었음

수정:
- MLB 일정도 Sportradar Daily Summaries 우선
- 처음부터 `sportradar_event_id` 보존
- Sportradar MLB 일정이 있으면 ESPN MLB 중복 수집 안 함
- 혹시 ESPN MLB 후보가 들어와도 lineups 호출 전 Sportradar event_id 재매칭
- 이미 시작한 경기는 제외
- 분 단위 prematch 시간창은 없음

유지:
- 픽 개수 제한 없음
- 점수 컷 없음
- FINAL COMBO 없음
- KBO/NPB Sportradar 일정
- KBO/NPB/MLB Sportradar 라인업
