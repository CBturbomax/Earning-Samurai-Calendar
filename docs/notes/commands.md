# 자주 하는 일 — 명령 모음

> `CLAUDE.md` 에서 옮겨 온 기록이다(2026-11-24, 문장은 그대로). 이 주제의 코드를 건드리기 전에 읽는다.

## 자주 하는 일

대부분의 작업은 **수집 없이** 끝난다. `data/*.json`이 저장소에 들어 있으므로
표기·레이아웃·기능을 고칠 때는 빌드만 다시 하면 된다.

```bash
python build.py
```

수집까지 다시 하려면 (네트워크 필요):

```bash
python scrape.py    2026-07-20 2026-09-30   # 일본 일정 (앞으로의 예정, 닛케이)
python scrape_jp_tdnet.py                   # 일본 발표 완료분 (TDnet, 최근 한 달)
python scrape_jp_tdnet.py --probe           # 목록 생김새만 떠보기
python scrape_mon_jp.py                     # 월매출 수치 (첨부 PDF 표에서)
python scrape_mon_jp.py --probe 7685        # 한 종목의 PDF 를 뜯어 본다
python scrape_mon_web.py                    # 월매출 (流通ニュース 월차 기사)
python scrape_mon_web.py --probe            # 목록·기사 한 장만 떠본다
python scrape_mon_ir.py                     # 월매출 (회사 IR 페이지 월차 표)
python scrape_mon_ir.py --probe 9843        # 한 회사의 月次 페이지를 뜯어 본다
python monir.py                             # IR 표 읽기 스스로 시험
python monweb.py                            # 기사 표 읽기 스스로 시험
python montable.py                          # PDF 표 읽기 스스로 시험
python pdftext.py 어떤.pdf                  # PDF 에서 글자만 꺼내 본다
python scrape_jp_sched.py                   # 일본 발표 예정 (JPX 공식 엑셀)
python scrape_jp_sched.py --probe           # 파일·열 매핑만 떠보기
python scrape_us.py 2026-07-20 2026-09-30   # 미국 일정
python scrape_hk.py 2026-07-20 2026-09-30   # 홍콩 공시
python scrape_caps.py                       # 일본·홍콩 시가총액 (범위 없음)
python scrape_fin.py                        # 미국 실적 수치 (범위 없음)
python scrape_fin_intl.py                   # 일본·홍콩 실적 수치 (범위 없음)
python scrape_fin_intl.py --probe           # 응답 생김새만 떠보기
python scrape_fin_jp.py                     # 일본 결산단신 (TDnet, 발표 당일)
python scrape_fin_jp.py --seg 6758          # 첨부에 부문 정보가 있는지 원문 확인
python scrape_seg_sec.py                    # 미국 부문별 매출 (SEC 분기 벌크·뼈대)
python scrape_seg_edgar.py                  # 미국 부문별 매출 최근 분기 (제출 서류)
python scrape_seg_edgar.py --probe          # 서류 한 장을 뜯어 생김새만 본다
python scrape_seg_hk.py                     # 홍콩 부문 비중 (동화순 F10)
python scrape_seg_hk.py --probe 00700       # 파서가 뭘 읽는지 본다
python scrape_seg_jp_edinetdb.py            # 일본 부문 연간 이력 (EDINETDB_KEY 필요)
python scrape_seg_jp_edinetdb.py --probe    # 아식스 한 종목만 떠본다 (예산 1건)
python scrape_fin_seg.py                    # 미국 부문별 매출 (최근 분기 보충)
python scrape_desc.py                       # 사업 설명 원문
```

**`data/` 에 있는 것만 실린다.** 세 시장이 다 없어도 빌드는 된다. 없는 시장은
탭에 「미수집」으로 뜨고 캘린더 자리에 "아직 수집하지 않았습니다"가 나온다.
