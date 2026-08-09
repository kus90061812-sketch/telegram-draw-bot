# SPORT NOW v10.2

수정:
- ESPN summary의 `news` 필드가 dict로 오는 경우 발생하던
  `KeyError: slice(None, 5, None)` 오류 수정
- list/dict/기타 응답 모두 안전하게 처리
- 기존 팀명 한글화 / PRIME SCORE / 야구 모델 / 픽 결과 기록 유지
