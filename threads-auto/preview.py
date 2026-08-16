"""발행 전 큐 내용을 HTML로 미리 본다.

queue.json을 읽어 Threads 게시물 모양으로 렌더링하고 브라우저로 연다.
글자 수·재무 근거(숫자) 유무·책임 고지 등 발행 전 점검 항목을 함께 표시한다.

사용법:
  python preview.py              # preview.html 생성 후 브라우저로 열기
  python preview.py --no-open    # 파일만 생성
"""
from __future__ import annotations

import os
import re
import sys
import json
import html
import webbrowser
from pathlib import Path
from typing import List, Dict, Any

# Threads 본문 상한
MAX_CHARS = 500
DISCLAIMER = "투자 판단"


def load_items(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"큐 파일을 읽을 수 없습니다: {exc}")
        return []


def check(text: str) -> List[Dict[str, str]]:
    """발행 전 점검 결과를 [{level, label}] 로 반환한다."""
    results = []

    n = len(text)
    if n > MAX_CHARS:
        results.append({"level": "bad", "label": f"{n}자 — 상한 {MAX_CHARS}자 초과"})
    else:
        results.append({"level": "ok", "label": f"{n}자"})

    # 재무 근거: 숫자가 거의 없으면 일반론일 가능성이 높다
    digits = len(re.findall(r"\d", text))
    if digits >= 6:
        results.append({"level": "ok", "label": f"숫자 {digits}개 — 재무 근거 있음"})
    elif digits > 0:
        results.append({"level": "warn", "label": f"숫자 {digits}개 — 근거 빈약"})
    else:
        results.append({"level": "bad", "label": "숫자 없음 — 일반론 위험"})

    if DISCLAIMER in text:
        results.append({"level": "ok", "label": "책임 고지 있음"})
    else:
        results.append({"level": "bad", "label": "책임 고지 없음"})

    tags = len(re.findall(r"#\S+", text))
    if tags:
        results.append({"level": "ok", "label": f"해시태그 {tags}개"})
    else:
        results.append({"level": "warn", "label": "해시태그 없음"})

    return results


def render_text(text: str) -> str:
    """본문을 HTML로. 해시태그만 강조하고 줄바꿈을 살린다."""
    escaped = html.escape(text)
    escaped = re.sub(r"(#[^\s#]+)", r'<span class="tag">\1</span>', escaped)
    return escaped.replace("\n", "<br>")


STATUS_LABEL = {"pending": "발행 대기", "published": "발행됨", "failed": "실패"}


def build_html(items: List[Dict[str, Any]], queue_path: Path) -> str:
    pending = sum(1 for i in items if i.get("status", "pending") == "pending")

    cards = []
    for item in items:
        text = item.get("text", "")
        status = item.get("status", "pending")
        checks = check(text)
        worst = "bad" if any(c["level"] == "bad" for c in checks) else (
            "warn" if any(c["level"] == "warn" for c in checks) else "ok"
        )
        # 발행된 글은 이미 나갔으므로 점검 강조를 하지 않는다
        if status == "published":
            worst = "done"

        chips = "".join(
            f'<span class="chip {c["level"]}">{html.escape(c["label"])}</span>'
            for c in checks
        )
        media = ""
        if item.get("image_url") or item.get("video_url"):
            kind = "영상" if item.get("video_url") else "이미지"
            url = item.get("video_url") or item.get("image_url")
            media = f'<div class="media">{kind} 첨부: {html.escape(url)}</div>'

        cards.append(f"""
        <article class="card {worst}">
          <header class="card-head">
            <span class="id">#{html.escape(str(item.get("id", "?")))}</span>
            <span class="status {status}">{STATUS_LABEL.get(status, status)}</span>
          </header>
          <div class="post">
            <div class="avatar"></div>
            <div class="body">
              <div class="handle">yoonchangpark</div>
              <div class="text">{render_text(text)}</div>
              {media}
            </div>
          </div>
          <footer class="checks">{chips}</footer>
        </article>""")

    body = "\n".join(cards) if cards else '<p class="empty">큐가 비어 있습니다.</p>'

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>발행 미리보기 — threads-auto</title>
<style>
  :root {{
    --paper:#FAFAF8; --surface:#FFF; --ink:#17191C; --muted:#5C6169; --line:#E7E5DF;
    --ok:#0F9D63; --ok-bg:#E8F5EF; --warn:#B7790A; --warn-bg:#FBF1DC;
    --bad:#C0392B; --bad-bg:#FAEAE8; --done:#8A8F96;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --paper:#0E1012; --surface:#16191C; --ink:#ECEDEA; --muted:#9AA0A6; --line:#272B2F;
      --ok:#34D399; --ok-bg:#11271E; --warn:#E0A93A; --warn-bg:#2A2113;
      --bad:#F87171; --bad-bg:#2C1717; --done:#767C82;
    }}
  }}
  * {{ box-sizing:border-box }}
  body {{
    margin:0; padding:32px 20px; background:var(--paper); color:var(--ink);
    font-family:system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;
    line-height:1.6;
  }}
  .wrap {{ max-width:640px; margin:0 auto; display:flex; flex-direction:column; gap:16px }}
  h1 {{ font-size:1.4rem; margin:0; letter-spacing:-.01em }}
  .sub {{ color:var(--muted); font-size:.9rem; margin:4px 0 8px }}
  .card {{
    background:var(--surface); border:1px solid var(--line); border-radius:14px;
    padding:16px 18px; border-left:3px solid var(--line);
  }}
  .card.ok {{ border-left-color:var(--ok) }}
  .card.warn {{ border-left-color:var(--warn) }}
  .card.bad {{ border-left-color:var(--bad) }}
  .card.done {{ opacity:.6 }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px }}
  .id {{ font-size:.8rem; color:var(--muted); font-variant-numeric:tabular-nums }}
  .status {{ font-size:.72rem; font-weight:650; padding:3px 10px; border-radius:999px; background:var(--warn-bg); color:var(--warn) }}
  .status.published {{ background:var(--ok-bg); color:var(--ok) }}
  .status.failed {{ background:var(--bad-bg); color:var(--bad) }}
  .post {{ display:flex; gap:12px }}
  .avatar {{ flex:none; width:36px; height:36px; border-radius:50%; background:var(--line) }}
  .handle {{ font-weight:650; font-size:.92rem; margin-bottom:2px }}
  .text {{ white-space:normal; word-break:break-word }}
  .tag {{ color:var(--ok) }}
  .media {{ margin-top:8px; font-size:.82rem; color:var(--muted) }}
  .checks {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:14px; padding-top:12px; border-top:1px dashed var(--line) }}
  .chip {{ font-size:.74rem; padding:3px 9px; border-radius:999px; font-weight:600 }}
  .chip.ok {{ background:var(--ok-bg); color:var(--ok) }}
  .chip.warn {{ background:var(--warn-bg); color:var(--warn) }}
  .chip.bad {{ background:var(--bad-bg); color:var(--bad) }}
  .empty {{ color:var(--muted); text-align:center; padding:40px }}
  footer.note {{ color:var(--muted); font-size:.82rem; margin-top:8px }}
  code {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.85em }}
</style>
</head>
<body>
<div class="wrap">
  <div>
    <h1>발행 미리보기</h1>
    <p class="sub">전체 {len(items)}건 · 발행 대기 {pending}건 · <code>{html.escape(str(queue_path))}</code></p>
  </div>
  {body}
  <footer class="note">
    발행하려면 <code>python main.py --once</code> · 내용을 고치려면 큐 파일을 직접 편집하세요.
  </footer>
</div>
</body>
</html>"""


def main() -> int:
    queue_path = Path(os.getenv("QUEUE_FILE", "queue.json"))
    items = load_items(queue_path)

    out = Path("preview.html")
    out.write_text(build_html(items, queue_path), encoding="utf-8")
    print(f"{out} 생성 완료 ({len(items)}건)")

    if "--no-open" not in sys.argv:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
