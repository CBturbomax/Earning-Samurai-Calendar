# Earning Samurai Calendar

> **이어받는 AI 는 먼저 `docs/AI_HANDOFF.md` 를 읽을 것** — 사용자와의 약속·작업 요령·최근 결정·남은 일. (GPT/Codex 는 `AGENTS.md` 로 들어온다.)

미국·일본·홍콩 상장사 실적발표 주간 캘린더. 정적 단일 HTML 하나가 산출물이고,
GitHub Pages로 그대로 서비스한다. 서버·빌드툴·의존 패키지가 없다.

<https://cbturbomax.github.io/Earning-Samurai-Calendar/>

## 작업 받는 규칙 (회원님 11/23)

- 오타 수정, 단순 조회, 지시가 명확한 작업은 질문 없이 바로 수행한다.
- 요청에 상위 목표(Why)·대상(Who)·완료 기준(Success)·제약 조건(Constraint) 중 하나라도 빠져 있어서 방향이 달라질 수 있다면,
  계획·코드 작성 전에 반드시 `interview-me` 스킬(`.claude/skills/interview-me`)을 먼저 실행한다.
- 인터뷰로 의도가 확정되면 목표(Why)와 하지 않을 것(Out of scope)을 `docs/intent/<주제>.md` 에 저장하고,
  새 세션에서 깔끔하게 구현할 수 있도록 그 자리에서 턴을 멈춘다.

## 파이프라인

```
scrape.py      ──> data/earnings.json     ┐   '언제 발표하나'
scrape_jp_tdnet.py ─> earnings_jp_past.json│   (일본은 소스가 셋이다)
                 └─> monthly_jp.json      │   '이번 달 얼마 팔았나'(월매출)
scrape_mon_jp.py  ─> monthly_nums_jp.json │   (+ pdftext.py + montable.py)
scrape_mon_web.py ─> monthly_web_jp.json  │   (流通ニュース · + monweb.py)
scrape_mon_ir.py  ─> monthly_ir_jp.json   │   (회사 IR 페이지 · + monir.py)
scrape_jp_sched.py ─> earnings_jp_sched.json│
scrape_us.py   ──> data/earnings_us.json  │
scrape_hk.py   ──> data/earnings_hk.json  │
scrape_caps.py ──> data/caps.json         ├─> build.py ──> index.html
scrape_fin.py  ──> data/financials.json   │   (+ markets.py
scrape_fin_intl.py ─> financials_intl.json│    + companies*.py
scrape_fin_jp.py  ─> financials_jp.json   │    + descriptions.py
scrape_fin_seg.py ─> segments.json        │    + translit.py)
scrape_seg_sec.py ─> segments_sec.json    │
scrape_seg_edgar.py ─> segments_edgar.json│
scrape_seg_hk.py  ─> segments_hk.json     │
scrape_seg_jp_edinetdb.py ─> segments_jp_hist.json │
scrape_pr_us.py   ─> briefs_us.json       │  '회사가 뭐라 했나' (+ briefs.py)
scrape_desc.py    ─> desc.json            ┘
(닛케이/나스닥/HKEXnews/야후/SEC/TDnet/stockanalysis)
                 '얼마나 큰가' '무엇을 발표했나' '무엇을 파는가'
```

표준 라이브러리만 쓴다. `pip install` 필요 없음.

**아무도 손으로 돌리지 않는다.** 워크플로 셋이 저장소에서 돌고 되커밋한다.
회원님은 로컬에서 아무것도 실행하지 않는다 — 코드를 고쳐 `main`에 올리면 끝이다.

## 설명서 쓰는 법 (회원님 11/24 — 토큰·시간을 아끼려고 줄였다)

- **이 파일은 세션마다 통째로 읽힌다. 짧게 둔다(200줄 안쪽).** 예전에는 1,645줄이었다.
- '왜 그렇게 했는가'(실측 숫자·실패 사례)는 아래 표의 **주제 파일**에 쓴다. 이 파일에는 꼭 필요하면 한 줄만 더한다.
- **코드를 건드리기 전에 그 주제 파일을 먼저 읽는다.** 같은 실수를 되풀이하지 않으려고 쌓은 기록이다.
- 되돌리기: 줄이기 전 원본이 `docs/backup/CLAUDE_2026-11-24.md` 에 있다. 회원님이 "설명서 원래대로 해줘"라고 하면
  그 파일을 `CLAUDE.md` 로 되돌리고 `docs/notes/` 의 옮긴 파일을 지운다(그 사이 더해진 줄은 버리지 말고 끝에 붙인다).
  다른 파일은 건드리지 않는다. 의도와 절차는 `docs/intent/claude-md-slim.md`.

## 찾아가기 — 무엇을 건드리기 전에 무엇을 읽나

| 건드리는 것 | 먼저 읽을 파일 |
|---|---|
| 어느 파일을 고쳐야 하는지 모를 때 | `docs/notes/where-to-edit.md` |
| 수집기·빌드를 손으로 돌려 볼 때(명령 모음) | `docs/notes/commands.md` |
| 세 시장 공통 규칙(행 형식 · 은행·리츠 제외 · 조용한 0건 · 수집 구멍 · 휴장) | `docs/notes/general.md` |
| 화면(`build.py` 의 TEMPLATE — CSS·필터·배지·피드·검색·YoY 축·폰 화면) | `docs/notes/screen.md` |
| 회사 한글명·음차(`translit.py` · `companies*.py`) | `docs/notes/names.md` |
| 미국 일정·8-K 속보·보도자료 숫자(`scrape_us.py` · `scrape_live_us.py` · `pr_table.py` · `scrape_us_fpi.py`) | `docs/notes/us.md` |
| 일본 일정·시각(`scrape.py` · `scrape_jp_sched.py` · `scrape_jp_tdnet.py` · `scrape_jp_time.py`) | `docs/notes/jp.md` |
| 홍콩 일정(`scrape_hk.py`) | `docs/notes/hk.md` |
| 월매출(`scrape_mon_*.py` · `montable.py` · `pdftext.py` · `monweb.py` · `monir.py`) | `docs/notes/monthly.md` |
| 실적 수치·분기 이름(`scrape_fin*.py` · `build.py` 의 unstack) | `docs/notes/financials.md` |
| 사업부·지역·수주·AI 판독(`scrape_seg_*.py` · `geojp.py` · `ai_seg.py` · `ai_fill.py`) | `docs/notes/segments.md` |
| 실적 브리핑(`briefs.py` · `scrape_pr_us.py` · `briefBlock`) | `docs/notes/briefs.md` |
| GitHub Actions 워크플로(`.github/workflows/` — 주기·충돌·이어달리기·푸시·담당 파일 표) | `docs/notes/workflows.md` |
| 실시간 서버·텔레그램(`realtime/` — 지연 원인·보낼 회사 고르기·가이던스) | `docs/notes/realtime.md` |

## 늘 지키는 것 (까닭은 주제 파일에)

- **없는 숫자·시각·분기를 지어 넣지 않는다.** 틀린 값은 빈 값보다 나쁘다. 오검출 하나가 놓침 하나보다 나쁘다.
- **조용한 0건을 만들지 않는다.** 수집기의 검증을 끄거나 약하게 만들지 않는다. 막힌 것 같으면 먼저 우리 쪽 눈을 의심한다.
- 규칙을 넓히기 전후로 **실제 데이터에 돌려** 새로 걸리는 것을 눈으로 본다.
- `index.html` 은 직접 고치지 않는다 — `build.py` 가 덮어쓴다. 표준 라이브러리만 쓴다.
- 종목 열쇠는 `시장:코드` 다. 통화를 환산하지 않는다. 연간 막대는 그리지 않는다(분기만).
- **은행·리츠는 세 시장 공통으로 싣지 않는다**(`markets.excluded`). 텔레그램은 더 거른다 — 금융·화학·철강·캐는 회사·
  미국 건설·신약 바이오·코인 관련·시총 1,000억 원 미만(`realtime/app.py` 의 `tg_pick`, 목록은 `docs/notes/realtime.md`).
- **수집 워크플로는 main 에서만 돈다.** 한 파일의 주인은 워크플로 하나다(담당 표는 `docs/notes/workflows.md`).
  되커밋에 `git pull --rebase` 를 쓰지 않는다. 푸시 앞에 오래 걸리는 일을 두지 않는다.
- **Railway 는 `railway` 가지를 본다 — main 이 아니다.** 서버 코드(`realtime/` 와 railway.json 의 watchPatterns)를 고치면
  main 에 올린 뒤 그 가지에도 올린다(가지가 main 과 갈라져 있어 main 을 합치는 커밋으로 민다). 고친 것은 모아서 한 번에.
- 알림이 늦게 왔으면 서버 `/api/log` 의 「원문 0」·「숫자 대기」·「AI 숫자 못 씀」부터 본다.

## 배포

`main`에 푸시하면 GitHub Pages가 자동으로 다시 배포한다.
`index.html`을 커밋에 포함시켜야 사이트에 반영된다.
