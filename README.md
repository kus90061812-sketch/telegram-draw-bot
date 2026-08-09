# SportNow News Bot v2

국내 + 해외 스포츠 뉴스를 수집해 Telegram `@sportnow0` 채널에 자동 게시합니다.

## v2 추가 기능
- 해외 스포츠 기사 제목 자동 한국어 번역
- 해외 RSS 설명 기반 2~3문장 한국어 요약
- 국내 기사도 짧은 AI 요약 가능
- 해외 번역 실패 시 영어 원문을 그대로 게시하지 않고 다음 주기에 재시도
- 출처와 원문 링크 유지
- 중복 게시 방지
- 첫 실행 기존 기사 도배 방지

## Railway Variables

반드시 Railway의 Variables에서 입력하세요.

TELEGRAM_BOT_TOKEN = BotFather 토큰
OPENAI_API_KEY = OpenAI API 키
TELEGRAM_CHANNEL_ID = @sportnow0
CHECK_INTERVAL = 300
MAX_POSTS_PER_CYCLE = 8
FIRST_RUN_SKIP_EXISTING = true
SUMMARIZE_KOREAN = true
OPENAI_MODEL = gpt-5-mini

중요: BOT TOKEN과 OPENAI_API_KEY를 GitHub 코드에 직접 적지 마세요.

## Railway Start Command
python main.py

## 게시 예시

🌍 해외축구
🌐 해외 기사 자동번역

맨체스터 유나이티드, ○○ 영입 협상 진전

맨유가 ○○ 영입을 두고 협상을 진행하고 있다. 구체적인 조건은 아직 공개되지 않았다.

📰 출처: BBC Sport
🔗 원문 기사 보기

## 참고
AI 요약은 RSS에 포함된 제목과 짧은 설명만 사용합니다.
원문 전체를 복사하거나 재게시하지 않습니다.

국내기사 AI 요약 비용을 줄이고 싶다면:
SUMMARIZE_KOREAN=false

로 설정하면 국내기사는 제목만 게시하고 해외기사만 AI 번역/요약합니다.

SQLite DB는 Railway 재배포/인스턴스 교체 시 사라질 수 있으므로
장기 운영 시 PostgreSQL 전환을 권장합니다.
