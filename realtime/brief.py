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
LIMIT = 24000                                      # 원문 글자 수 — 길면 느리고 비싸다

SYSTEM = """너는 실적 발표 자료를 읽고 '실적이 왜 이렇게 나왔는지'를 한국 투자자에게 쉬운 말로 짚어 주는 사람이다.

절대 규칙:
- 자료에 회사가 직접 쓴 이유만 쓴다. 추측·일반론·덕담 금지. 근거가 없으면 빈 배열.
- 숫자는 자료에 나온 것만. 직접 계산하지 않는다. 매출·이익 금액을 되읊지 않는다(차트에 있다).
- "매출이 늘어서 이익이 늘었다" 같은 동어반복, 회사의 다짐·방침은 이유가 아니다.
- 한 줄 40자 안팎, 쉬운 한국어. 한자어를 소리 나는 대로 옮기지 말고 한국에서 쓰는 말로
  (堅調→호조, 価格転嫁→가격 인상 반영, 円安→엔화 약세, 不採算→적자 사업).
- 숫자는 한국식으로 옮긴다: 76億４千５百万円 -> 76.5억 엔, 107,322,446천 위안 -> 1,073억 위안, $580.8M -> 5.8억 달러.
  일본어·한자 단위(億·千·百万円)를 남기지 않는다. 퍼센트는 소수 첫째 자리까지.
- 제품·지역·고객처럼 구체적인 것을 우선한다(예: "AI 서버용 수요 급증으로 데이터센터 매출 2배").

JSON 으로만 답한다: {"key": "자료에 적힌 이번 기간 핵심 숫자 한 줄 — 매출·영업이익(또는 순이익)과 전년 대비 %를 자료 표기 그대로 통화 단위 붙여(예: 매출 120.5억 위안(+35%) · 순이익 18.2억 위안(+52%)). 자료에 없으면 빈 문자열", "why": [최대 3줄 — 실적을 움직인 이유], "watch": [최대 2줄 — 일회성·환율·감익 부문·가이던스 하향 등 주의할 점], "guide": [가이던스·전망에 대한 회사 언급 최대 2줄(없으면 [])], "biz": "이 회사가 무엇을 파는지 쉬운 한국어 한 줄(25자 안팎, 예: 화장품·의약품용 펌프와 마개)", "segs": [자료의 사업부문(세그먼트)별 또는 제품·지역별 매출 표가 있으면 이번 기간 값을 표에서 그대로 — {"n": 부문 이름(쉬운 한국어), "v": 숫자(표에 적힌 그대로, 단위 변환 없이 — 분기 단독(3개월) 값이 표에 있으면 그것을, 없으면 누계를 쓰고 basis 에 그렇게 적는다), "y": 전년 같은 기간 대비 %(표의 전년 칸으로 계산하거나 적힌 값, 모르면 null), "p": 표에 적힌 전년 같은 기간 값(v 와 같은 단위·같은 길이의 기간, 표에 없으면 null)}. 합계·조정·소거 줄은 빼고 최대 8개. 표가 없으면 []], "unit": "segs 숫자의 단위(예: 백만 엔, 백만 달러, 천 위안)", "scale": segs 숫자에 곱하면 통화 1단위가 되는 수(백만이면 1000000, 천이면 1000, 그대로면 1), "basis": "segs 기간(예: 3개월, 9개월 누계, 반기)", "segq": [자료(특히 설명자료·투자자 발표자료)에 사업부별 **분기 단독(3개월)** 매출이 여러 분기 나란히 실린 표가 있으면 그 열을 전부 — {"end": "그 분기가 끝난 달 YYYY-MM", "vals": {부문 이름: 숫자(segs 와 같은 단위·scale)}}. 누계·연간 열은 넣지 않는다. 없으면 []], "fin": {"period": "이번 실적 기간(예: 2026년 3분기, 9개월 누계, 상반기)", "unit": "단위(예: 백만 위안)", "rev": [이번, 전년 같은 기간], "op": [이번, 전년] 영업이익(없으면 null), "ni": [이번, 전년] 지배주주 순이익(없으면 null)} — 자료의 손익 표에서 그대로, 없으면 null}

segs 는 표에 적힌 숫자만 옮긴다. 본문 문장 속 숫자를 끌어 모아 표를 지어내지 않는다."""


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
                  {"model": AN_MODEL, "max_tokens": 1200, "system": SYSTEM,
                   "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": AN_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                  timeout)
        return "".join(b.get("text", "") for b in d.get("content", []))
    if OR_KEY:
        d = _post("https://openrouter.ai/api/v1/chat/completions",
                  {"model": OR_MODEL, "temperature": 0.1, "max_tokens": 1200,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]},
                  {"Authorization": f"Bearer {OR_KEY}", "content-type": "application/json"}, timeout)
        return d["choices"][0]["message"]["content"]
    return ""


KANA = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")
HAN_ONLY = re.compile(r"[\u4e00-\u9fff]{2,}")


def translate_names(names, timeout=60):
    """부문 이름(일·중) -> 쉬운 한국어. 뜻으로 옮긴다(한자음으로 읽지 않는다). 실패하면 {}."""
    if not names or not enabled():
        return {}
    ask = ("다음 사업부문 이름(일본어·중국어·영어)을 한국 애널리스트가 쓰는 자연스러운 한국어로 뜻을 옮겨라. "
           "브랜드·고유명사는 한국에서 쓰는 표기로. 한자를 한국 한자음으로 읽지 말고, "
           "가타카나 외래어는 표준 한국어 외래어로(エナジー→에너지), 事業·セグメント 같은 꼬리는 빼거나 '사업'으로, 14자 안팎. "
           "JSON 객체 {원문: 한국어} 로만 답한다.\n" + json.dumps(names, ensure_ascii=False))
    try:
        out = _ask(ask, timeout)
        m = re.search(r"\{.*\}", out, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:                                       # noqa: BLE001
        LOG("이름 번역 실패", repr(e)[:200])
        return {}
    return {k: str(v).strip() for k, v in d.items() if k in names and v and not KANA.search(str(v))}


def translate_lines(lines, timeout=40):
    """일본어·한자가 섞인 문장 -> 한국어로만(뜻 그대로, 고유명사도 한국어 표기)."""
    try:
        out = _ask("다음 문장들을 뜻 그대로 쉬운 한국어로만 다시 써라. 일본어·한자를 남기지 말고 고유명사도 한국어로 적는다. "
                   "JSON 객체 {원문: 한국어} 로만 답한다.\n" + json.dumps(lines, ensure_ascii=False), timeout)
        m = re.search(r"\{.*\}", out, re.S)
        return {k: str(v) for k, v in (json.loads(m.group(0)) if m else {}).items()}
    except Exception:                                            # noqa: BLE001
        return {}


def enabled():
    return bool(CC_TOKEN or AN_KEY or OR_KEY)


def us_text(raw):
    """보도자료 -> 앞머리(제목·인용·하이라이트) + 사업부 표가 있는 자리. 부문 표는 대개 뒤쪽이다."""
    t = plain(raw)
    head = t[:10000]
    m = re.search(r"(?i)segment|by (product|region|geograph)|revenue by", t[6000:])
    if not m:
        return t[:LIMIT]
    i = 6000 + m.start()
    return head + "\n…\n" + t[max(10000, i - 500):max(10000, i - 500) + 14000]


def plain(raw):
    """HTML -> 글. 표가 길게 늘어진 보도자료라 앞쪽(제목·CEO 인용·하이라이트)만 쓴다."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


HK_SEC = re.compile(r"MANAGEMENT DISCUSSION|BUSINESS REVIEW|OPERATIONAL REVIEW|FINANCIAL REVIEW|"
                    r"管理層討論|業務回顧", re.I)


def hk_text(pdf_bytes):
    """홍콩 실적 PDF -> 글. poppler 의 pdftotext 가 우리 표준 라이브러리 판독기보다 훨씬 낫다
    (메이디그룹 PDF 가 우리 것으로는 깨진 기호 1,300자뿐이었다). 없으면 우리 것으로."""
    t = ""
    if shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", "-layout", "-l", "40", "-", "-"], input=pdf_bytes,
                               capture_output=True, timeout=30)
            t = r.stdout.decode("utf-8", "replace")
        except Exception as e:                                   # noqa: BLE001
            LOG("pdftotext 실패", repr(e)[:200])
    if len(t.strip()) < 500:
        import pdftext
        t = pdftext.extract_text(pdf_bytes, max_pages=25) or ""
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    m = HK_SEC.search(t, 3000)
    # 앞쪽(실적 요약 표·하이라이트) + 경영진 설명 절 — 숫자와 이유가 둘 다 들어가게.
    return (t[:9000] + "\n…\n" + t[m.start():m.start() + 14000]) if m and m.start() > 9000 else t


def why(name, text, timeout=85, names=None):
    """-> {"why": [...], "watch": [...], "guide": str} 또는 None."""
    text = (text or "").strip()
    if not enabled() or len(text) < 80:
        LOG("요약 건너뜀:", name, "원문", len(text))
        return None
    try:
        hint = ("\n\n이 회사 사업부 이름(우리 사이트 표기) — segs 의 n 은 같은 부문이면 **이 중 하나를 글자 그대로** 쓴다: "
                + json.dumps(names, ensure_ascii=False)) if names else ""
        out = _ask(f"회사: {name}{hint}\n\n자료 원문:\n{text[:LIMIT]}", timeout)
        m = re.search(r"\{.*\}", out, re.S)
        d = json.loads(m.group(0)) if m else {}
        if not d:
            LOG("요약 빈 응답:", name, repr(out[:300]))
    except Exception as e:                                       # noqa: BLE001
        LOG("요약 실패:", name, repr(e)[:300])
        return None
    clean = lambda xs: [str(x).strip() for x in (xs or []) if str(x).strip()]   # noqa: E731
    # 한국어만 — 일본어·한자가 남은 줄은 한 번 더 옮기게 한다(⚠️ 주의 줄에 일본어가 섞여 나갔다, 10/30).
    def ko(lines):
        bad = [x for x in lines if KANA.search(x) or HAN_ONLY.search(x)]
        if not bad:
            return lines
        fixed = translate_lines(bad)
        return [fixed.get(x, x) for x in lines if not (KANA.search(fixed.get(x, x)))]
    for k in ("why", "watch", "guide"):
        v = d.get(k)
        if isinstance(v, list):
            d[k] = ko([str(x) for x in v])
        elif isinstance(v, str) and v:
            d[k] = (ko([v]) or [""])[0]
    for k in ("key", "biz"):
        if isinstance(d.get(k), str) and (KANA.search(d[k]) or HAN_ONLY.search(d[k])):
            d[k] = (ko([d[k]]) or [""])[0]
    g = d.get("guide")
    res = {"why": clean(d.get("why"))[:3], "watch": clean(d.get("watch"))[:2],
           "guide": clean(g if isinstance(g, list) else [g] if g else [])[:2],
           "key": str(d.get("key") or "").strip()[:160], "biz": str(d.get("biz") or "").strip()[:60]}
    segs = []
    for x in d.get("segs") or []:
        try:
            v = float(str(x.get("v")).replace(",", "")) if x.get("v") is not None else None
            y = float(x["y"]) if x.get("y") not in (None, "") else None
            pv = float(str(x.get("p")).replace(",", "")) if x.get("p") not in (None, "") else None
        except (TypeError, ValueError, AttributeError):
            continue
        if x.get("n") and v is not None:
            segs.append({"n": str(x["n"])[:30], "v": v, "y": y, "p": pv})
    f = d.get("fin") if isinstance(d.get("fin"), dict) else None
    if f:
        def pair(v):
            try:
                a, b = v
                return [float(str(a).replace(",", "")) if a is not None else None,
                        float(str(b).replace(",", "")) if b is not None else None]
            except (TypeError, ValueError):
                return None
        fin = {k: pair(f.get(k)) for k in ("rev", "op", "ni")}
        if fin["rev"] and fin["rev"][0]:
            res["fin"] = dict(fin, period=str(f.get("period") or "")[:30], unit=str(f.get("unit") or "")[:20])
    segq = []
    for col in d.get("segq") or []:
        try:
            end = str(col.get("end") or "")[:7]
            vals = {str(k)[:30]: float(str(v).replace(",", "")) for k, v in (col.get("vals") or {}).items()
                    if v not in (None, "")}
        except (TypeError, ValueError, AttributeError):
            continue
        if re.match(r"\d{4}-\d{2}$", end) and len(vals) >= 2:
            segq.append({"end": end, "vals": vals})
    if segq:
        res["segq"] = segq[:16]
    if len(segs) >= 2:
        try:
            scale = float(d.get("scale") or 0)
        except (TypeError, ValueError):
            scale = 0
        res.update(segs=segs[:8], unit=str(d.get("unit") or "")[:20], basis=str(d.get("basis") or "")[:20])
    if segs or segq:
        try:
            scale = float(d.get("scale") or 0)
        except (TypeError, ValueError):
            scale = 0
        res["scale"] = scale if scale in (1, 1e3, 1e4, 1e6, 1e8, 1e9) else 0
    if not (res["why"] or res["watch"] or res["guide"] or res["key"]):
        LOG("요약 내용 없음:", name, "원문", len(text), repr(text[:120]))
        return None
    return res
