# SPORT NOW v13.3.9 — Real Unlimited / No Score Cut

이번 버전에서 실제로 남아 있던 제한을 모두 제거했습니다.

제거:
- AI 프롬프트의 55점 미만 제외 문구
- Python의 MIN_NEWS_EDGE 점수 필터
- AI 응답 data[:4] 4개 제한
- 그룹별 max_picks 제한
- 뉴스 3개 미만이면 전체 분석 중단하는 조건
- source_ids가 없으면 픽을 버리는 조건

유지:
- 라인업 확인
- 경기 데이터 기반 분석
- PRIME SCORE는 참고용
- 픽 개수 제한 없음
- FINAL COMBO 없음
- 중복 경기 게시 방지
- KBO/NPB Sportradar 일정
- KBO/NPB/MLB Sportradar 라인업
