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

`render_spec()`이 돌려주는 경로를 `public_urls()`에 넣으면 캐러셀 URL이 나온다.

```python
from cardnews.render import render_spec, public_urls

paths = render_spec(spec, out_dir)
queue.add(text, image_urls=public_urls(paths))
```

Meta 서버가 `image_url`을 직접 가져가므로 **배포된 뒤**에 발행해야 한다.
이미지를 커밋·푸시해서 Railway에 올라간 다음 큐에 넣는 순서다.

글 본문은 `devlog_generator.py`가 쓴다. 여기서는 이미지만 만든다.
