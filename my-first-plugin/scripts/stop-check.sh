#!/bin/bash
# Stop hook — 세션 종료 전 미커밋 변경사항 경고

git rev-parse --git-dir > /dev/null 2>&1 || exit 0

count=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
[ "$count" -eq 0 ] && exit 0

echo ""
echo "⚠️  [tenbagger] 미커밋 변경사항 ${count}개 있음 — 푸시 전 커밋하세요."
git status --short 2>/dev/null | head -15
echo ""
