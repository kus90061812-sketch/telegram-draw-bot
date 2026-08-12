# SPORT NOW v14.3 — Minimal Sportradar Calls

변경:
- 야구 일정 조회를 과거/미래 4일 조회에서 최소 1~2일 조회로 변경
- KST 오늘 날짜 조회
- UTC 날짜가 KST와 다를 때만 UTC 오늘 날짜 추가 조회 (MLB 날짜 경계 보완)
- 내일 일정 선조회 제거
- Railway 로그에 실제 조회 날짜 표시

유지:
- KBO / NPB / MLB 공통 일정 캐시
- 30분 일정 캐시
- 라인업 캐시 / 재확인
- Sportradar 요청 간격 / 429 백오프
- 픽 개수 제한 없음
- 텔레그램 게시 간격 120초
- FINAL COMBO 없음
