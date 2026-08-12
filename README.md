# SPORT NOW v13.3.4 — Clean Rebuild

정상 v13.2 원본에서 다시 빌드했습니다.

변경:
- 픽 개수 제한 없음
- KBO / NPB / MLB / 축구 / 농구 모두 55점 이상 기준 충족 시 개수 제한 없이 게시
- FINAL COMBO 게시 제거
- 동일 event_id 중복 방지는 유지
- NPB Sportradar 일정 수집 유지
- KBO/NPB/MLB Sportradar 라인업 유지
- PostgreSQL 기존 DB 클래스/psycopg(v3) 구조 그대로 유지

중요:
이 버전은 psycopg2를 새로 쓰지 않습니다.
기존 requirements.txt의 psycopg 구조를 그대로 사용합니다.

Railway에서 삭제 가능:
MAX_PICKS_KBO
MAX_PICKS_NPB
MAX_PICKS_MLB
MAX_PICKS_SOCCER
MAX_PICKS_BASKETBALL
MAX_PICKS_PER_GROUP
MAX_PICKS_PER_DAY
ENABLE_COMBO_PICKS
COMBO_MIN_SCORE
COMBO_MAX_PER_GROUP
