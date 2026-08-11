# SPORT NOW v11.5 — KBO / NPB Lineup Fallback

MLB는 기존 ESPN 라인업 감지를 유지합니다.

KBO:
- 네이버 스포츠 비공식 게이트웨이에서 당일 경기 ID 확인
- relay JSON 안의 lineup/starter/batting-order 성격 배열을 탐색
- 양 팀 7명 이상 확인될 때만 LINEUP CONFIRMED

NPB:
- SportsNavi 당일 경기 페이지 탐색
- 실제 페이지에 `スタメン` 또는 `オーダー`가 나온 경우만 선수명 추출
- 양 팀 7명 이상 확인될 때만 LINEUP CONFIRMED

가짜 확정 판정은 하지 않습니다.
기존 FINAL COMBO / 오마카세 버튼 / PRIME SCORE / 결과 기록 유지.

Railway:
ENABLE_ASIA_LINEUP_FALLBACK=true
KBO_NAVER_GATEWAY=https://api-gw.sports.naver.com
NPB_YAHOO_BASE=https://baseball.yahoo.co.jp

주의: 두 보조 소스는 공식 개발자 API가 아니거나 HTML 파싱 방식이므로 구조 변경 시 수정이 필요할 수 있습니다.
