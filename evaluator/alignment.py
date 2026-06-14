import os
import asyncio
import httpx
import logging
import re

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


async def _gpt_mini(prompt: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=20.0)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def evaluate_scene(scene: dict) -> int:
    prompt = f"""
narration: {scene.get('narration', '')}
search: {scene.get('search', '')}

Evaluate how well the search visually represents narration.
Score 0~100 only.
"""
    try:
        content = await _gpt_mini(prompt)
        score = int("".join(filter(str.isdigit, content))[:3])
        return min(score, 100)
    except Exception as e:
        logger.error(f"Scene 평가 에러: {e}")
        return 0


async def evaluate_script(script: list) -> dict:
    tasks = [evaluate_scene(s) for s in script]
    scores = await asyncio.gather(*tasks)

    if not scores:
        return {"scene_scores": [], "final_score": 0, "pass": False}

    final_score = sum(scores) / len(scores)
    return {"scene_scores": scores, "final_score": final_score, "pass": final_score >= 70}


async def evaluate_history_quality(scenes: list, topic: str = "") -> dict:
    """
    history 모드 대본 품질 평가 (B-roll 정합성과 별개).
    아래 5개 항목을 채점해 0~100 점수를 반환한다.

    반환:
      {
        "story_arc": int,      # 반전 서사 강도 (0~30)
        "no_jargon": int,      # 내부 용어 없음 (0~20): '시스템 점수', '텐배거 점수' 미사용
        "hook_strength": int,  # 첫 씬 훅 강도 (0~20)
        "data_integration": int, # 수치가 자연스럽게 녹아드는가 (0~15)
        "conclusion_clarity": int, # '대중은 늦는다' 결론 (0~15)
        "total": int,          # 합계 0~100
        "feedback": str,       # 다음 생성에 쓸 개선 지시
      }
    """
    narrations = " / ".join(s.get("narration", "") for s in scenes if s.get("narration"))
    scene_count = len(scenes)
    # 면책 씬은 실제 낭독되지 않으므로 글자 수 집계에서 제외
    char_count = sum(len(s.get("narration", "")) for s in scenes
                     if s.get("source_type") != "disclaimer")
    has_disclaimer = any(s.get("source_type") == "disclaimer" for s in scenes)

    prompt = f"""
다음은 한국 주식 유튜브 쇼츠 '과거 텐배거 해부' 대본이다.
주제: {topic}
씬 수: {scene_count}, 총 낭독 글자 수: {char_count}
면책 씬 존재: {has_disclaimer}

모든 낭독 (씬 순서대로):
{narrations}

[채점 기준 — 각 항목의 점수를 정수로만 응답]

1. story_arc (0~30): 무관심→반전→폭등→'대중은 늦는다'의 서사 완성도
   - 30: 처음에 당시 무관심을 묘사하고, 중반에 반전 계기(AI/HBM 등)를 설명하며, 끝에 교훈 메시지로 마무리
   - 15: 서사 있지만 어느 한 단계가 약함
   - 0: 단순 수치 나열

2. no_jargon (0~20): '시스템 점수', '텐배거 점수', '시스템이 알고 있었다' 같은 내부 용어가 낭독에 없으면 20점
   - 20: 내부 용어 0개
   - 10: 1개 있음
   - 0: 2개 이상

3. hook_strength (0~20): 첫 씬 낭독이 "아, 이 얘기 들어야겠다"를 만드는가
   - 20: 구체적 수치나 충격적 상황을 2초 안에 제시
   - 10: 미약하게 흥미 유발
   - 0: 일반적인 소개

4. data_integration (0~15): 수치(매출 몇 배, 영업이익 몇 배, 수익률 등)가 서사 흐름에 자연스럽게 녹아드는가
   - 15: 수치가 감정적 반전의 증거로 작동 ("그런데 5년 뒤, 영업이익이 18배가 됩니다")
   - 7: 수치는 있지만 나열식
   - 0: 수치 없거나 맥락 없이 삽입

5. conclusion_clarity (0~15): 마지막 메시지가 '숫자/데이터'에 관한 명확한 교훈인가
   (성공 사례: '숫자를 봤다면 잡았다' / 광풍·경계 사례: '광풍·스토리에 올라타면 물린다, 숫자가 먼저다'
    — 둘 중 어느 쪽이든 '숫자 기반' 교훈이면 인정)
   - 15: 명확한 교훈 + 다음 영상도 보고 싶은 여운
   - 7: 결론은 있지만 약함
   - 0: 없음

반드시 아래 JSON 형식으로만 응답하라. 설명 텍스트 금지.
{{
  "story_arc": <int>,
  "no_jargon": <int>,
  "hook_strength": <int>,
  "data_integration": <int>,
  "conclusion_clarity": <int>,
  "feedback": "<다음 생성 시 반드시 고쳐야 할 것 1~2줄 한국어>"
}}
"""
    try:
        raw = await _gpt_mini(prompt)
        # JSON 블록 추출
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("JSON 미반환")
        data = __import__("json").loads(m.group())
        total = (
            data.get("story_arc", 0)
            + data.get("no_jargon", 0)
            + data.get("hook_strength", 0)
            + data.get("data_integration", 0)
            + data.get("conclusion_clarity", 0)
        )
        data["total"] = total
        # 분량(씬 수·글자 수) / 면책 씬 패널티 — 90초 기준선(22~28씬, 440~560자) 미달 시 감점
        if scene_count < 18:
            data["total"] = max(0, data["total"] - 25)
            data["feedback"] = (
                f"(분량 부족: {scene_count}씬 — 22~28씬으로 늘려라) " + data.get("feedback", "")
            )
        elif char_count < 380:
            data["total"] = max(0, data["total"] - 15)
            data["feedback"] = (
                f"(낭독 분량 부족: {char_count}자 — 440자 이상으로) " + data.get("feedback", "")
            )
        if not has_disclaimer:
            data["total"] = max(0, data["total"] - 10)
        return data
    except Exception as e:
        logger.error(f"history 품질 평가 실패: {e}")
        return {
            "story_arc": 0, "no_jargon": 0, "hook_strength": 0,
            "data_integration": 0, "conclusion_clarity": 0,
            "total": 0, "feedback": "평가 실패 — 재생성 권장",
        }


# 둔화/하락을 나타내는 표현 — 팩트체크 게이트가 대본에서 찾는 키워드
_DOWNTURN_WORDS = (
    "적자", "하락", "무너", "꺾", "반토막", "고점", "폭락", "식", "둔화",
    "물렸", "물린", "떨어", "추락", "거품", "정점", "꼭대기", "위험",
)


def factcheck_history_script(scenes: list, context_data: str) -> dict:
    """Layer 2 팩트체크 게이트 — 규칙 기반(LLM 호출 없음, 빠름·결정적).

    context_data에 '최종 연도 영업적자/정점 대비 하락' ground-truth 요구가 들어있는데
    대본이 그 둔화를 한 번도 언급하지 않으면 '거짓 성공담'으로 보고 위반 처리한다.

    반환: {ok: bool, violations: [str], fix_instruction: str}
    """
    ctx = context_data or ""
    requires_downturn = ("영업적자" in ctx) or ("정점 대비" in ctx and "하락" in ctx)
    if not requires_downturn:
        return {"ok": True, "violations": [], "fix_instruction": ""}

    narr = " ".join(
        s.get("narration", "") for s in scenes
        if s.get("source_type") != "disclaimer"
    )
    mentions_downturn = any(w in narr for w in _DOWNTURN_WORDS)
    if mentions_downturn:
        return {"ok": True, "violations": [], "fix_instruction": ""}

    return {
        "ok": False,
        "violations": ["최종 연도 실적 둔화(영업적자/정점 대비 하락)를 대본이 전혀 언급하지 않음 — 거짓 성공담 위험"],
        "fix_instruction": (
            "★팩트체크 실패: 이 종목은 정점 이후 실적이 꺾였다(영업적자 또는 매출 급감). "
            "그런데 대본이 폭락/둔화를 한 번도 다루지 않아 거짓 성공담이 됐다. "
            "반드시 후반부에 '정점이 곧 고점이었고 이후 실적/주가가 꺾였다'는 반전과, "
            "'광풍에 올라타면 물린다'는 경계 교훈을 넣어 다시 작성하라."
        ),
    }
