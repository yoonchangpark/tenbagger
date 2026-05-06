@echo off
REM ============================================================
REM 텐배거 헌터 - Claude Code 에이전트 실행 스크립트
REM Windows 작업 스케줄러에 등록해서 자동 실행 가능
REM ============================================================

SET PROJECT_DIR=C:\Users\00LG00\Desktop\tenbagger
SET LOG_DIR=%PROJECT_DIR%\logs
SET TIMESTAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%
SET LOG_FILE=%LOG_DIR%\agent_%TIMESTAMP%.log

REM 로그 디렉토리 생성
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%DATE% %TIME%] 텐배거 에이전트 시작 >> "%LOG_FILE%"
echo ============================================================
echo  텐배거 헌터 에이전트
echo  시작 시간: %DATE% %TIME%
echo ============================================================

REM 인자 처리
SET MODE=%1
if "%MODE%"=="" SET MODE=daily

echo 실행 모드: %MODE%

REM ============================================================
REM 모드별 실행
REM ============================================================

if "%MODE%"=="daily" goto :DAILY_ADVISOR
if "%MODE%"=="accuracy" goto :ACCURACY_CHECK
if "%MODE%"=="improve" goto :IMPROVE_SCORING
if "%MODE%"=="invest" goto :INVESTMENT_ADVISOR
if "%MODE%"=="status" goto :STATUS_CHECK
goto :USAGE

REM ============================================================
:DAILY_ADVISOR
REM ============================================================
echo.
echo [일일 어드바이저 실행]
echo Claude Code가 daily_advisor.md 프롬프트를 실행합니다...
echo.

cd /d "%PROJECT_DIR%"

REM Claude Code로 에이전트 실행
claude --print --allowedTools "Bash,Read,Write,Edit" ^
  "agent_prompts/daily_advisor.md 파일을 읽고 모든 Step을 순서대로 실행하라. 오류가 발생해도 계속 진행하고 최종 결과를 보고하라." ^
  >> "%LOG_FILE%" 2>&1

echo [%DATE% %TIME%] 일일 어드바이저 완료 >> "%LOG_FILE%"
goto :END

REM ============================================================
:ACCURACY_CHECK
REM ============================================================
echo.
echo [정확도 검증 실행]
echo Claude Code가 accuracy_check.md 프롬프트를 실행합니다...
echo.

cd /d "%PROJECT_DIR%"

claude --print --allowedTools "Bash,Read,Write,Edit" ^
  "agent_prompts/accuracy_check.md 파일을 읽고 정확도 검증을 실행하라. 결과를 logs/accuracy_%TIMESTAMP%.txt에 저장하라." ^
  >> "%LOG_FILE%" 2>&1

echo [%DATE% %TIME%] 정확도 검증 완료 >> "%LOG_FILE%"
goto :END

REM ============================================================
:IMPROVE_SCORING
REM ============================================================
echo.
echo [스코어링 개선 실행]
echo Claude Code가 improve_scoring.md 프롬프트를 실행합니다...
echo.

cd /d "%PROJECT_DIR%"

claude --print --allowedTools "Bash,Read,Write,Edit" ^
  "agent_prompts/improve_scoring.md 파일을 읽고 최근 accuracy 로그를 분석한 후 필요한 경우 scoring.py를 개선하라. 반드시 CHANGELOG.md를 업데이트하라." ^
  >> "%LOG_FILE%" 2>&1

echo [%DATE% %TIME%] 스코어링 개선 완료 >> "%LOG_FILE%"
goto :END

REM ============================================================
:INVESTMENT_ADVISOR
REM ============================================================
echo.
echo [투자 어드바이저 실행]
SET /P QUESTION=투자 질문을 입력하세요:

cd /d "%PROJECT_DIR%"

claude --print --allowedTools "Bash,Read,Write,Edit,WebSearch" ^
  "agent_prompts/investment_advisor.md 파일을 읽고 다음 질문에 답변하라: %QUESTION%" ^
  >> "%LOG_FILE%" 2>&1

type "%LOG_FILE%"
goto :END

REM ============================================================
:STATUS_CHECK
REM ============================================================
echo.
echo [시스템 상태 확인]

docker ps | findstr tenbagger
echo.

docker exec tenbagger_db psql -U tenbagger -d tenbagger -c ^
  "SELECT grade, COUNT(*) as cnt, ROUND(AVG(total_score),2) as avg FROM score_cache GROUP BY grade ORDER BY avg DESC;" ^
  2>nul || echo DB 연결 실패

echo.
echo 마지막 ETL 로그:
docker exec tenbagger_scheduler tail -5 /app/reports/advisor.log 2>nul || echo 로그 없음

goto :END

REM ============================================================
:USAGE
REM ============================================================
echo.
echo 사용법: run_agent.bat [모드]
echo.
echo  daily    - 일일 ETL + 어드바이저 리포트 (기본값)
echo  accuracy - 주간 정확도 검증
echo  improve  - 월간 스코어링 개선
echo  invest   - 온디맨드 투자 질문 답변
echo  status   - 시스템 상태 빠른 확인
echo.
echo 예시:
echo  run_agent.bat daily
echo  run_agent.bat invest
echo  run_agent.bat status
goto :END

REM ============================================================
:END
REM ============================================================
echo.
echo [%DATE% %TIME%] 에이전트 실행 완료
echo 로그: %LOG_FILE%
echo.
pause
