# 제품 흐름 원본 (테마 · 검증 일지)

세션은 운영 DB에 닿지 못하므로, 테마와 승인 스냅샷의 원본은 이 폴더의 JSON이다.
백엔드가 기동할 때 `backend/app/domain/product_seed.py`가 DB(`product_*` 테이블)로 옮긴다.
규칙은 `ops/product/SKILL.md`, 판정은 `ops/product/proof.md`.

## themes/<id>.json

```json
{
  "id": "grid-2026",
  "name": "전력망 교체 주기",
  "line": "한 줄 내러티브",
  "competence": "능력 범위: 이해할 수 있는 사업인가",
  "money": "누가 돈을 버나: 이익이 어디에 쌓이나",
  "invert": "뒤집어 보기: 무엇이 틀리면 깨지나",
  "safety": "안전마진: 틀려도 덜 잃는 이유",
  "sources": [{"title": "근거 제목", "url": "https://..."}],
  "status": "draft | approved | retired",
  "approved_by": "owner | ai-delegated",
  "approved_at": "YYYY-MM-DD",
  "candidates": [
    {"ticker": "000000", "name": "회사명", "stage": 3, "survival_pass": true},
    {"ticker": "000001", "name": "회사명", "survival_pass": false, "excluded_reason": "부채비율 350%"}
  ],
  "evidence": [
    {"ticker": "000000", "link": 1, "status": "확인", "claim": "주장", "source_url": "https://...", "source_type": "공시", "checked_at": "YYYY-MM-DD"}
  ]
}
```

- 4필터(competence·money·invert·safety)가 하나라도 비면 DB에 올라가지 않는다 (proof #3).
- `source_url`이 없는 근거는 `확인`으로 적어도 `미확인`으로 저장된다 (proof #2).
- `draft`는 API로 공개되지 않는다.

## snapshots/<TB-SNAP-0001>.json

```json
{"id": "TB-SNAP-0001", "ticker": "000000", "name": "회사명", "theme_id": "grid-2026", "version": 1,
 "approved_by": "ai-delegated", "approved_at": "YYYY-MM-DD", "price_at": 12000,
 "notion_url": "https://...", "payload": {"stage": 3, "...": "승인 순간의 화면 내용 전체"}}
```

- 한 번 커밋한 스냅샷 파일은 고치지 않는다. DB 트리거가 수정·삭제를 막고, 파일을 고치면 동기화가 건너뛴다.
  바뀌면 `version`을 올린 새 파일(새 ID)을 만든다 (proof #4).
- `content_hash`는 적지 않아도 된다. 적었다면 내용과 맞아야 한다.
