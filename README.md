# SPORT NOW v10 — Baseball Weighted Model

야구(KBO/NPB/MLB)는 별도 모델로 평가합니다.

가중치:
- 선발 30%
- 타선 25%
- 불펜 20%
- 최근 팀 흐름 15%
- 라인업/결장/기타 10%

원칙:
- 선발 하나만 보고 픽하지 않음
- 경기 전 알 수 없는 '당일 컨디션'은 추측하지 않음
- PRIME SCORE는 실제 승률이 아니라 상대 우세 지표
- 최소 게시 기준 55 유지
- 뉴스는 공개하지 않고 내부 분석 재료로만 사용
- 경기 종료 후 적중/미적중과 누적 기록 유지

Railway 추가:
BASEBALL_STARTER_WEIGHT=0.30
BASEBALL_OFFENSE_WEIGHT=0.25
BASEBALL_BULLPEN_WEIGHT=0.20
BASEBALL_FORM_WEIGHT=0.15
BASEBALL_LINEUP_WEIGHT=0.10
