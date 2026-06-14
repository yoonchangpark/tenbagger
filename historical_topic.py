"""
과거 텐배거 해부 모드
"당시 데이터만으로 시스템이 골라냈다"를 검증하는 백테스트 기반 콘텐츠.

재료:
  1. 텐배거 백테스트 API — 당시 재무지표 기준 시스템 점수 + 실제 수익률
  2. 네이버 데이터랩 — 검색량 추이 ("대중 관심 폭발 = 고점" 네러티브)
  3. 검색량 차트 mp4 — source_type 'chart' Scene의 B-roll로 삽입

종목 목록: data/tenbagger_history.json (수동 큐레이션, 직접 추가/수정)

환경변수:
  TENBAGGER_API_BASE — 텐배거 API 주소
  NAVER_CLIENT_ID / NAVER_CLIENT_SECRET — 데이터랩용 (developers.naver.com, 무료)
"""
import os
import json
import logging
import datetime
import httpx

from tenbagger_topic import DISCLAIMER, _fmt
from tenbagger_card import make_score_card

logger = logging.getLogger(__name__)

HISTORY_LIST_FILE = os.path.join("data", "tenbagger_history.json")


def _load_curated_list() -> list[dict]:
    with open(HISTORY_LIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


async def _fetch_backtest(base: str, ticker: str, base_year: int, hold_years: int) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{base}/api/backtest/{ticker}",
            params={"base_year": base_year, "hold_years": hold_years},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()


async def _fetch_datalab_trend(name: str, base_year: int) -> list[dict]:
    """네이버 데이터랩 월간 검색량 (base_year ~ 현재). 키 없거나 실패 시 빈 리스트."""
    cid = os.getenv("NAVER_CLIENT_ID", "")
    csec = os.getenv("NAVER_CLIENT_SECRET", "")
    if not (cid and csec):
        logger.warning("NAVER_CLIENT_ID/SECRET 미설정 — 검색량 트렌드 생략")
        return []
    body = {
        "startDate": f"{base_year}-01-01",
        "endDate": datetime.date.today().isoformat(),
        "timeUnit": "month",
        "keywordGroups": [{"groupName": name, "keywords": [name]}],
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://openapi.naver.com/v1/datalab/search",
                headers={
                    "X-Naver-Client-Id": cid,
                    "X-Naver-Client-Secret": csec,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=20.0,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0].get("data", []) if results else []
    except Exception as e:
        logger.warning(f"데이터랩 조회 실패: {e}")
        return []


def _trend_narrative(trend: list[dict], base_year: int) -> str:
    """검색량 데이터 → '대중은 늦는다' 네러티브 수치 추출"""
    if not trend:
        return ""
    base_ratios = [d["ratio"] for d in trend if d["period"].startswith(str(base_year))]
    peak = max(trend, key=lambda d: d["ratio"])
    base_avg = sum(base_ratios) / len(base_ratios) if base_ratios else 0
    peak_month = peak["period"][:7].replace("-", "년 ") + "월"
    return (
        f"{base_year}년 네이버 검색 관심도 평균 {base_avg:.1f} (최고점 100 기준) — 대중의 관심 거의 없음.\n"
        f"검색량 최고점: {peak_month} (관심도 100). 즉, 대중의 관심이 폭발했을 때는 이미 주가 급등 이후였음."
    )


def make_trend_chart(name: str, trend: list[dict], base_year: int, output_path: str) -> bool:
    """검색량 추이 차트 → 5초 세로(1080x1920) mp4. 실패 시 False."""
    if not trend:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        # 한글 폰트 (Windows: 맑은고딕)
        for f in ["Malgun Gothic", "NanumGothic", "AppleGothic"]:
            if any(f.lower() in ft.name.lower() for ft in font_manager.fontManager.ttflist):
                plt.rcParams["font.family"] = f
                break

        periods = [d["period"][:7] for d in trend]
        ratios = [d["ratio"] for d in trend]

        fig, ax = plt.subplots(figsize=(10.8, 9.6), dpi=100)  # 1080x960 (상하 여백은 영상에서)
        fig.patch.set_facecolor("#0a0f1e")
        ax.set_facecolor("#0a0f1e")
        ax.plot(periods, ratios, color="#10b981", linewidth=3)
        ax.fill_between(periods, ratios, color="#10b981", alpha=0.15)

        peak_i = ratios.index(max(ratios))
        ax.annotate("대중 관심 폭발\n= 이미 고점", xy=(peak_i, ratios[peak_i]),
                    xytext=(max(0, peak_i - len(periods) // 3), max(ratios) * 0.75),
                    color="#ef4444", fontsize=18, fontweight="bold",
                    arrowprops=dict(color="#ef4444", arrowstyle="->"))
        ax.annotate(f"{base_year}년: 아무도 안 봄", xy=(0, ratios[0]),
                    xytext=(1, max(ratios) * 0.35),
                    color="#f59e0b", fontsize=18, fontweight="bold",
                    arrowprops=dict(color="#f59e0b", arrowstyle="->"))

        ax.set_title(f"{name} — 네이버 검색 관심도", color="white", fontsize=22, fontweight="bold", pad=16)
        step = max(1, len(periods) // 6)
        ax.set_xticks(range(0, len(periods), step))
        ax.set_xticklabels(periods[::step], color="#9ca3af", fontsize=11)
        ax.tick_params(colors="#9ca3af")
        for spine in ax.spines.values():
            spine.set_color("#374151")

        png_path = output_path.replace(".mp4", ".png")
        fig.savefig(png_path, facecolor="#0a0f1e", bbox_inches="tight")
        plt.close(fig)

        # PNG → 5초 세로 mp4 (검정 캔버스 1080x1920 중앙 배치)
        from moviepy.editor import ImageClip, ColorClip, CompositeVideoClip
        img = ImageClip(png_path).resize(width=1040).set_duration(5)
        canvas = ColorClip(size=(1080, 1920), color=(10, 15, 30)).set_duration(5)
        clip = CompositeVideoClip([canvas, img.set_position("center")])
        clip.write_videofile(output_path, fps=24, codec="libx264", audio=False,
                             preset="ultrafast", logger=None)
        return os.path.exists(output_path)
    except Exception as e:
        logger.warning(f"트렌드 차트 생성 실패: {e}")
        return False


async def pick_history_topic(exclude_topics: list[str] | None = None,
                             chart_dir: str = ".",
                             render_clips: bool = True) -> tuple[str, str, dict]:
    """
    큐레이션 목록에서 히스토리에 없는 종목을 골라 (주제, 문맥, asset_clips) 반환.
    asset_clips: {'chart': 검색량차트mp4, 'card': 분석카드mp4} — 생성된 것만 포함.
    영상의 source_type==키 인 씬에 각각 삽입된다.
    render_clips=False 면 mp4 렌더링을 모두 건너뛴다(대본 프리뷰용 — 빠름).
    """
    exclude = exclude_topics or []
    base = os.getenv("TENBAGGER_API_BASE", "http://localhost:8000").rstrip("/")
    curated = _load_curated_list()

    target = None
    for item in curated:
        if not any(item["name"] in t for t in exclude):
            target = item
            break
    if target is None:
        raise ValueError("미사용 과거 텐배거 종목 없음 — data/tenbagger_history.json에 추가 필요")

    name, ticker = target["name"], target["ticker"]
    base_year, hold_years = target["base_year"], target.get("hold_years", 5)

    bt = await _fetch_backtest(base, ticker, base_year, hold_years)
    score = bt.get("score_at_base_year", {}) or {}

    trend = await _fetch_datalab_trend(name, base_year)
    trend_block = _trend_narrative(trend, base_year)

    asset_clips = {}
    chart_path = ""
    if render_clips:
        os.makedirs(chart_dir, exist_ok=True)
        if trend:
            candidate = os.path.join(chart_dir, f"trend_{ticker}.mp4")
            if make_trend_chart(name, trend, base_year, candidate):
                chart_path = candidate
                asset_clips["chart"] = chart_path
        # 당시 재무 스냅샷 + 이후 실제 수익률 분석 카드
        card_path = os.path.join(chart_dir, f"card_{ticker}.mp4")
        gs = score.get("growth_score")
        ret = bt.get("actual_return_pct")
        card_data = {
            "name": name,
            "subtitle": f"{base_year}년 재무 데이터 기준 분석",
            "grade": score.get("grade"),
            "total_score": score.get("total_score"),
            "metrics": ([("성장성 점수", f"{float(gs):.1f}/10", float(gs) / 10)]
                        if gs is not None else []),
            "highlight": (f"+{float(ret):,.0f}%" if ret is not None else None),
            "highlight_label": f"{hold_years}년 뒤 실제 주가",
        }
        if make_score_card(card_data, card_path):
            asset_clips["card"] = card_path

    topic = f"{base_year}년의 {name}, 데이터는 이미 알고 있었다"
    # 데이터 라벨에 '시스템 점수' 같은 내부 용어를 쓰지 않는다(GPT가 그대로 베껴 읽는 것 방지)
    lines = [
        f"종목명: {name} ({ticker})",
        f"[{base_year}년 당시 재무데이터만으로 분석한 결과]",
        f"재무 종합 평가: 10점 만점에 {_fmt(score.get('total_score'))}점 | 당시 분류 등급: {score.get('grade', 'N/A')}",
        f"성장성 평가: 10점 만점에 {_fmt(score.get('growth_score'))}점",
        f"{base_year}년 주가: {_fmt(bt.get('price_at_base_year'), '원', 0)}",
        f"{bt.get('end_year')}년 주가: {_fmt(bt.get('price_at_end_year'), '원', 0)}",
        f"실제 수익률 ({hold_years}년 보유 시): {_fmt(bt.get('actual_return_pct'), '%')}",
    ]
    if target.get("note"):
        lines.append(f"배경 메모: {target['note']}")
    if trend_block:
        lines.append(f"\n[대중 심리 — 네이버 검색량]\n{trend_block}")

    card_rule = (
        "4-1. 초반 Scene 하나는 반드시 source_type을 'card'로 지정하라 "
        "(텐배거 시스템이 당시 재무 데이터로 매긴 등급·점수·이후 수익률 카드가 화면에 뜬다). "
        "해당 narration은 '재무 데이터로만 분석했을 때 이런 결과였다'는 맥락으로 카드를 가리키듯 말하라.\n"
        if asset_clips.get("card") else ""
    )
    chart_rule = (
        "4-2. 중반 Scene 하나는 반드시 source_type을 'chart'로 지정하라 "
        "(검색량 추이 차트가 삽입됨). 해당 narration은 대중 관심과 주가 타이밍에 관한 내용일 것.\n"
        if asset_clips.get("chart") else ""
    )
    context = (
        "\n".join(lines) + "\n\n"
        "[대본 작성 가이드 — 반전 서사 우선, 수치는 결정적 순간에만]\n"
        f"1. 훅(첫 Scene): \"{base_year}년, 아무도 {name}을(를) 거들떠보지 않았습니다\" 처럼 "
        "과거의 무관심 → 반전 구조로 시작하라. 충격적 결과(몇 년 뒤 +몇 %)를 미리 살짝 흘려 호기심을 걸어도 좋다.\n"
        "2. '시스템 점수 X점' 같은 내부 용어를 그대로 쓰지 마라 — 시청자는 '시스템'이 뭔지 모른다. "
        "대신 '재무 데이터로만 분석했을 때', '당시 숫자들이 보내던 신호', '아무도 몰랐지만 숫자는 이미 답을 갖고 있었다' "
        "같이 누구나 이해할 수 있는 표현으로 치환하라.\n"
        "3. 관통하는 서사: '대중은 늘 늦는다. 검색량이 터졌을 땐 이미 고점. 하지만 데이터는 먼저 알고 있었다.' "
        "이 한 편의 이야기를 처음부터 끝까지 끌고 가라. 서사를 충분히 담도록 "
        "전체 65~80초 / 10~12개 Scene으로 구성하라(너무 짧게 끝내지 마라).\n"
        "4. 수치는 결정적 순간에만 — 실제 수익률, 검색량 고점 시기, 이 2~3개면 충분하다. "
        "나머지 Scene은 숫자 없이 서사·긴장·맥락으로 채워라.\n"
        f"{card_rule}{chart_rule}"
        "6. 후견지명처럼 보이지 않게: '당시 데이터만으로' 평가했다는 점을 반드시 1회 명시하라.\n"
        f"7. 마지막 Scene은 source_type을 반드시 'disclaimer'로 지정하고 narration에 다음 문구를 넣어라: \"{DISCLAIMER}\" "
        "(이 씬은 TTS 낭독 없이 영상 하단 자막으로만 표시된다.)"
    )
    logger.info(f"과거 텐배거 주제 선정: {topic} (수익률 {bt.get('actual_return_pct')}%)")
    return (topic, context, asset_clips)
