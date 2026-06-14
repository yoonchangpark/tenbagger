"""
텐배거 분석 카드 클립 생성기
종목의 등급·종합점수·핵심 재무지표를 세로(1080x1920) 인포그래픽으로 렌더 →
5초짜리 mp4 B-roll 클립으로 만들어 영상에 삽입한다.
'단순 점수 텍스트' 대신 시스템 분석 결과를 시각적으로 보여줘 신뢰도 + 브랜딩 효과.

영상 합성 시 하단(y>1000)에 딤박스/자막이 덮이므로, 핵심 내용은 상단~중단(y 60~960)에 배치한다.
"""
import os
import logging

logger = logging.getLogger(__name__)

# 텐배거 등급별 색상 (RGB)
GRADE_COLORS = {
    "TENBAGGER": (245, 197, 24),    # 골드
    "COMPOUNDER": (16, 185, 129),   # 에메랄드
    "WATCHLIST": (59, 130, 246),    # 블루
    "AVOID": (148, 163, 184),       # 그레이
}
ACCENT = (16, 185, 129)   # 텐배거 시그니처 에메랄드
BG = (10, 15, 30)         # 영상 톤과 동일한 다크 네이비


def _load_font(size, bold=True):
    from PIL import ImageFont
    for name in (["malgunbd.ttf", "malgun.ttf"] if bold else ["malgun.ttf", "malgunbd.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except IOError:
            continue
    try:
        return ImageFont.truetype("NanumGothicBold.ttf", size)
    except IOError:
        return ImageFont.load_default()


def make_score_card(data: dict, output_path: str, duration: float = 5.0) -> bool:
    """
    data 키:
      name(필수), ticker, subtitle, grade, total_score(0~10),
      metrics: list[(label, value_str, ratio|None)]  # ratio 0~1 이면 미니바 표시
      highlight: str, highlight_label: str            # 예: ('+739%', '5년 실제 수익률')
    성공 시 True (output_path 에 mp4 생성).
    """
    try:
        from PIL import Image, ImageDraw
        import numpy as np

        W, H = 1080, 1920
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        def text_w(s, font):
            if hasattr(d, "textlength"):
                return d.textlength(s, font=font)
            return d.textsize(s, font=font)[0]

        # ── 상단 브랜드 바 ──────────────────────────────
        d.rectangle([(0, 0), (W, 8)], fill=ACCENT)
        brand_font = _load_font(40)
        d.text((60, 60), "텐배거 헌터", font=brand_font, fill=(255, 255, 255))
        tag_font = _load_font(26, bold=False)
        d.text((62, 118), "재무 데이터 기반 텐배거 발굴 시스템", font=tag_font, fill=(148, 163, 184))
        d.line([(60, 175), (W - 60, 175)], fill=(55, 65, 81), width=2)

        # ── 종목명 + 서브타이틀 ─────────────────────────
        y = 215
        name = str(data.get("name", ""))
        name_font = _load_font(96)
        if text_w(name, name_font) > W - 120:
            name_font = _load_font(76)
        d.text((60, y), name, font=name_font, fill=(255, 255, 255))
        y += 120
        sub = data.get("subtitle") or (f"종목코드 {data['ticker']}" if data.get("ticker") else "")
        if sub:
            d.text((62, y), str(sub), font=_load_font(34, bold=False), fill=(148, 163, 184))
            y += 60

        # ── 등급 배지 + 종합점수 ────────────────────────
        y += 25
        grade = str(data.get("grade", "") or "").upper()
        if grade:
            gcolor = GRADE_COLORS.get(grade, (148, 163, 184))
            gfont = _load_font(46)
            pad = 30
            bw = text_w(grade, gfont) + pad * 2
            d.rounded_rectangle([(60, y), (60 + bw, y + 78)], radius=16, fill=gcolor)
            d.text((60 + pad, y + 14), grade, font=gfont, fill=(10, 15, 30))

        ts = data.get("total_score")
        if ts is not None:
            sfont = _load_font(72)
            lfont = _load_font(34, bold=False)
            score_str = f"{float(ts):.1f}"
            sw = text_w(score_str, sfont)
            unit_w = text_w(" / 10", lfont)
            x0 = W - 60 - sw - unit_w
            d.text((x0, y - 4), score_str, font=sfont, fill=ACCENT)
            d.text((x0 + sw, y + 36), " / 10", font=lfont, fill=(148, 163, 184))
            d.text((W - 60 - text_w("종합 점수", lfont), y - 44), "종합 점수",
                   font=lfont, fill=(148, 163, 184))
        y += 130

        # ── 핵심 지표 행 (라벨 | 값 | 미니바) ───────────
        metrics = data.get("metrics") or []
        row_font = _load_font(40, bold=False)
        val_font = _load_font(42)
        for label, value, ratio in metrics[:5]:
            d.text((60, y), str(label), font=row_font, fill=(203, 213, 225))
            vw = text_w(str(value), val_font)
            d.text((W - 60 - vw, y - 2), str(value), font=val_font, fill=(255, 255, 255))
            y += 64
            if ratio is not None:
                r = max(0.0, min(1.0, float(ratio)))
                d.rounded_rectangle([(60, y), (W - 60, y + 12)], radius=6, fill=(31, 41, 55))
                if r > 0:
                    d.rounded_rectangle([(60, y), (60 + int((W - 120) * r), y + 12)],
                                        radius=6, fill=ACCENT)
                y += 34
            else:
                y += 14

        # ── 하이라이트 (예: 실제 수익률) ────────────────
        hl = data.get("highlight")
        if hl:
            y = max(y, 820)
            d.rounded_rectangle([(60, y), (W - 60, y + 120)], radius=20,
                                fill=(16, 185, 129), outline=None)
            hlbl = data.get("highlight_label", "")
            d.text((90, y + 18), str(hlbl), font=_load_font(32, bold=False), fill=(10, 30, 24))
            d.text((90, y + 56), str(hl), font=_load_font(60), fill=(10, 15, 30))

        # ── 하단 푸터 (딤 영역, 은은하게) ───────────────
        d.text((60, H - 70), "tenbagger-production.up.railway.app · 투자 권유 아님",
               font=_load_font(26, bold=False), fill=(100, 116, 139))

        png_path = output_path.replace(".mp4", ".png")
        img.save(png_path)

        from moviepy.editor import ImageClip
        clip = ImageClip(np.array(img)).set_duration(duration)
        clip.write_videofile(output_path, fps=24, codec="libx264", audio=False,
                             preset="ultrafast", logger=None)
        return os.path.exists(output_path)
    except Exception as e:
        logger.warning(f"텐배거 분석 카드 생성 실패: {e}")
        return False
