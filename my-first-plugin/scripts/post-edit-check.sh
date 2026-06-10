#!/bin/bash
# PostToolUse hook — Edit/Write 직후 자동 정적 분석
# stdin: JSON { tool_input: { file_path: "..." } }

raw=$(cat)
[ -z "$raw" ] && exit 0

file=$(echo "$raw" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('file_path', ''))
except:
    print('')
" 2>/dev/null)

[ -z "$file" ] && exit 0
[ ! -f "$file" ] && exit 0

name=$(basename "$file")
warnings=()

# ── HTML 검사 ──────────────────────────────────────────────
if [[ "$file" == *.html ]]; then
    content=$(cat "$file")

    # script 태그 균형
    opens=$(grep -o '<script' "$file" | wc -l)
    closes=$(grep -o '</script>' "$file" | wc -l)
    if [ "$opens" != "$closes" ]; then
        warnings+=("script 태그 불일치 (열림 $opens / 닫힘 $closes)")
    fi

    # mobile-tab-bar 누락
    has_nav=$(grep -c 'class="nav-links"' "$file" 2>/dev/null || echo 0)
    has_tab=$(grep -c 'mobile-tab-bar' "$file" 2>/dev/null || echo 0)
    if [ "$has_nav" -gt 0 ] && [ "$has_tab" -eq 0 ] && [ "$name" != "login.html" ]; then
        warnings+=("nav-links 있으나 mobile-tab-bar 없음 — 모바일 네비게이션 누락")
    fi

    # viewport 메타 누락
    if ! grep -q 'name="viewport"' "$file"; then
        warnings+=("viewport 메타태그 없음 — 모바일 렌더링 불가")
    fi

    # res.ok 체크 없는 fetch
    fetch_count=$(grep -c 'await fetch(' "$file" 2>/dev/null || echo 0)
    res_ok_count=$(grep -c 'res\.ok\|\.ok\b' "$file" 2>/dev/null || echo 0)
    if [ "$fetch_count" -gt 0 ] && [ "$res_ok_count" -eq 0 ]; then
        warnings+=("fetch() 후 res.ok 체크 없음 — HTTP 에러 미처리 가능")
    fi
fi

# ── Python 검사 ────────────────────────────────────────────
if [[ "$file" == *.py ]]; then
    result=$(python3 -c "
import ast, sys
try:
    ast.parse(open('$file', encoding='utf-8').read())
    print('OK')
except SyntaxError as e:
    print(f'SyntaxError: {e}')
" 2>&1)
    if [ "$result" != "OK" ]; then
        warnings+=("Python 구문 오류: $result")
    fi
fi

# ── 결과 출력 ──────────────────────────────────────────────
if [ ${#warnings[@]} -eq 0 ]; then
    echo "✅ [tenbagger] $name — 이상 없음"
else
    echo ""
    echo "⚠️  [tenbagger] $name 에서 ${#warnings[@]}건 감지됨"
    for w in "${warnings[@]}"; do
        echo "   • $w"
    done
    echo ""
fi
