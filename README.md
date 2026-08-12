# SPORT NOW v13.3.10 — No Prematch Time Window

변경:
- PREMATCH_MIN_MINUTES 완전 제거
- PREMATCH_MAX_MINUTES 완전 제거
- 경기 시작 몇 분 전인지로 후보를 제외하지 않음
- 아직 시작하지 않은 경기라면 후보로 유지
- 라인업/경기 데이터가 확보되고 분석 가능하면 게시

기존 유지:
- 픽 개수 제한 없음
- PRIME SCORE/55점 컷 없음
- FINAL COMBO 없음
- KBO/NPB Sportradar 일정
- KBO/NPB/MLB 라인업
- 동일 경기 중복 게시 방지

Railway에서 PREMATCH_MIN_MINUTES / PREMATCH_MAX_MINUTES 변수는 삭제해도 됩니다.
