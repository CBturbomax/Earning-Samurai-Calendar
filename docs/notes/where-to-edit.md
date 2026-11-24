# 손대는 위치 — 무엇을 고치려면 어느 파일인가

> `CLAUDE.md` 에서 옮겨 온 기록이다(2026-11-24, 문장은 그대로). 이 주제의 코드를 건드리기 전에 읽는다.

## 손대는 위치

| 하고 싶은 것 | 고칠 파일 |
|---|---|
| 회사 한글명이 틀렸다 | `translit.py`의 `WORDS`(일본), `companies_hk.py`의 `NAMES`(홍콩) — ★까지 붙일 종목만 `companies*.py`의 `NOTABLE` |
| 주목종목을 넣고 뺀다 | 해당 시장 `companies*.py`의 `NOTABLE` |
| 테마 그룹을 바꾼다 | 해당 시장 `companies*.py`의 `GROUP_ORDER` |
| 화면·기능·CSS | `build.py`의 `TEMPLATE` (HTML/CSS/JS가 전부 여기 있다) |
| 종목 검색(맨 위) | `build.py`의 `FIND` / `fqSearch` / `fqOpen` — 아래 표의 `#q` 와 다른 것이다 |
| 규모 필터 기본값 | `build.py`의 `capDefault` — 지금은 1조원 이상으로 연다 |
| 시장 이름·색·휴장일 | `markets.py` |
| 업종·거래소·발표시각 한글 표기 | `markets.py`의 `SECTOR_KO` / `MARKET_KO` / `TIMING_KO` |
| 시장을 하나 더 붙인다 | `markets.py` 항목 추가 → `scrape_*.py` → `companies_*.py` → `build.py`의 `DICTS` |
| 규모 필터 눈금·환율 | `markets.py`의 `CAP_STEPS` / `USD_KRW` |
| 발표 시각을 옮기는 규칙 | `markets.py`의 `US_BMO`/`US_AMC`/`JP_TYPICAL`, `build.py`의 `to_kst` |
| 미국 실적 수치를 더 받는다 | `scrape_fin.py` — 매출 태그는 `REV_TAGS` |
| 일본·홍콩 실적 수치 | `scrape_fin_intl.py`, 일본 당일치는 `scrape_fin_jp.py` |
| 부문별 매출 | 미국 `scrape_seg_sec.py`(뼈대)·`scrape_seg_edgar.py`(최근 분기)·`scrape_seg_fpi.py`(외국 기업) · 일본 `scrape_fin_jp.py` · 홍콩 `scrape_seg_hk.py` |
| 미국 상장 **외국 기업**(10-Q 없음 — SE·NU·PBR…)의 옛 분기 총매출·사업부 | `ai_fpi.py`(6-K 보도자료 표, 검산은 코드가) → `data/fpi_pr.json` → `build.py` 의 `load_fpi_pr`/`load_fpi_seg` · 워크플로 `ai_fpi.yml` |
| 미국 **4분기(결산 분기)** 사업부 빈칸 | `build.py` 의 `seg_fy_fix`(연간 − 9개월 누계, 그 해 9개월 비율로 검산) |
| 일본 부문 **연간** 이력(수집만, 화면엔 안 냄) | `scrape_seg_jp_edinetdb.py` |
| **지역별 매출** · **수주(수주고·수주잔고·RPO)** | 미국 `scrape_seg_sec.py`(벌크의 지역 축 `geo` · `RPO_TAGS`)·`scrape_seg_edgar.py`(최근 분기) · 일본 `scrape_geo_jp.py` + 표 읽기 `geojp.py` → `data/geo_jp.json` · 화면 `build.py` 의 `load_geo`/`load_ord`/`pack_ord` → `D.geo`/`D.ord`, 그림은 사업부와 같은 `segChart` |
| 일본 **월매출(月次)** 공시 | 수집은 `scrape_jp_tdnet.py` 의 `is_monthly`/`monthly_period`, 화면은 `build.py` 의 `load_monthly`/`renderMonthly` |
| 월매출 **수치**(금액·전년비) | `scrape_mon_jp.py` — 글자 꺼내기는 `pdftext.py`, 표 읽기는 `montable.py`, 차트는 `build.py` 의 `mnChart` |
| 월매출 — 공시를 안 내는 회사 | `scrape_mon_web.py` — 표 읽기와 브랜드→코드 사전은 `monweb.py` 의 `BRANDS` |
| 월매출 — 회사 IR 페이지 | `scrape_mon_ir.py` — 표 읽기는 `monir.py`, 손으로 잇는 주소는 `monir.py` 의 `IR_SITES` |
| 월매출 — **화면 계열과 같은 줄 잇기**(11/23 근본 수정) | 표 읽기(`montable`)가 표의 전년비 줄을 **전부** `series` 로 내고(이온 자회사 표·PPIH 암호 PDF·패스트리 直営店 줄), 수집기(`scrape_mon_jp`·`scrape_mon_ir`)가 회사별로 쌓고, `build.py` 의 `apply_series_match` 가 **화면 이력과 석 달 넘게 0.15 안(두 달이면 0.05 안·유일)** 맞는 줄로 빈 달만 잇는다. 원장 메우기보다 먼저 돈다. 회사 IR 표·流通ニュース 기사의 달별 값(same/all)도 같은 잣대로 잇는다(시마무라 10월). 실시간 서버 월차 알림(`mon_series_pick`)도 같은 잣대. **막힌 사이트를 다른 IP 로 돌아 받지 않는다**(무인양품 IR 429 — 중계를 만들다 막혔다, 우회는 안 한다). 무인양품(7453)은 달마다 손으로 원장에 넣는다(`data/monthly_ledger_jp.json` 의 7453 — InvestingWiki read_url 로 IR 페이지 「全店+EC 売上高」, 다음 공시 12/2). 아사히(2502)는 회사 전체 월매출을 안 낸다(맥주류·음료·식품 부문별 전년비뿐). 회사 IR PDF 이름표의 결산기(「2027年8月期」)를 발표일로 쓰던 것도 오늘로 눌렀다(패스트리 9·10월이 미래 달로 버려졌다) |
| 월매출 — **빈 달 메우기**(InvestingWiki 月次 원장 스냅샷) | `data/monthly_ledger_jp.json` → `build.py` 의 `apply_ledger_fill`. 화면 기준과 같은 계열·겹치는 달 전부 일치일 때만 실렸고, 있는 값은 안 덮는다. 원장은 GitHub Actions 에서 못 부르므로(MCP) 손으로 다시 떠서 갱신한다 — **10/22 판에 멈춰 10월분이 통째로 비었었다(11/22 다시 뜸: 17곳 · 11/23 화면 계열과 대조해 다시 뜸: 10월 +45곳 — 패스트리·이온·PPIH·모노타로·ABC마트·J프론트…). 달이 바뀌면 다시 뜰 것** — 대조용 화면 계열은 `MON_DUMP=파일 python build.py` 로 뽑는다 |
| 일본 **분기 실적이 통째로 끊긴 회사**(만다라케 2652 — stockanalysis 가 2006~2010 만 줬다) | `data/financials_jp_wiki.json`(InvestingWiki jp_financials 스냅샷, 손으로 뜬다 — MCP 라 Actions 에서 못 부름) → `build.py` 의 `jp_wiki_fin`. rank 가 가장 낮아 빈 분기만 메우고, 가진 줄이 그 앞 해에 끝났으면 옛 줄을 버린다. 결산기를 바꾼 해는 뺀다. 11/23 판 16종목 |
| 부문 이름 한글 표기 | `markets.py`의 `SEG_KO_FULL`/`SEG_KO_EN`/`SEG_KO_CJK` — 옮기기는 `build.py`의 `seg_ko` |
| 부문을 어느 축으로 가를까 | `scrape_seg_sec.py`의 `axis_rank` / `AXIS_KO` |
| 회사 사업 설명(한국어) | `descriptions.py`의 `DESC_KO` — 원문 수집은 `scrape_desc.py` |
| 실적 브리핑(숫자 요약·문구) | `build.py`의 `briefBlock` — 한국어 코멘트는 `briefs.py`, 원문 수집은 `scrape_pr_us.py`(미국)·`scrape_fin_jp.py`의 `fcst`(일본 가이던스) |
| 차트 모양·통화 표기 | `build.py`의 `finChart` / `segChart` / `CUR` |
| 상세창 **연간 표**(컨센서스·PER·PBR·PSR) | 수집 `scrape_fcst.py` → `data/forecast.json`, 싣기 `build.py` 의 `pack_fcst`, 그리기 `fcstTable`. 무료 범위: 직전년도 실적·당해 매출/영업이익/순이익·차기 매출/EPS. 차기·차차기 매출·영업이익·순이익은 **Business Insider** 종목 페이지 Estimates 표(`bi` 주소, `by` 표 — 미국은 티커, 일본·홍콩은 영문 이름 검색 + ISIN 국가, 당해/차기 매출이 stockanalysis 와 ±12% 안일 때만 채택, 매출이 비면 순이익·EPS 로 대조). 검색어는 여러 꼴(전체·군더더기 뗀·앞 두 낱말·첫 낱말)로 넣고 이름 낱말 겹침으로 고른다. 미국도 티커 주소가 없으면 이름 검색. **소형주까지 전 종목**(MIN_CAP 0, 1만여)이라 제 워크플로 `fcst.yml`(25분·6갈래 병렬·이어달리기)이 돈다. 재무 통화가 주가 통화와 다르면(위안 보고 홍콩주) caps.json 환율로 옮긴다. BI 가 없으면 미국은 나스닥 연간 EPS(`ny`)로 순이익만. MarketScreener·finanzen.net 403, IFIS 503(2026-10 실측). 결산 끝났는데 발표 전인 해(소스가 est=0 으로 줌)는 숫자가 천 단위로 안 떨어지면 추정으로 돌린다(`pack_fcst`) |

`index.html`은 **직접 고치지 않는다.** `build.py`가 덮어쓴다.
