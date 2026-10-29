"""회사가 실적을 내며 한 설명 -> '왜 이렇게 나왔나' 쉬운 한국어 몇 줄.

회원님(10/29): "숫자는 차트로 판단할 거고, 글자로는 왜 잘됐는지 회사가 설명하는 것들을
가독성 좋게 쉬운 말로 딱 간결하게."

원문은 이미 받는 자리에서 꺼낸다 — 미국 8-K EX-99 보도자료, 일본 결산단신 첨부의
経営成績に関する説明(scrape_fin_jp.read_narrative), 홍콩 실적 공시 PDF(pdftext).
옮기는 것은 LLM 이다. 키는 Railway 변수로만 받고, 먼저 있는 것을 쓴다:
  CLAUDE_CODE_OAUTH_TOKEN (회원님 Claude Max — `claude setup-token`, Claude Code 를 불러 쓴다)
  ANTHROPIC_API_KEY · OPENROUTER_API_KEY (기존 봇이 쓰던 것).
키가 없거나 실패하면 None — 알림은 설명 없이 그대로 나간다(늦추지 않는다).
"""
import html
import json
import os
import re
import shutil
import subprocess
import urllib.request

OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
AN_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CC_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
CC_MODEL = os.environ.get("CLAUDE_MODEL", "haiku")      # Max 안에서 — 빠른 것. sonnet 으로 바꿔도 된다
OR_MODEL = os.environ.get("LLM_MODEL", "google/gemini-3-flash-preview")
AN_MODEL = os.environ.get("LLM_MODEL_ANTHROPIC", "claude-haiku-5-5")
LOG = print
LIMIT = 9000                                       # 원문 글자 수 — 길면 느리고 비싸다

SYSTEM = """너는 실적 발표 자료를 읽고 '실적이 왜 이렇게 나왔는지'를 한국 투자자에게 쉬운 말로 짚어 주는 사람이다.

절대 규칙:
- 자료에 회사가 직접 쓴 이유만 쓴다. 추측·일반론·덕담 금지. 근거가 없으면 빈 배열.
- 숫자는 자료에 나온 것만. 직접 계산하지 않는다. 매출·이익 금액을 되읊지 않는다(차트에 있다).
- "매출이 늘어서 이익이 늘었다" 같은 동어반복, 회사의 다짐·방침은 이유가 아니다.
- 한 줄 40자 안팎, 쉬운 한국어. 한자어를 소리 나는 대로 옮기지 말고 한국에서 쓰는 말로
  (堅調→호조, 価格転嫁→가격 인상 반영, 円安→엔화 약세, 不採算→적자 사업).
- 제품·지역·고객처럼 구체적인 것을 우선한다(예: "AI 서버용 수요 급증으로 데이터센터 매출 2배").

JSON 으로만 답한다: {"why": [최대 3줄 — 실적을 움직인 이유], "watch": [최대 2줄 — 일회성·환율·감익 부문·가이던스 하향 등 주의할 점], "guide": "가이던스·전망에 대한 회사 언급 한 줄(없으면 빈 문자열)"}"""


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ask(user, timeout):
    if CC_TOKEN and shutil.which("claude"):
        r = subprocess.run(["claude", "-p", "--model", CC_MODEL, "--output-format", "text", "--max-turns", "1"],
                           input=SYSTEM + "\n\n" + user, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": CC_TOKEN})
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        LOG("Claude Code 실패:", r.returncode, (r.stderr or r.stdout)[-300:])
    if AN_KEY:
        d = _post("https://api.anthropic.com/v1/messages",
                  {"model": AN_MODEL, "max_tokens": 600, "system": SYSTEM,
                   "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": AN_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                  timeout)
        return "".join(b.get("text", "") for b in d.get("content", []))
    if OR_KEY:
        d = _post("https://openrouter.ai/api/v1/chat/completions",
                  {"model": OR_MODEL, "temperature": 0.1, "max_tokens": 600,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]},
                  {"Authorization": f"Bearer {OR_KEY}", "content-type": "application/json"}, timeout)
        return d["choices"][0]["message"]["content"]
    return ""


def enabled():
    return bool(CC_TOKEN or AN_KEY or OR_KEY)


def plain(raw):
    """HTML -> 글. 표가 길게 늘어진 보도자료라 앞쪽(제목·CEO 인용·하이라이트)만 쓴다."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


HK_SEC = re.compile(r"MANAGEMENT DISCUSSION|BUSINESS REVIEW|OPERATIONAL REVIEW|FINANCIAL REVIEW|"
                    r"管理層討論|業務回顧", re.I)


def hk_text(pdf_bytes):
    import pdftext
    t = re.sub(r"\s+", " ", pdftext.extract_text(pdf_bytes, max_pages=25) or "")
    m = HK_SEC.search(t)
    return t[m.start():] if m else t


def why(name, text, timeout=40):
    """-> {"why": [...], "watch": [...], "guide": str} 또는 None."""
    text = (text or "").strip()
    if not enabled() or len(text) < 80:
        return None
    try:
        out = _ask(f"회사: {name}\n\n자료 원문:\n{text[:LIMIT]}", timeout)
        m = re.search(r"\{.*\}", out, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:                                       # noqa: BLE001
        LOG("요약 실패:", name, repr(e)[:300])
        return None
    clean = lambda xs: [str(x).strip() for x in (xs or []) if str(x).strip()]   # noqa: E731
    res = {"why": clean(d.get("why"))[:3], "watch": clean(d.get("watch"))[:2],
           "guide": str(d.get("guide") or "").strip()}
    return res if res["why"] or res["watch"] or res["guide"] else None
