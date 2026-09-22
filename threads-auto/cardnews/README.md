# 카드뉴스 생성기

빌딩인퍼블릭 카드뉴스를 JSON 하나로 만든다. 소재는 종목 분석이 아니라
**만드는 과정**이다 — 텐배거를 어떻게 고치고 있는지, 숏츠를 만들며 뭐가 터졌는지.

지금까지 `frontend/media/threads-posts/`의 카드들(첫 영상 제작기, 오늘의 디버깅 로그)은
손으로 만들어 다시 만들 방법이 없었다. 이제 스펙 파일이 원본이다.

## 쓰는 법

```bash
cd threads-auto
pip install playwright && playwright install chromium   # 최초 1회
python cardnews/render.py cardnews/examples/2026-09-21-shorts-inventory.json
```

PNG는 `frontend/media/threads-posts/`에 떨어지고, 배포 후 쓸 공개 URL이 같이 출력된다.
`--out <경로>`로 다른 데 쓸 수 있다.

설치된 Playwright와 브라우저 버전이 어긋나면 `CHROMIUM_PATH`로 실행 파일을 직접 지정한다.

## 스펙

```json
{
  "date": "2026-09-21",
  "slug": "shorts-inventory",
  "label": "2026.09.21 — 숏츠 제작 로그",
  "cards": [ ... ]
}
```

카드는 네 종류다.

| kind | 쓸 곳 | 필드 |
|---|---|---|
| `hook` | 표지 | `headline`, `sub` |
| `compare` | AS-IS → TO-BE | `asis`, `tobe`, `asis_tag`, `tobe_tag` |
| `body` | 설명 한 덩어리 | `title`, `body` |
| `outro` | 마무리 + 질문 | `body`, `question` |

모든 카드에 `label`(좌상단)과 `footer`(좌하단)를 따로 줄 수 있다. 안 주면
스펙의 `label`과 기본 문구를 쓴다. 번호(우상단)는 순서대로 자동으로 붙는다.

본문 표기는 두 가지뿐이다: `**굵게**`, 백틱 코드. 줄바꿈(`\n`)은 문단을 나누고,
`hook`·`outro`의 제목·질문에서는 그 위치에서 줄이 바뀐다 — 한글은 자동 줄바꿈이
어색해지므로 표제는 직접 끊는 편이 낫다.

## 발행에 붙이기

`publish.py`가 CHANGELOG 한 항목을 카드 5장 + 본문으로 만들어 큐에 넣는다.

```bash
python cardnews/publish.py                 # 최신 미발행 항목 1건
python cardnews/publish.py --render-only   # 렌더만, 큐에는 안 넣음
python cardnews/publish.py --spec my.json  # 직접 쓴 스펙으로 렌더만
```

카드 문구는 CHANGELOG의 `### 배경`(AS-IS)과 `### 추가`/`### 변경`/`### 수정`(TO-BE)에서
뽑는다. 본문은 `devlog_generator.generate_devlog_post()`를 그대로 쓴다 — 글 쓰는 규칙이
한 곳에만 있도록 복제하지 않았다. 같은 버전을 두 번 내지 않도록 `source_key`로 막는다
(`changelog-cards:v7.3`, 본문만 낸 `changelog:v7.3`도 함께 확인).

직접 붙이려면 이렇게 쓴다.

```python
from cardnews.render import render_spec, public_urls

paths = render_spec(spec, out_dir)
queue.add(text, image_urls=public_urls(paths))
```

### ⚠️ 실행 위치와 순서

렌더에 Chromium이 필요하고, 배포 컨테이너의 파일시스템은 재배포마다 초기화된다.
Meta 서버는 **발행 시점에** `image_url`을 직접 가져가므로, 그때 이미지가 배포돼 있어야 한다.

    이 명령 실행(로컬) → PNG 커밋·푸시 → 배포 확인 → 예정된 발행일에 나감

큐 적재는 Postgres(`CONTENT_QUEUE_BACKEND=postgres`)를 쓰면 로컬에서 실행해도
배포된 스케줄러가 같은 큐를 읽는다.
