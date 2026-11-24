"""
제품 흐름 v2 원본(content/product/*.json) → DB 동기화

세션은 운영 DB와 운영 URL에 닿지 못한다. 그래서 테마·스냅샷의 원본은 저장소 파일이고,
기동할 때 이 모듈이 DB로 옮긴다. 원본이 git에 남으므로 누가 언제 무엇을 승인했는지가 기록된다.

- 테마: 파일이 정답. 매 기동마다 덮어쓰고, 후보 목록은 테마 단위로 통째로 교체한다.
- 스냅샷: 한 번 들어가면 고치지 않는다. 이미 있는 ID는 건너뛰고, 파일 내용 해시가
  DB와 다르면 경고만 남긴다(DB 트리거가 수정을 막는다). 고칠 일이 생기면 새 버전 파일을 만든다.
"""
import hashlib
import json
import os

from sqlalchemy import text

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "content", "product")
THEME_FIELDS = ("id", "name", "line", "competence", "money", "invert", "safety")
SNAPSHOT_FIELDS = ("id", "ticker", "name", "approved_by", "approved_at", "payload")


def snapshot_hash(snap: dict) -> str:
    """content_hash를 뺀 스냅샷 전체의 정규화 JSON sha256."""
    body = {k: v for k, v in snap.items() if k != "content_hash"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_dir(name: str) -> list[dict]:
    folder = os.path.join(ROOT, name)
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder)):
        if fn.endswith(".json"):
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def _missing(obj: dict, fields: tuple) -> list[str]:
    return [k for k in fields if not obj.get(k)]


def sync_product_seed(db) -> dict:
    """원본 파일을 DB에 반영하고 처리 결과를 돌려준다. 실패한 항목은 건너뛴다."""
    result = {"themes": 0, "snapshots_added": 0, "skipped": []}

    for t in _load_dir("themes"):
        miss = _missing(t, THEME_FIELDS)
        if miss:
            result["skipped"].append(f"theme {t.get('id')}: 빈 칸 {miss}")
            continue
        db.execute(text("""
            INSERT INTO product_themes
                (id, name, line, competence, money, invert, safety, sources,
                 status, approved_by, approved_at, updated_at)
            VALUES (:id, :name, :line, :competence, :money, :invert, :safety, CAST(:sources AS JSONB),
                    :status, :approved_by, :approved_at, NOW())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, line = EXCLUDED.line, competence = EXCLUDED.competence,
                money = EXCLUDED.money, invert = EXCLUDED.invert, safety = EXCLUDED.safety,
                sources = EXCLUDED.sources, status = EXCLUDED.status,
                approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at,
                updated_at = NOW()
        """), {
            **{k: t[k] for k in THEME_FIELDS},
            "sources": json.dumps(t.get("sources", []), ensure_ascii=False),
            "status": t.get("status", "draft"),
            "approved_by": t.get("approved_by"),
            "approved_at": t.get("approved_at"),
        })
        db.execute(text("DELETE FROM product_theme_candidates WHERE theme_id = :id"), {"id": t["id"]})
        for c in t.get("candidates", []):
            db.execute(text("""
                INSERT INTO product_theme_candidates
                    (theme_id, ticker, name, stage, survival_pass, excluded_reason)
                VALUES (:theme_id, :ticker, :name, :stage, :survival_pass, :excluded_reason)
            """), {
                "theme_id": t["id"], "ticker": c["ticker"], "name": c["name"],
                "stage": c.get("stage"), "survival_pass": c.get("survival_pass"),
                "excluded_reason": c.get("excluded_reason"),
            })
        for e in t.get("evidence", []):
            db.execute(text("""
                INSERT INTO product_link_evidence
                    (ticker, link, status, claim, source_url, source_type, checked_at)
                VALUES (:ticker, :link, :status, :claim, :source_url, :source_type, :checked_at)
                ON CONFLICT (ticker, link, claim) DO UPDATE SET
                    status = EXCLUDED.status, source_url = EXCLUDED.source_url,
                    source_type = EXCLUDED.source_type, checked_at = EXCLUDED.checked_at
            """), {
                "ticker": e["ticker"], "link": e["link"],
                # 출처 링크 없는 주장은 '확인'으로 올리지 않는다 (proof #2)
                "status": e.get("status", "미확인") if e.get("source_url") else "미확인",
                "claim": e["claim"], "source_url": e.get("source_url"),
                "source_type": e.get("source_type"), "checked_at": e.get("checked_at"),
            })
        result["themes"] += 1

    existing = {r[0]: r[1] for r in db.execute(text("SELECT id, content_hash FROM product_snapshots"))}
    for s in _load_dir("snapshots"):
        miss = _missing(s, SNAPSHOT_FIELDS)
        if miss:
            result["skipped"].append(f"snapshot {s.get('id')}: 빈 칸 {miss}")
            continue
        h = snapshot_hash(s)
        if s.get("content_hash") and s["content_hash"] != h:
            result["skipped"].append(f"snapshot {s['id']}: 파일의 content_hash가 내용과 다름")
            continue
        if s["id"] in existing:
            if existing[s["id"]] != h:
                result["skipped"].append(f"snapshot {s['id']}: DB와 내용이 다름 (수정 금지, 새 버전으로)")
            continue
        db.execute(text("""
            INSERT INTO product_snapshots
                (id, ticker, name, theme_id, version, approved_by, approved_at,
                 price_at, notion_url, payload, content_hash)
            VALUES (:id, :ticker, :name, :theme_id, :version, :approved_by, :approved_at,
                    :price_at, :notion_url, CAST(:payload AS JSONB), :content_hash)
        """), {
            "id": s["id"], "ticker": s["ticker"], "name": s["name"], "theme_id": s.get("theme_id"),
            "version": s.get("version", 1), "approved_by": s["approved_by"],
            "approved_at": s["approved_at"], "price_at": s.get("price_at"),
            "notion_url": s.get("notion_url"),
            "payload": json.dumps(s["payload"], ensure_ascii=False), "content_hash": h,
        })
        result["snapshots_added"] += 1

    db.commit()
    return result
