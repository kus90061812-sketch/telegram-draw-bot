# SPORT NOW v15 CLEAN

기존 v14 패치 누적판을 이어붙이지 않고 새로 만든 최소 구조입니다.

## 원칙
- Sportradar 완전 제거
- 배당 분석 완전 제거
- 뉴스 + 라인업 + 선발 + 부상/결장 + 일정/최근 흐름만 AI에 전달
- AI는 제공된 데이터 밖의 사실을 만들지 않도록 제한
- 코멘트 1문장, 핵심 근거 2~3개
- 야구는 라인업이 없으면 T-30분까지 대기, 이후 '라인업 미확인' 상태로만 분석 가능
- MLB: MLB Stats API
- KBO: Naver Sports 일정/라인업 + KBO 공식 사이트를 뉴스/검증 소스로 확장 가능
- NPB: NPB.jp 일정/오더
- 축구/농구: ESPN 일정/summary
- 결과는 provider event ID로만 조회하도록 설계. 팀명만으로 전날 경기 결과를 매칭하지 않음.

## 주의
KBO/NPB/축구 사이트/API 응답 구조는 외부에서 변경될 수 있습니다.
그래서 소스별 adapter 함수를 분리했고, 한 소스 실패가 전체 cycle을 죽이지 않도록 구성했습니다.

## Railway
Start Command:
python main.py

Variables:
.env.example 참고

## v15.1 게시 시간 규칙
- 신규 픽 분석/게시 구간: 경기 시작 120분 전 ~ 30분 전
- 경기 시작 30분 이내: 신규 픽 무조건 스킵
- 야구: T-120부터 라인업을 반복 확인하고, 라인업이 확인된 경우에만 분석/게시
- 야구 라인업이 T-30까지 확인되지 않으면 해당 경기는 자동 스킵
- 라인업 대기 마감과 게시 마감을 별도 조건으로 두지 않아 경계 충돌 방지
- `NEW_PICK_CUTOFF_MINUTES=30`

## v15.4 KBO official schedule hotfix
- Removed every `api-gw.sports.naver.com` / `disabled.invalid` request.
- KBO schedule discovery now uses the official KBO Daily Schedule page.
- No fake replacement hostname.
- KBO lineup remains fail-closed: without a verified lineup source, no KBO pick is published.
- T-30 cutoff remains.

## v15.5 ESPN fallback
ESPN schedule requests now try:
1. site.api.espn.com
2. site.web.api.espn.com
3. cdn.espn.com scoreboard fallback

403/404/429/5xx from one ESPN host no longer stops that league immediately.
Applies to all ESPN-backed soccer leagues and NBA. T-30 cutoff is unchanged.
