# 🚀 텐배거 헌터

한국 주식 장기투자 분석 시스템 · DART + pykrx 기반

## 빠른 시작

### 1. .env 파일 설정
```
# tenbagger/.env
DART_API_KEY=본인_DART_API_키_입력
```

### 2. Docker로 백엔드 실행
```bash
cd C:\Users\00LG00\OneDrive\tenbagger
docker-compose up --build
```

### 3. 프론트엔드 열기
`frontend/index.html` 파일을 브라우저에서 직접 열기

### 4. 분석 시작
- 종목코드 입력 (예: 005930 삼성전자)
- 분석하기 클릭
- 약 20~30초 후 결과 확인

## 분석 항목
| 카테고리 | 가중치 | 핵심 지표 |
|---|---|---|
| 성장성 | 30% | 매출 CAGR, EPS CAGR, ROE |
| 재무 안정성 | 20% | 부채비율, 유동비율 |
| 현금흐름 | 20% | FCF 마진, FCF 품질 |
| 배당 정책 | 15% | 배당성향, 배당수익률 |
| 실적 일관성 | 15% | 영업이익률 변동성, 적자 횟수 |

## 텐배거 등급 기준
- ⭐ **TENBAGGER**: 종합 7.5점 이상 + 성장성 8.0점 이상
- 🟢 **COMPOUNDER**: 종합 6.5점 이상
- 🟡 **WATCHLIST**: 종합 5.0점 이상
- 🔴 **AVOID**: 5.0점 미만

## API 문서
백엔드 실행 후 → http://localhost:8000/docs
