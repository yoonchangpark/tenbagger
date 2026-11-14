# 카드뉴스 이미지

**목적**: CHANGELOG 항목을 "만드는 과정" 카드 세트로 만들어 Threads 글에 붙인다.

**언제 쓰나**: "이번 변경 카드뉴스로 만들어줘", "카드 이미지가 안 붙었어".

**입력과 위치**: 카드 스펙 JSON (`threads-auto/cardnews/examples/*.json` 형식), 결과 PNG는 `frontend/media/threads-posts/`.

## 단계
1. **INTERVIEW** — 카드로 만들 CHANGELOG 버전을 고른다. 버전 하나 = 카드 세트 하나.
2. **MANUAL** — 종목 분석 카드가 아니다. 무엇을 고쳤는지, 숏츠를 만들며 무엇이 터졌는지.
3. **TOOLBOX** — Chromium이 필요해 Railway에서는 못 돈다. 로컬 또는 세션에서 렌더.
4. **EXECUTE** — `python cardnews/render.py <스펙.json>` → PNG 커밋·푸시 → 배포 → `cardnews/publish.py`로 큐에 적재.
5. **VERIFY** — `proof.md`.

## 판단 규칙
- 카드는 `hook` → `compare`(AS-IS/TO-BE) → `body` → `outro`(질문) 순.
- 본문은 직접 쓰지 않고 `generate_devlog_post()`를 부른다.
- 중복 키 `changelog:<버전>`과 `changelog-cards:<버전>`을 **둘 다** 확인한다.

## 완료 기준
카드가 붙은 글이 큐에 있고, 모든 이미지 URL이 배포돼 열린다.
