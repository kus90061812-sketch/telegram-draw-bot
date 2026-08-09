# SportNow v6 — PostgreSQL + 자동 적중 결과

## 추가된 기능
- Railway PostgreSQL 지원
- 픽별 event_id / 리그 / 홈·원정 / 선택팀 / 우세도 저장
- 경기 종료 후 최종 스코어 자동 조회
- 적중 / 미적중 / 무승부 자동 판정
- 결과를 Telegram 채널에 자동 게시
- 최근 24시간 성적 + 누적 성적 / 적중률 자동 표시
- 재배포해도 PostgreSQL에 기록 유지

## Railway 설정

### 1) PostgreSQL 추가
Railway 프로젝트에서:
New → Database → Add PostgreSQL

추가하면 일반적으로 `DATABASE_URL` 변수가 만들어집니다.
봇 서비스 Variables에서도 DATABASE_URL을 참조/연결하세요.

### 2) Variables
기존 변수 + 아래를 확인하세요.

DATABASE_URL=Railway PostgreSQL 연결주소
ENABLE_RESULT_POSTS=true

ENABLE_NEWS_PICKS=true
PREMATCH_MIN_MINUTES=90
PREMATCH_MAX_MINUTES=240
MAX_PICKS_PER_DAY=4

### 3) SQL
`schema.sql`을 제공합니다.
하지만 main.py가 시작될 때 필요한 테이블을 자동 생성하므로 보통 직접 실행할 필요는 없습니다.

## 결과 게시 예시

🏁 SPORT NOW PICK RESULT

🏆 MLB
San Diego Padres 3 : 6 Los Angeles Dodgers

🎯 사전 PICK: Los Angeles Dodgers
📊 뉴스 기반 우세도: 68%
📌 결과: ✅ 적중

📈 최근 24시간: 3승 1패 (75.0%)
📚 누적: 38승 21패 (64.4%)

## 주의
현재 픽의 %는 뉴스 기반 AI 추정 우세도이며,
배당/공식 통계 모델의 실제 승률이 아닙니다.
