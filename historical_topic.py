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
import asyncio
import httpx

from tenbagger_topic import DISCLAIMER, _fmt
from tenbagger_card import make_score_card

logger = logging.getLogger(__name__)

HISTORY_LIST_FILE = os.path.join("data", "tenbagger_history.json")

# ── 사례 유형별 서사 프레임 ──────────────────────────────────
# 모든 사례는 공통 백본("대중은 늦는다, 숫자는 먼저 온다")을 쓰되,
# 결말 교훈과 카드 프레이밍은 종목 성격에 맞춰 분기한다.
# (성공담만 찍어내면 HMM·위메이드 같은 경계 사례에서 거짓 서사가 된다)
CASE_FRAMES = {
    "success": (
        "[사례 유형: 성공 — 데이터가 조용히 위대함을 가리켰고 끝까지 강했다]\n"
        "결말 교훈: '화제는 늦게 왔지만 숫자는 먼저 왔다. 그때 숫자를 봤다면 잡을 수 있었다.' "
        "긍정적·확신에 찬 톤으로 마무리하라. 카드는 시스템이 당시 높게 평가했음을 자랑스럽게 가리켜도 된다."
    ),
    "frenzy": (
        "[사례 유형: 광풍 — 텐배거는 됐지만 대중 관심의 정점이 곧 주가 고점이었고 이후 실적이 꺾였다]\n"
        "★중요: 이건 단순 성공담이 아니다. '검색량이 폭발한 그 순간이 가장 위험했다'는 양날의 검 서사로 써라. "
        "전반부: 2019년 무관심 속 데이터는 기회를 가리켰다. "
        "후반부 반전: 대중이 열광하며 검색량이 정점을 찍은 바로 그때가 고점이었고, 이후 실적/주가가 꺾였다. "
        "결말 교훈: '데이터로 들어가고, 군중이 가장 뜨거울 때 의심했어야 했다. 광풍에 올라타지 마라.' "
        "절대 '계속 좋았다'고 거짓으로 끝내지 마라 — 정점 이후 하락을 반드시 언급하라."
    ),
    "cyclical": (
        "[사례 유형: 사이클 — 업황 사이클이 만든 상승. 재무 데이터로도 타이밍 예측은 한계]\n"
        "정직한 톤으로 써라. '데이터는 바닥의 저평가를 가리켰지만, 이건 회사의 실력보다 업황 사이클이 만든 상승이었다.' "
        "결말 교훈: '사이클주는 데이터로도 타이밍이 어렵다. 그래서 더더욱 숫자 없이 분위기로 사면 위험하다.' "
        "시스템의 한계를 솔직히 인정하는 것이 오히려 신뢰를 준다."
    ),
    "caution": (
        "[사례 유형: 경계 — 네러티브(스토리)가 주도한 위험 사례. 화려한 테마 vs 빈약한 숫자]\n"
        "★중요: 이건 '데이터를 무시하면 어떻게 되는가'의 반면교사다. "
        "스토리(P2E·메타버스 등)는 화려했지만 그걸 받쳐줄 실적·현금흐름 숫자는 부족했고, 결국 급락했다는 구조로 써라. "
        "결말 교훈: '스토리는 귀를 홀리지만, 숫자는 거짓말을 안 한다. 숫자가 안 받쳐주는 테마는 결국 무너진다.' "
        "이 채널이 '숫자 기반'임을 각인시키는 가장 좋은 사례다."
    ),
}

# ── 퀄리티 기준선 예시 (한미반도체 v2) ──────────────────────────
# GPT에게 "이 정도 수준·구조로 써라"를 보여주는 few-shot. 다른 종목 사례이므로
# 구조(무관심→떡밥→DART신뢰도→겨울버티기→트리거공감→폭발→대중지각→교훈→CTA)만
# 참고하고, 내용·수치는 반드시 현재 주어진 종목의 실제 데이터로 채우게 한다.
_FEWSHOT_EXEMPLAR = (
    "[참고 예시 — '한미반도체' 사례. 톤·서사 구조만 참고하라. "
    "절대 이 회사 이름·수치를 복사하지 마라. 너는 위에 주어진 종목으로 써야 한다.]\n"
    "S1 무관심 훅: \"2019년, 한미반도체는 끝났다는 소리를 들었습니다.\"\n"
    "S5 반전 떡밥: \"그런데 5년 뒤, 영업이익이 18배가 됩니다.\"\n"
    "S6 [card·DART신뢰도]: \"당시 DART 공시 숫자만 봐도, 신호는 이미 거기 있었죠.\"\n"
    "S9 겨울 은유: \"죽은 게 아니라, 겨울을 버티고 있었던 거죠.\"\n"
    "S13~15 [핵심·트리거 공감]: \"근데 2019년, SK하이닉스는 이미 HBM 생산을 늘리고 있었어요.\" "
    "→ \"HBM이 늘면, 그걸 쌓는 장비도 필요하다.\" → \"그 장비 독점사가 한미반도체. 이 연결만 봤어도.\" "
    "(★ 이 부분이 시청자가 '아, 그때 이 연결만 봤으면' 하고 무릎 치게 만드는 가장 중요한 대목이다. "
    "주어진 트리거 신호로 반드시 이런 '봤어야 할 연결고리' 서사를 만들어라.)\n"
    "S20 [chart]: \"그제서야, 사람들이 검색하기 시작했습니다.\"\n"
    "S24 교훈: \"화제는 항상 늦게 옵니다. 숫자는, 먼저 오죠.\"\n"
    "S25 [CTA]: \"이런 과거 분석의 전체 기록은, 프로필 링크에서 공개 중입니다.\"\n"
)


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


async def _fetch_qualitative(base: str, ticker: str) -> str:
    """텐배거 백엔드 정성 분석 API 호출. 실패 시 빈 문자열."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{base}/api/v2/company/{ticker}/qualitative",
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                # 정성 분석 결과에서 네러티브에 쓸 핵심 필드 추출 (실제 API 스키마 기준)
                parts = []
                for key in ("business_model", "competitive_advantage", "future_outlook"):
                    val = data.get(key)
                    if val and isinstance(val, str) and len(val) > 10:
                        parts.append(val.strip())
                # 해자 요약 (moat_detail.summary)
                moat = data.get("moat_detail")
                if isinstance(moat, dict) and moat.get("summary"):
                    parts.append(str(moat["summary"]).strip())
                return " ".join(parts)[:800]
    except Exception as e:
        logger.warning(f"정성 분석 API 실패: {e}")
    return ""


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

    bt, trend, qualitative = await asyncio.gather(
        _fetch_backtest(base, ticker, base_year, hold_years),
        _fetch_datalab_trend(name, base_year),
        _fetch_qualitative(base, ticker),
    )
    score = bt.get("score_at_base_year", {}) or {}
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

    # 사례 유형 (성공/광풍/사이클/경계) — 결말 서사를 분기한다.
    # ★ Layer 1: 백테스트가 보유기간 재무 추세로 자동 판정한 case_type을 우선 사용한다.
    #   (사람이 손으로 라벨링하던 success/frenzy 오분류를 구조적으로 제거)
    #   단, 업종 판단이 필요한 cyclical/caution 수동 라벨은 존중한다.
    manual_case = target.get("case_type", "success")
    auto = (bt.get("case_analysis") or {})
    auto_case = auto.get("case_type")  # 'success'|'frenzy'|'unknown'|None
    if manual_case in ("cyclical", "caution"):
        case_type = manual_case
    elif auto_case in ("success", "frenzy"):
        case_type = auto_case
        if auto_case != manual_case:
            logger.info(f"[{name}] case_type 자동교정: 수동='{manual_case}' → 데이터판정='{auto_case}' ({auto.get('reason')})")
    else:
        case_type = manual_case
    case_frame = CASE_FRAMES.get(case_type, CASE_FRAMES["success"])
    topic_suffix = {
        "success": "데이터는 이미 알고 있었다",
        "frenzy": "모두가 환호할 때, 데이터는 식고 있었다",
        "cyclical": "사이클이 만든 10배, 데이터의 한계",
        "caution": "스토리는 화려했지만, 숫자는 비어 있었다",
    }.get(case_type, "데이터는 이미 알고 있었다")
    topic = f"{base_year}년의 {name}, {topic_suffix}"
    # 데이터 라벨에 '시스템 점수' 같은 내부 용어를 쓰지 않는다(GPT가 그대로 베껴 읽는 것 방지)
    lines = [
        f"종목명: {name} ({ticker})",
        f"[{base_year}년 당시 재무데이터만으로 분석한 결과]",
        f"재무 종합 평가: 10점 만점에 {_fmt(score.get('total_score'))}점 | 당시 분류 등급: {score.get('grade', 'N/A')}",
        f"성장성 평가: 10점 만점에 {_fmt(score.get('growth_score'))}점",
        f"{base_year}년 주가: {_fmt(bt.get('price_at_base_year'), '원', 0)}",
        f"{bt.get('end_year')}년 주가: {_fmt(bt.get('price_at_end_year'), '원', 0)}",
        f"실제 수익률 ({hold_years}년 보유 시): {_fmt(bt.get('actual_return_pct'), '%')}",
        f"\n{case_frame}",
    ]
    if target.get("note"):
        lines.append(f"배경 메모: {target['note']}")
    # ★ 실제 보유기간 재무 추세 (DART) — GPT가 인용할 ground-truth 수치. 환각 방지.
    trajectory = bt.get("financial_trajectory") or []
    if trajectory:
        lines.append(f"\n[{base_year}~{bt.get('end_year')}년 실제 재무 추세 — DART 공시, 이 수치만 인용하라]")
        for r in trajectory:
            rev = r.get("revenue")
            op = r.get("operating_profit")
            rev_s = f"매출 {rev/1e8:,.0f}억" if rev is not None else "매출 N/A"
            op_s = f"영업이익 {op/1e8:,.0f}억" if op is not None else "영업이익 N/A"
            lines.append(f"  {r.get('year')}년: {rev_s} · {op_s}")
        if auto.get("final_year_operating_loss"):
            lines.append(
                f"  ※ 최종 {bt.get('end_year')}년은 영업적자다. "
                "절대 '계속 좋았다'는 식의 거짓 성공담으로 끝내지 마라 — 정점 이후 둔화/적자를 반드시 다뤄라."
            )
        elif auto.get("revenue_drop_from_peak_pct") and auto["revenue_drop_from_peak_pct"] >= 30:
            lines.append(
                f"  ※ 최종 연도 매출이 정점 대비 {auto['revenue_drop_from_peak_pct']}% 하락했다. "
                "고점 이후 둔화를 반드시 언급하라."
            )
    # 공감 포인트: 당시 관찰 가능했던 트리거 신호들
    triggers = target.get("triggers") or []
    if triggers:
        lines.append(
            f"\n[{base_year}년 당시 존재했던 트리거 신호 — 알아봤다면 잡을 수 있었던 정보]"
        )
        for i, t in enumerate(triggers, 1):
            lines.append(f"  신호 {i}: {t}")
    if trend_block:
        lines.append(f"\n[대중 심리 — 네이버 검색량]\n{trend_block}")
    # 정성 분석 (해자·CEO·산업 구조)
    if qualitative:
        lines.append(f"\n[정성 분석 — 비즈니스 해자 및 산업 구조]\n{qualitative}")

    # 카드/차트 씬 지시는 '데이터 가용성'으로 판단한다(렌더 여부와 무관).
    # 프리뷰(render_clips=False)에서도 score/trend가 있으면 규칙을 넣어야
    # 미리보기 대본에 card/chart 씬이 포함되고, '이 대본 그대로 제작' 시 클립이 붙는다.
    card_available = bool(score) or bool(asset_clips.get("card"))
    chart_available = bool(trend) or bool(asset_clips.get("chart"))
    card_rule = (
        "4-1. 초반 Scene 하나는 반드시 source_type을 'card'로 지정하라 "
        "(텐배거 시스템이 당시 재무 데이터로 매긴 등급·점수·이후 수익률 카드가 화면에 뜬다). "
        "해당 narration은 '재무 데이터로만 분석했을 때 이런 결과였다'는 맥락으로 카드를 가리키듯 말하라.\n"
        if card_available else ""
    )
    chart_rule = (
        "4-2. 중반 Scene 하나는 반드시 source_type을 'chart'로 지정하라 "
        "(검색량 추이 차트가 삽입됨). 해당 narration은 대중 관심과 주가 타이밍에 관한 내용일 것.\n"
        if chart_available else ""
    )
    context = (
        "\n".join(lines) + "\n\n"
        "[대본 작성 가이드 — 반전 서사 우선, 수치는 결정적 순간에만]\n"
        f"1. 훅(첫 Scene): \"{base_year}년, 아무도 {name}을(를) 거들떠보지 않았습니다\" 처럼 "
        "과거의 무관심 → 반전 구조로 시작하라. 충격적 결과(몇 년 뒤 +몇 %)를 미리 살짝 흘려 호기심을 걸어도 좋다.\n"
        "2. '시스템 점수 X점' 같은 내부 용어를 그대로 쓰지 마라 — 시청자는 '시스템'이 뭔지 모른다. "
        "대신 '재무 데이터로만 분석했을 때', '당시 숫자들이 보내던 신호', '아무도 몰랐지만 숫자는 이미 답을 갖고 있었다' "
        "같이 누구나 이해할 수 있는 표현으로 치환하라.\n"
        "3. 관통하는 서사: 위 '[사례 유형]' 블록에 적힌 결말 교훈을 반드시 따르라. "
        "공통 백본은 '대중은 늘 늦는다 — 검색량이 터졌을 땐 이미 고점'이되, "
        "성공 사례는 '숫자를 봤다면 잡았다', 광풍·경계 사례는 '광풍/스토리에 올라타면 물린다'로 끝을 다르게 가져가라. "
        "한 편의 이야기를 처음부터 끝까지 끌고 가되, 서사를 충분히 담도록 "
        "전체 약 85~95초 / 반드시 22~28개 Scene으로 구성하라(★12개 이하로 끝내면 실패다. "
        "참고 예시 대본처럼 한 호흡씩 잘게 쪼개 충분한 길이를 확보하라).\n"
        "4. 수치는 결정적 순간에만 — 실제 수익률, 검색량 고점 시기, 이 2~3개면 충분하다. "
        "나머지 Scene은 숫자 없이 서사·긴장·맥락으로 채워라.\n"
        f"{card_rule}{chart_rule}"
        "6. 후견지명처럼 보이지 않게: '당시 DART 공시 데이터만으로 분석했다'는 점을 반드시 1회 명시하라. "
        "DART = 금융감독원 전자공시 시스템. 이 수치는 법정공시 원본이다.\n"
        "6-1. ★정직성 규칙(생존자 편향 금지): 이 영상은 '이 종목은 이랬다'는 개별 사례 분석이다. "
        "'이 숫자만 보면 누구나 텐배거를 찾는다', '데이터만 보면 무조건 부자 된다'는 식의 과장된 일반화는 절대 금지. "
        "'이 사례에서는 당시 숫자에 이런 신호가 있었다' 정도로만 말하고, 같은 점수의 종목이 다 성공하는 건 아님을 전제로 하라. "
        "시스템을 '미래를 맞히는 점쟁이'로 포장하지 말고 '근거를 먼저 보는 도구'로만 그려라.\n"
        "7. 트리거 씬 (1개 이상): 제공된 '당시 트리거 신호' 중 가장 강력한 것을 하나 골라 "
        "'그때 이 신호를 봤다면 알아챌 수 있었다'는 공감 서사로 만들어라. "
        "시청자가 '아, 그걸 봤으면 됐겠구나' 하는 반응을 유도해야 한다.\n"
        "8. 결말 직전 CTA 씬 (1개): 마지막 교훈 씬 바로 앞에 "
        "source_type을 'pexels'로 지정하고 narration에 "
        "'이 분석의 전체 예측 기록은 공개 중입니다 — 프로필 링크에서 확인하세요'와 유사한 CTA를 넣어라. "
        "search는 'financial data dashboard screen glow'처럼 데이터 화면 이미지로 지정.\n"
        f"9. 마지막 Scene은 source_type을 반드시 'disclaimer'로 지정하고 narration에 다음 문구를 넣어라: \"{DISCLAIMER}\" "
        "(이 씬은 TTS 낭독 없이 영상 하단 자막으로만 표시된다.)\n\n"
        + _FEWSHOT_EXEMPLAR
    )
    logger.info(f"과거 텐배거 주제 선정: {topic} (수익률 {bt.get('actual_return_pct')}%)")
    return (topic, context, asset_clips)
