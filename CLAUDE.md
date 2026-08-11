# Earning Samurai Calendar

일본 상장사 결산발표(決算発表) 주간 캘린더. 정적 단일 HTML 하나가 산출물이고,
GitHub Pages로 그대로 서비스한다. 서버·빌드툴·의존 패키지가 없다.

<https://cbturbomax.github.io/Earning-Samurai-Calender/>

## 파이프라인

```
scrape.py  ──>  data/earnings.json  ──>  build.py  ──>  index.html
(닛케이 수집)      (수집 원본)          (+ translit.py     (배포물)
                                        + companies.py)
```

표준 라이브러리만 쓴다. `pip install` 필요 없음.

## 자주 하는 일

대부분의 작업은 **수집 없이** 끝난다. `data/earnings.json`이 저장소에 들어 있으므로
표기·레이아웃·기능을 고칠 때는 빌드만 다시 하면 된다.

```bash
python build.py
```

수집까지 다시 하려면 (네트워크 필요):

```bash
python scrape.py 2026-07-20 2026-09-30
```

## 손대는 위치

| 하고 싶은 것 | 고칠 파일 |
|---|---|
| 회사 한글명이 틀렸다 | `companies.py`(주목종목) 또는 `translit.py`의 `WORDS` |
| 주목종목을 넣고 뺀다 | `companies.py`의 `NOTABLE` |
| 화면·기능·CSS | `build.py`의 `TEMPLATE` (HTML/CSS/JS가 전부 여기 있다) |
| 업종·시장·휴일 한글 표기 | `build.py`의 `SECTOR_KO` / `MARKET_KO` / `HOLIDAY_KO` |

`index.html`은 **직접 고치지 않는다.** `build.py`가 덮어쓴다.

## 알아둘 것

**닛케이 레이트리밋.** 연속 30~40 요청쯤에서 본문 없는 껍데기 페이지를 돌려준다.
이걸 "0건"으로 삼키면 발표가 몰린 날이 조용히 빈 날로 기록된다(실제로 한 번 당했다).
`fetch_valid()`가 건수 표시나 `0件` 문구를 확인하고, 없으면 15/45/120/300초 물러섰다
재시도한다. 하루 받을 때마다 증분 저장하므로 중간에 죽어도 재실행하면 이어서 받는다.
**이 검증을 끄거나 약하게 만들지 말 것.**

**한글 표기는 기계 변환이 75%다.** 한자 지명·인명은 훈독이라(`小田原`=오다와라)
한국 한자음으로 읽으면 틀린다. 그래서 화면에는 원문을 항상 병기한다 —
점선 표시, 표의 원문 열, 캘린더의 원문 토글, 원문 검색. 이 안전장치를 빼지 말 것.

**발표 시각 데이터는 없다.** 원본에 없어서 EarningsHub의 Before Open / After Close에
대응하는 축을 만들 수 없었다. 분기(1Q/2Q/3Q/본결산)로 갈라놨다.

**수집 구멍을 숨기지 않는다.** 발표가 없는 날과 아직 못 받은 날은 다르다.
`okDays`로 구분해 캘린더에 "미수집 구간"으로 적고 푸터에도 목록을 낸다.

## 배포

`main`에 푸시하면 GitHub Pages가 자동으로 다시 배포한다.
`index.html`을 커밋에 포함시켜야 사이트에 반영된다.
