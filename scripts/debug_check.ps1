# ─────────────────────────────────────────────────────────────
# 텐배거 디버깅 에이전트 — PostToolUse 정적 분석 훅
# Edit / Write 도구 실행 직후 자동으로 호출됨
# 훅 입력: stdin JSON { tool_input: { file_path: "..." } }
# ─────────────────────────────────────────────────────────────

param()
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# stdin에서 도구 입력 읽기
$raw = @($input) -join "`n"
if (-not $raw -or $raw.Trim() -eq "") { exit 0 }

try {
    $data = $raw | ConvertFrom-Json
} catch {
    exit 0
}

$file = $data.tool_input.file_path
if (-not $file -or -not (Test-Path $file)) { exit 0 }

$warnings = @()
$name = Split-Path $file -Leaf

# ── HTML 검사 ──────────────────────────────────────────────
if ($file -match '\.html$') {
    $c = Get-Content $file -Raw -Encoding UTF8

    # script 태그 균형
    $opens  = ([regex]::Matches($c, '<script')).Count
    $closes = ([regex]::Matches($c, '</script>')).Count
    if ($opens -ne $closes) {
        $warnings += "script 태그 불일치 (열림 $opens / 닫힘 $closes)"
    }

    # mobile-tab-bar 유무 (nav-links 있는 페이지는 탭바도 있어야 함)
    $hasNav = $c -match 'class="nav-links"'
    $hasTab = $c -match 'mobile-tab-bar'
    if ($hasNav -and -not $hasTab -and $name -ne 'login.html') {
        $warnings += "nav-links 있으나 mobile-tab-bar 없음 — 모바일 네비게이션 누락"
    }

    # viewport 메타 누락
    if (-not ($c -match 'name="viewport"')) {
        $warnings += "viewport 메타태그 없음 — 모바일 렌더링 불가"
    }

    # AbortController 없는 fetch (index.html 한정)
    if ($name -eq 'index.html' -and ($c -match 'await fetch') -and -not ($c -match 'AbortController')) {
        $warnings += "fetch 호출에 AbortController 없음 — 취소 불가 요청 존재"
    }
}

# ── Python 검사 ────────────────────────────────────────────
if ($file -match '\.py$') {
    $pyCmd = "python -c `"import ast, sys; ast.parse(open(r'$file', encoding='utf-8').read()); print('OK')`""
    $result = Invoke-Expression $pyCmd 2>&1
    if ($result -ne 'OK') {
        $warnings += "Python 구문 오류: $result"
    }
}

# ── 결과 출력 ──────────────────────────────────────────────
if ($warnings.Count -eq 0) {
    Write-Host "✅ [디버깅 에이전트] $name — 이상 없음"
} else {
    Write-Host ""
    Write-Host "⚠️  [디버깅 에이전트] $name 에서 $($warnings.Count)건 감지됨 — PM 검토 필요"
    foreach ($w in $warnings) {
        Write-Host "   • $w"
    }
    Write-Host "   → .agent.md 기준으로 심층 분석을 실행하세요."
    Write-Host ""
}
