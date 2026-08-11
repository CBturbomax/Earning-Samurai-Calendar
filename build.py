# -*- coding: utf-8 -*-
"""
data/earnings.json  ->  index.html (단일 파일, 외부 의존 없음)

주간 캘린더는 클라이언트에서 그린다. 주(週)를 넘길 때마다 서버가 없으니,
전 기간 데이터를 JSON으로 심어두고 JS가 해당 주만 잘라 렌더한다.
"""
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from companies import GROUP_ORDER, NOTABLE
from translit import to_korean

HERE = Path(__file__).parent
DATA = HERE / "data" / "earnings.json"
OUT = HERE / "index.html"

# 결산종별 표기를 짧게. 원문은 第１/第２/第３/本.
KIND_MAP = {"第１": "1Q", "第２": "2Q", "第３": "3Q", "本": "본결산"}

# 업종 36종 — 닫힌 집합이라 전부 한글로 옮긴다.
SECTOR_KO = {
    "その他製造": "기타제조", "その他金融": "기타금융", "ガス": "가스", "ゴム": "고무",
    "サービス": "서비스", "パルプ・紙": "펄프·종이", "不動産": "부동산", "保険": "보험",
    "倉庫": "창고", "化学": "화학", "医薬品": "의약품", "商社": "상사",
    "小売業": "소매업", "建設": "건설", "機械": "기계", "水産": "수산",
    "海運": "해운", "石油": "석유", "空運": "항공", "窯業": "요업",
    "精密機器": "정밀기기", "繊維": "섬유", "自動車": "자동차", "証券": "증권",
    "輸送用機器": "운송용기기", "通信": "통신", "造船": "조선",
    "鉄道・バス": "철도·버스", "鉄鋼": "철강", "鉱業": "광업", "銀行": "은행",
    "陸運": "육운", "電力": "전력", "電気機器": "전기기기",
    "非鉄金属製品": "비철금속제품", "食品": "식품",
}
MARKET_KO = {"東証": "도쿄", "名証": "나고야", "札証": "삿포로", "福証": "후쿠오카"}

HOLIDAY_KO = {
    "元日": "새해 첫날", "成人の日": "성인의 날", "建国記念の日": "건국기념일",
    "天皇誕生日": "천황탄생일", "春分の日": "춘분", "昭和の日": "쇼와의 날",
    "憲法記念日": "헌법기념일", "みどりの日": "녹색의 날", "こどもの日": "어린이날",
    "振替休日": "대체휴일", "海の日": "바다의 날", "山の日": "산의 날",
    "敬老の日": "경로의 날", "国民の休日": "국민의 휴일", "秋分の日": "추분",
    "スポーツの日": "스포츠의 날", "文化の日": "문화의 날",
    "勤労感謝の日": "근로감사의 날", "大納会後休場": "연말 휴장",
}

# 2026년 일본 증시 휴장일(국민의 축일). 발표가 0건인 날이 주말인지
# 공휴일인지 구분해줘야 "이 날은 원래 없는 날"임이 읽힌다.
HOLIDAYS = {
    "2026-01-01": "元日", "2026-01-12": "成人の日",
    "2026-02-11": "建国記念の日", "2026-02-23": "天皇誕生日",
    "2026-03-20": "春分の日", "2026-04-29": "昭和の日",
    "2026-05-03": "憲法記念日", "2026-05-04": "みどりの日",
    "2026-05-05": "こどもの日", "2026-05-06": "振替休日",
    "2026-07-20": "海の日", "2026-08-11": "山の日",
    "2026-09-21": "敬老の日", "2026-09-22": "国民の休日",
    "2026-09-23": "秋分の日", "2026-10-12": "スポーツの日",
    "2026-11-03": "文化の日", "2026-11-23": "勤労感謝の日",
    "2026-12-31": "大納会後休場",
}


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build():
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    rows = raw["rows"]
    ok_days = raw.get("ok_days", sorted({r["date"] for r in rows}))

    # 행을 배열로 눕혀 담는다. 키 이름이 3천 번 반복되면 파일만 커진다.
    # [날짜, 코드, 한글명, 결산기, 분기, 업종, 시장, 원문, 변환등급]
    packed = []
    for r in sorted(rows, key=lambda x: (x["date"], x["code"])):
        ko, lvl = to_korean(r["name"], NOTABLE.get(r["code"], ("",))[0])
        fy = r["fy"].replace("月期", "월 결산")
        packed.append([r["date"], r["code"], ko, fy,
                       KIND_MAP.get(r["kind"], r["kind"]),
                       SECTOR_KO.get(r["sector"], r["sector"]),
                       MARKET_KO.get(r["market"], r["market"]),
                       r["name"], lvl])

    per_day = Counter(r["date"] for r in rows)
    sectors = sorted({p[5] for p in packed if p[5]})
    markets = sorted({p[6] for p in packed if p[6]})
    lvl_counts = Counter(p[8] for p in packed)
    notable_hits = sum(1 for r in rows if r["code"] in NOTABLE)
    busiest = max(per_day.items(), key=lambda kv: kv[1]) if per_day else ("-", 0)

    # 데이터가 있는 주만 네비게이션에 노출한다.
    weeks = sorted({monday_of(date.fromisoformat(d)).isoformat() for d in ok_days})

    today = date.today().isoformat()
    default_week = monday_of(date.fromisoformat(today)).isoformat()
    if default_week not in weeks and weeks:
        default_week = min(weeks, key=lambda w: abs(
            (date.fromisoformat(w) - date.fromisoformat(today)).days))

    payload = {
        "rows": packed,
        "notable": {k: list(v) for k, v in NOTABLE.items()},
        "groupOrder": GROUP_ORDER,
        "holidays": {d: HOLIDAY_KO.get(n, n) for d, n in HOLIDAYS.items()},
        "okDays": ok_days,
        "weeks": weeks,
        "defaultWeek": default_week,
        "today": today,
        "sectors": sectors,
        "markets": markets,
        "source": raw.get("source", ""),
        "sourceUrl": raw.get("source_url", ""),
    }

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    head = (f'전 기간 <b>{len(rows):,}건</b> · 주목종목 <b>{notable_hits}건</b> · '
            f'수집 <b>{ok_days[0]} ~ {ok_days[-1]}</b> · 갱신 {stamp}')
    verified = lvl_counts[2] + lvl_counts[1]
    tl_note = (f'회사명 {len(packed):,}건 중 <b>{verified:,}건</b>은 사전 표기, '
               f'<b>{lvl_counts[0]:,}건</b>은 기계 변환입니다.')

    html = TEMPLATE.replace("__HEAD__", head) \
                   .replace("__CARD_TOTAL__", f"{len(rows):,}") \
                   .replace("__CARD_DAYS__", str(len([d for d in ok_days if per_day[d]]))) \
                   .replace("__CARD_BUSY__", f"{busiest[0]} · {busiest[1]:,}건") \
                   .replace("__CARD_NOTABLE__", f"{notable_hits:,}") \
                   .replace("__SOURCE__", "니혼게이자이신문 「결산발표 스케줄」 (QUICK 제공)") \
                   .replace("__TLNOTE__", tl_note) \
                   .replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                                   separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"{OUT}  ({kb:,.0f} KB)")
    print(f"  {len(rows):,}건 / {len(ok_days)}일 / 주목 {notable_hits}건 / {len(weeks)}주")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- GitHub Pages는 같은 URL에 새 파일을 덮어쓴다. 캐시가 남으면 지난주 일정을
     이번주로 착각하게 되므로 매번 새로 받도록 강제한다. -->
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>일본 결산발표 캘린더 by CB</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=42dot+Sans:wght@300..800&display=swap"
      rel="stylesheet">
<style>
:root {
  --bg:#0f1419; --fg:#ffffff; --a1:#F0435A; --a2:#5B9BD5; --a3:#FFB020;
  --panel:#161d24; --line:#243039; --mute:#93a4b1; --ok:#7FD1A4;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:32px 28px 80px;
  background:var(--bg); color:var(--fg);
  font-family:'42dot Sans','Noto Sans JP','Yu Gothic','Meiryo',
              'Malgun Gothic',sans-serif;
  font-size:20px; line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1680px; margin:0 auto; }
.topline {
  font-size:19px; color:var(--mute); margin:0 0 10px; padding:9px 16px;
  background:var(--panel); border:1px solid var(--line); border-radius:8px;
  border-left:5px solid var(--a1); display:inline-block;
}
.topline b { color:var(--fg); }
h1 { font-size:38px; font-weight:800; margin:0 0 6px; letter-spacing:-.5px; }
h1 .jp { color:var(--a1); }
h2 {
  font-size:26px; font-weight:700; margin:52px 0 14px;
  padding-left:14px; border-left:6px solid var(--a3);
}
h2 .n { color:var(--a3); margin-right:10px; }
.sub { color:var(--mute); font-size:20px; margin:0 0 4px; }
.meta { color:var(--mute); font-size:18px; font-weight:400; }

.cards { display:flex; flex-wrap:wrap; gap:16px; margin:18px 0 8px; }
.card {
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 22px; min-width:190px;
}
.card .k { color:var(--mute); font-size:18px; }
.card .v { font-size:30px; font-weight:800; color:var(--a1); }
.card .v.sm { font-size:22px; }

.note {
  background:#1a2129; border:1px solid var(--line); border-left:5px solid var(--a3);
  border-radius:8px; padding:14px 20px; margin:14px 0; color:#c9d6e0; font-size:19px;
}

/* ── 알림 배너 ─────────────────────────────────────────────── */
.alertbar {
  background:linear-gradient(90deg,#2a1a20,#1a2129);
  border:1px solid #4a2530; border-left:5px solid var(--a1);
  border-radius:10px; padding:16px 20px; margin:16px 0;
}
.alertbar.none { border-left-color:var(--line); background:#151c23; }
.alertbar .ah { font-size:21px; font-weight:700; margin-bottom:6px; }
.alertbar .ah .cnt { color:var(--a1); }
.alertbar .arow {
  display:flex; flex-wrap:wrap; gap:8px; margin-top:10px;
}
.alertbar .hint { color:var(--mute); font-size:18px; }

/* ── 툴바 ──────────────────────────────────────────────────── */
.tools {
  display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:14px 0;
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px;
}
select, input[type=search] {
  background:#0b1015; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:11px 13px; font-size:19px; font-family:inherit;
}
select { max-width:230px; }
select:focus { outline:2px solid var(--a3); }
input[type=search] { width:min(420px,100%); padding:11px 16px; font-size:20px; }
input[type=search]:focus { outline:2px solid var(--a1); border-color:var(--a1); }
.chk {
  display:inline-flex; align-items:center; gap:8px; font-size:19px;
  cursor:pointer; user-select:none; white-space:nowrap;
}
.chk input { width:20px; height:20px; accent-color:var(--a1); cursor:pointer; }
.count { margin-left:auto; color:var(--mute); font-size:19px; }
.count b { color:var(--a1); font-size:22px; }

button.btn {
  background:#0b1015; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:11px 16px; font-size:19px; font-family:inherit;
  cursor:pointer;
}
button.btn:hover { border-color:var(--a1); color:var(--a1); }
button.btn.pri { background:var(--a1); border-color:var(--a1); color:#fff; font-weight:700; }
button.btn.pri:hover { filter:brightness(1.12); color:#fff; }
button.btn:disabled { opacity:.4; cursor:default; }
button.btn:disabled:hover { border-color:var(--line); color:var(--fg); }

/* ── 주 네비게이션 ─────────────────────────────────────────── */
.weeknav {
  display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:12px 16px; margin:14px 0;
}
.weeknav .wlabel { font-size:24px; font-weight:800; letter-spacing:-.3px; }
.weeknav .wsum { color:var(--mute); font-size:18px; }
.weeknav .spacer { margin-left:auto; }

/* ── 주간 캘린더 ───────────────────────────────────────────── */
.cal {
  display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:14px;
  align-items:start;
}
.cal.wk7 { grid-template-columns:repeat(7,minmax(0,1fr)); }
@media (max-width:1400px) { .cal, .cal.wk7 { grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media (max-width:900px)  { .cal, .cal.wk7 { grid-template-columns:minmax(0,1fr); } }

.day {
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  min-width:0; overflow:hidden;
}
.day.today { border-color:var(--a1); box-shadow:0 0 0 1px var(--a1) inset; }
.day.closed { opacity:.62; }
.dh {
  padding:12px 14px; border-bottom:1px solid var(--line); background:#1b232b;
  display:flex; align-items:baseline; gap:8px;
}
.dh .dow { font-size:18px; color:var(--mute); font-weight:700; }
.dh .dnum { font-size:26px; font-weight:800; }
.day.today .dh .dnum, .day.today .dh .dow { color:var(--a1); }
.dh .dcnt { margin-left:auto; font-size:18px; color:var(--mute); }
.dh .dcnt b { color:var(--a3); font-size:20px; }
.dh .todaytag {
  font-size:14px; font-weight:700; background:var(--a1); color:#fff;
  border-radius:4px; padding:2px 7px; margin-left:4px;
}
.dbody { padding:10px 10px 12px; }
.dsec {
  font-size:16px; color:var(--mute); font-weight:700; margin:6px 2px 7px;
  letter-spacing:.02em;
}
.dsec.star { color:var(--a3); }
.empty { color:#55636e; font-size:18px; padding:16px 4px; text-align:center; }
.empty .why { display:block; color:var(--mute); font-size:17px; margin-top:4px; }

/* 종목 칩 */
.chip {
  display:flex; align-items:center; gap:7px; width:100%;
  background:#0f1620; border:1px solid #1e2831; border-radius:7px;
  padding:7px 9px; margin-bottom:5px; cursor:pointer; text-align:left;
  font-family:inherit; color:var(--fg); font-size:17px; line-height:1.3;
}
.chip:hover { border-color:var(--a2); background:#16202b; }
.chip.big { background:#1d1418; border-color:#43242c; }
.chip.big:hover { border-color:var(--a1); }
.chip .cd {
  font-size:15px; font-weight:700; color:#8fb8dc; font-variant-numeric:tabular-nums;
  flex:0 0 auto;
}
.chip.big .cd { color:var(--a1); }
.chip .cn { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
.chip .cq {
  flex:0 0 auto; font-size:14px; color:var(--mute); border:1px solid var(--line);
  border-radius:4px; padding:1px 5px;
}
.chip .st { flex:0 0 auto; font-size:16px; color:#3d4852; }
.chip .st.on { color:var(--a3); }
.chip.watch { border-color:var(--a3); }
.more {
  width:100%; background:transparent; border:1px dashed var(--line);
  color:var(--mute); border-radius:7px; padding:7px; font-size:16px;
  cursor:pointer; font-family:inherit; margin-top:2px;
}
.more:hover { border-color:var(--a3); color:var(--a3); }

/* ── 표 ────────────────────────────────────────────────────── */
.scroll {
  max-height:640px; overflow:auto;
  border:1px solid var(--line); border-radius:10px;
}
table { border-collapse:separate; border-spacing:0; width:100%;
        font-variant-numeric:tabular-nums; }
thead th {
  position:sticky; top:0; z-index:2;
  background:#1b232b; color:#dce7ef; font-size:19px; font-weight:700;
  text-align:left; padding:13px 14px; white-space:nowrap;
  border-bottom:2px solid var(--line); cursor:pointer; user-select:none;
}
thead th:hover { color:var(--a3); }
thead th .ar { opacity:.35; font-size:15px; margin-left:5px; }
thead th.asc .ar, thead th.desc .ar { opacity:1; color:var(--a3); }
thead th.nos { cursor:default; }
thead th.nos:hover { color:#dce7ef; }
tbody td {
  padding:11px 14px; text-align:left; white-space:nowrap;
  border-bottom:1px solid #1c252d; font-size:19px;
}
tbody tr { background:#0f1419; }
tbody tr:nth-child(even) { background:#12191f; }
tbody tr:hover { background:#1d2833; }
td.code { color:#8fb8dc; font-weight:700; }
td.code.big { color:var(--a1); }
td.jp { color:var(--mute); font-size:17px; }
td.dim { color:var(--mute); font-size:18px; }
/* 기계 변환한 한글 표기는 점선을 깔아 구분한다. 마우스를 올리면 원문이 뜬다. */
.guess { border-bottom:1px dotted #3c4750; }
.sbtn { background:none; border:0; cursor:pointer; font-size:19px; color:#3d4852;
        padding:0 4px; font-family:inherit; }
.sbtn.on { color:var(--a3); }
.qtag {
  font-size:15px; border:1px solid var(--line); border-radius:4px;
  padding:1px 6px; color:var(--mute);
}
.qtag.q4 { color:var(--a3); border-color:#4a3a1c; }

/* ── 주목종목 그룹 ─────────────────────────────────────────── */
.groups { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr));
          gap:16px; margin-top:14px; }
.gbox { background:var(--panel); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px 10px; min-width:0; }
.gbox h3 { font-size:20px; margin:2px 0 10px; font-weight:700; }
.gbox h3 .gn { color:var(--mute); font-size:17px; font-weight:400; margin-left:8px; }
.grow { display:flex; align-items:center; gap:9px; padding:6px 2px;
        border-bottom:1px solid #1a222a; font-size:18px; }
.grow:last-child { border-bottom:0; }
.grow .gc { color:#8fb8dc; font-weight:700; font-size:16px; flex:0 0 46px; }
.grow .gk { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
.grow .ge { color:var(--mute); font-size:15px; }
.grow .gd { flex:0 0 auto; color:var(--a3); font-size:17px; font-weight:700; }
.grow .gd.past { color:var(--mute); font-weight:400; }

/* ── 발표 건수 차트 ────────────────────────────────────────── */
.chartbox { background:var(--panel); border:1px solid var(--line); border-radius:10px;
            padding:14px 12px 8px; margin-top:14px; overflow-x:auto; }
svg.bars { display:block; width:100%; height:auto; min-width:900px; }
svg.bars text { font-family:inherit; fill:var(--mute); font-size:11px; }
svg.bars .vl { fill:var(--fg); font-size:11px; font-weight:700; }
svg.bars rect.b { fill:var(--a2); }
svg.bars rect.b.wk { fill:var(--a1); }
svg.bars rect.b:hover { fill:var(--a3); }

/* ── 상세 모달 ─────────────────────────────────────────────── */
.mdback { position:fixed; inset:0; background:rgba(6,10,14,.82); z-index:50;
          display:flex; align-items:center; justify-content:center; padding:24px; }
.mdback[hidden] { display:none; }
.md { background:var(--panel); border:1px solid var(--line); border-radius:12px;
      max-width:620px; width:100%; padding:22px 24px; }
.md .mt { font-size:26px; font-weight:800; margin:0 0 4px; }
.md .ms { color:var(--mute); font-size:18px; margin:0 0 14px; }
.md dl { display:grid; grid-template-columns:auto 1fr; gap:8px 16px; margin:0 0 18px;
         font-size:19px; }
.md dt { color:var(--mute); }
.md dd { margin:0; }
.md .mact { display:flex; gap:10px; flex-wrap:wrap; }
.md a.btn { text-decoration:none; display:inline-block; }

.foot { color:var(--mute); font-size:17px; margin-top:56px; line-height:1.7;
        border-top:1px solid var(--line); padding-top:18px; }
.foot a { color:var(--a2); }
</style>
</head>
<body>
<div class="wrap">

<div class="topline">__HEAD__</div>
<h1>일본 결산발표 캘린더 <span class="jp">by CB</span></h1>
<p class="sub">주간 결산발표 일정 — 누가 언제 발표하는지, 관심종목은 알림까지</p>

<div class="cards">
  <div class="card"><div class="k">수집 발표</div><div class="v">__CARD_TOTAL__</div></div>
  <div class="card"><div class="k">발표일 수</div><div class="v">__CARD_DAYS__</div></div>
  <div class="card"><div class="k">주목종목 발표</div><div class="v">__CARD_NOTABLE__</div></div>
  <div class="card"><div class="k">최다 발표일</div><div class="v sm">__CARD_BUSY__</div></div>
</div>

<h2><span class="n">1</span>관심종목 알림</h2>
<div id="alertbar" class="alertbar none"></div>
<div class="tools">
  <button class="btn pri" id="icsWatch">📅 관심종목 일정 내보내기 (.ics)</button>
  <button class="btn" id="icsWeek">이번 주 전체 .ics</button>
  <button class="btn" id="clearWatch">관심종목 비우기</button>
  <span class="count">★ 를 눌러 담으면 브라우저에 저장됩니다</span>
</div>

<h2><span class="n">2</span>주간 캘린더 <span class="meta">일본 상장사 결산발표 예정</span></h2>
<div class="weeknav">
  <button class="btn" id="wPrev">← 이전 주</button>
  <div>
    <div class="wlabel" id="wLabel">—</div>
    <div class="wsum" id="wSum"></div>
  </div>
  <button class="btn" id="wNext">다음 주 →</button>
  <span class="spacer"></span>
  <button class="btn" id="wToday">오늘 주</button>
  <select id="wPick"></select>
  <label class="chk"><input type="checkbox" id="onlyBig">주목종목만</label>
  <label class="chk"><input type="checkbox" id="onlyWatch">관심종목만</label>
  <label class="chk"><input type="checkbox" id="jpToggle">원문(일본어)</label>
</div>
<div class="cal" id="cal"></div>

<h2><span class="n">3</span>주목종목 발표일 <span class="meta" id="gMeta"></span></h2>
<div class="note">
  대만 AI서버 공급망 표와 짝이 맞도록, 일본 쪽 강점인 <b>장비·소재·부품</b>을 앞에 두고
  일반 대형주를 뒤에 붙였습니다. 수집 기간 안에 발표 일정이 잡힌 종목만 나옵니다.
</div>
<div class="groups" id="groups"></div>

<h2><span class="n">4</span>일자별 발표 건수 <span class="meta">막대를 누르면 그 주로 이동</span></h2>
<div class="chartbox"><svg class="bars" id="bars" viewBox="0 0 1400 260"
     preserveAspectRatio="xMinYMid meet"></svg></div>

<h2><span class="n">5</span>전체 종목 표</h2>
<div class="tools">
  <input type="search" id="q" placeholder="한글·원문·영문·코드 검색 — 소니 / ソニー / Sony / 6758" autocomplete="off">
  <select id="fSector"><option value="">전체 업종</option></select>
  <select id="fMarket"><option value="">전체 시장</option></select>
  <select id="fKind"><option value="">전체 분기</option></select>
  <label class="chk"><input type="checkbox" id="tBig">주목종목만</label>
  <label class="chk"><input type="checkbox" id="tWatch">관심종목만</label>
  <label class="chk"><input type="checkbox" id="tFuture">오늘 이후만</label>
  <span class="count" id="tCnt"></span>
</div>
<div class="scroll">
  <table id="tAll">
    <thead><tr>
      <th class="nos" style="width:44px">★</th>
      <th data-k="0">발표일<span class="ar">▾</span></th>
      <th data-k="1">코드<span class="ar">▾</span></th>
      <th data-k="2">회사명<span class="ar">▾</span></th>
      <th data-k="7">원문<span class="ar">▾</span></th>
      <th data-k="4">분기<span class="ar">▾</span></th>
      <th data-k="3">결산기<span class="ar">▾</span></th>
      <th data-k="5">업종<span class="ar">▾</span></th>
      <th data-k="6">시장<span class="ar">▾</span></th>
    </tr></thead>
    <tbody id="tBody"></tbody>
  </table>
</div>

<div class="foot">
  출처 __SOURCE__ · <span id="srcLink"></span><br>
  __TLNOTE__ 지명·인명 한자는 훈독이라(小田原=오다와라) 기계 변환이 틀릴 수 있습니다.
  점선이 그어진 이름이 기계 변환분이고, 마우스를 올리면 원문이 뜹니다.
  캘린더의 <b>원문(일본어)</b> 체크로 통째로 바꿔 볼 수도 있습니다.<br>
  발표일은 예정일이며 회사 사정으로 바뀔 수 있습니다. 발표 시각은 원본에 없어 표기하지 않았습니다
  (일본은 대부분 장 마감 후 15시 전후 발표).<br>
  <span id="gapNote"></span>
  관심종목은 이 브라우저에만 저장되며 서버로 전송되지 않습니다.
</div>
</div>

<div class="mdback" id="mdBack" hidden>
  <div class="md" role="dialog" aria-modal="true">
    <p class="mt" id="mdTitle"></p>
    <p class="ms" id="mdSub"></p>
    <dl id="mdList"></dl>
    <div class="mact">
      <button class="btn pri" id="mdStar">★ 관심종목</button>
      <a class="btn" id="mdNikkei" target="_blank" rel="noopener">닛케이 종목정보</a>
      <a class="btn" id="mdIr" target="_blank" rel="noopener">적시공시</a>
      <button class="btn" id="mdClose">닫기 (ESC)</button>
    </div>
  </div>
</div>

<script id="payload" type="application/json">__DATA__</script>
<script>
/* ══════════════════════════════════════════════════════════════
   일본 결산발표 캘린더 — 렌더링
   행은 [date, code, name, fy, kind, sector, market] 배열로 들어온다.
   ══════════════════════════════════════════════════════════════ */
const D = JSON.parse(document.getElementById('payload').textContent);
const ROWS = D.rows, NOTE = D.notable, HOL = D.holidays;
const DOW = ['월','화','수','목','금','토','일'];
const LS_KEY = 'jpEarnWatch';

const byDate = new Map();
for (const r of ROWS) {
  if (!byDate.has(r[0])) byDate.set(r[0], []);
  byDate.get(r[0]).push(r);
}
const okDays = new Set(D.okDays);

/* 관심종목 — localStorage. 사파리 프라이빗 모드처럼 쓰기가 막힌 환경에서도
   페이지 전체가 죽지는 않게 감싼다. */
let watch = new Set();
try { watch = new Set(JSON.parse(localStorage.getItem(LS_KEY) || '[]')); } catch (e) {}
function saveWatch() {
  try { localStorage.setItem(LS_KEY, JSON.stringify([...watch])); } catch (e) {}
}
function toggleWatch(code) {
  watch.has(code) ? watch.delete(code) : watch.add(code);
  saveWatch(); renderAll();
}

const pad = n => String(n).padStart(2, '0');
const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const parse = s => { const [y,m,d] = s.split('-').map(Number); return new Date(y, m-1, d); };
const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* r[2]=한글명, r[7]=일본어 원문, r[8]=변환등급(2 사전·1 단어사전·0 기계).
   '원문 보기'를 켜면 일본어를 그대로 보여준다. */
let showJp = false;
const nameOf = r => showJp ? r[7] : r[2];
const bothOf = r => r[2] + ' · ' + r[7];

let week = D.defaultWeek;

/* ── 주 네비게이션 ────────────────────────────────────────── */
const wPick = document.getElementById('wPick');
for (const w of D.weeks) {
  const o = document.createElement('option');
  o.value = w;
  const cnt = countWeek(w);
  o.textContent = fmtWeek(w) + '  (' + cnt + '건)';
  wPick.appendChild(o);
}
function weekDays(w) {
  const out = [];
  for (let i = 0; i < 7; i++) out.push(addDays(w, i));
  return out;
}
function countWeek(w) {
  return weekDays(w).reduce((s, d) => s + (byDate.get(d) || []).length, 0);
}
function fmtWeek(w) {
  const a = parse(w), b = parse(addDays(w, 4));
  return (a.getMonth()+1) + '/' + a.getDate() + ' ~ ' + (b.getMonth()+1) + '/' + b.getDate()
       + ' (' + a.getFullYear() + ')';
}
document.getElementById('wPrev').onclick = () => go(addDays(week, -7));
document.getElementById('wNext').onclick = () => go(addDays(week, 7));
document.getElementById('wToday').onclick = () => go(D.defaultWeek);
wPick.onchange = e => go(e.target.value);
function go(w) { week = w; renderAll(); }

/* ── 주간 캘린더 ──────────────────────────────────────────── */
const CHIP_LIMIT = 12;
const expanded = new Set();

function chip(r, big) {
  const on = watch.has(r[1]);
  return '<button class="chip' + (big ? ' big' : '') + (on ? ' watch' : '') +
         '" data-code="' + esc(r[1]) + '" data-date="' + r[0] + '">' +
         '<span class="cd">' + esc(r[1]) + '</span>' +
         '<span class="cn' + (r[8] === 0 ? ' guess' : '') + '" title="' + esc(bothOf(r)) +
         '">' + esc(nameOf(r)) + '</span>' +
         '<span class="cq">' + esc(r[4]) + '</span>' +
         '<span class="st' + (on ? ' on' : '') + '">' + (on ? '★' : '☆') + '</span>' +
         '</button>';
}

function renderCal() {
  const onlyBig = document.getElementById('onlyBig').checked;
  const onlyWatch = document.getElementById('onlyWatch').checked;
  const days = weekDays(week);
  // 주말은 발표가 잡혀 있을 때만 칸을 내준다.
  const showWeekend = (byDate.get(days[5]) || []).length || (byDate.get(days[6]) || []).length;
  const shown = showWeekend ? days : days.slice(0, 5);
  const cal = document.getElementById('cal');
  cal.className = 'cal' + (showWeekend ? ' wk7' : '');

  let total = 0, bigTotal = 0, watchTotal = 0;
  cal.innerHTML = shown.map(d => {
    let list = byDate.get(d) || [];
    total += list.length;
    bigTotal += list.filter(r => NOTE[r[1]]).length;
    watchTotal += list.filter(r => watch.has(r[1])).length;
    if (onlyBig) list = list.filter(r => NOTE[r[1]]);
    if (onlyWatch) list = list.filter(r => watch.has(r[1]));

    const dt = parse(d), dow = (dt.getDay() + 6) % 7;
    const isToday = d === D.today;
    const hol = HOL[d], weekend = dow >= 5;
    const closed = (hol || weekend) && !list.length;

    const big = list.filter(r => NOTE[r[1]]);
    const rest = list.filter(r => !NOTE[r[1]]);
    const key = week + d;
    const open = expanded.has(key);
    const restShown = open ? rest : rest.slice(0, CHIP_LIMIT);

    let body;
    if (!list.length) {
      let why = '';
      if (hol) why = '<span class="why">' + esc(hol) + ' · 휴장</span>';
      else if (weekend) why = '<span class="why">주말 · 휴장</span>';
      else if (!okDays.has(d)) why = '<span class="why">미수집 구간</span>';
      body = '<div class="empty">발표 없음' + why + '</div>';
    } else {
      body = (big.length ? '<div class="dsec star">★ 주목종목 ' + big.length + '</div>' +
                           big.map(r => chip(r, true)).join('') : '') +
             (rest.length ? '<div class="dsec">그 외 ' + rest.length + '</div>' +
                            restShown.map(r => chip(r, false)).join('') : '') +
             (rest.length > CHIP_LIMIT
               ? '<button class="more" data-key="' + key + '">' +
                 (open ? '접기' : '+' + (rest.length - CHIP_LIMIT) + '개 더 보기') + '</button>'
               : '');
    }

    return '<div class="day' + (isToday ? ' today' : '') + (closed ? ' closed' : '') + '">' +
      '<div class="dh"><span class="dow">' + DOW[dow] + '</span>' +
      '<span class="dnum">' + dt.getDate() + '</span>' +
      (isToday ? '<span class="todaytag">오늘</span>' : '') +
      '<span class="dcnt"><b>' + list.length + '</b>건</span></div>' +
      '<div class="dbody">' + body + '</div></div>';
  }).join('');

  document.getElementById('wLabel').textContent = fmtWeek(week);
  document.getElementById('wSum').textContent =
    total.toLocaleString() + '건 · 주목 ' + bigTotal + '건' +
    (watch.size ? ' · 관심 ' + watchTotal + '건' : '');
  wPick.value = D.weeks.includes(week) ? week : '';

  const wi = D.weeks.indexOf(week);
  document.getElementById('wPrev').disabled = wi === 0;
  document.getElementById('wNext').disabled = wi === D.weeks.length - 1;
}

/* ── 알림 배너 ────────────────────────────────────────────── */
function renderAlert() {
  const el = document.getElementById('alertbar');
  if (!watch.size) {
    el.className = 'alertbar none';
    el.innerHTML = '<div class="ah">관심종목이 비어 있습니다</div>' +
      '<div class="hint">아래 캘린더나 표에서 ☆ 를 누르면 여기에 모이고, ' +
      '발표일이 다가오면 D-day로 알려줍니다. .ics로 내보내 구글·아웃룩 캘린더에 넣으면 ' +
      '실제 알림도 받을 수 있습니다.</div>';
    return;
  }
  // 오늘 이후 예정만, 가까운 순으로.
  const up = ROWS.filter(r => watch.has(r[1]) && r[0] >= D.today)
                 .sort((a, b) => a[0] < b[0] ? -1 : 1);
  const inWeek = up.filter(r => weekDays(week).includes(r[0]));
  el.className = 'alertbar' + (up.length ? '' : ' none');
  const ddays = up.slice(0, 10).map(r => {
    const dd = Math.round((parse(r[0]) - parse(D.today)) / 86400000);
    const tag = dd === 0 ? '오늘' : 'D-' + dd;
    return '<button class="chip big" data-code="' + esc(r[1]) + '" data-date="' + r[0] +
           '" style="width:auto"><span class="cd">' + tag + '</span>' +
           '<span class="cn">' + esc(nameOf(r)) + '</span>' +
           '<span class="cq">' + r[0].slice(5) + '</span></button>';
  }).join('');

  el.innerHTML =
    '<div class="ah">관심종목 <span class="cnt">' + watch.size + '</span>개 · ' +
    '앞으로 예정 <span class="cnt">' + up.length + '</span>건' +
    (inWeek.length ? ' · 이번 주 <span class="cnt">' + inWeek.length + '</span>건' : '') +
    '</div>' +
    (up.length ? '<div class="arow">' + ddays + '</div>'
               : '<div class="hint">수집 기간 안에 남은 발표 일정이 없습니다.</div>');
}

/* ── 주목종목 그룹 ────────────────────────────────────────── */
function renderGroups() {
  const perGroup = {};
  for (const g of D.groupOrder) perGroup[g] = [];
  // 종목마다 한 줄만 남긴다. 오늘 이후 일정이 있으면 그 중 가장 이른 것,
  // 없으면 가장 최근 과거 일정. ROWS가 날짜 오름차순이라 한 번만 훑으면 된다.
  const first = new Map();
  for (const r of ROWS) {
    if (!NOTE[r[1]]) continue;
    const cur = first.get(r[1]);
    if (!cur || (cur[0] < D.today && (r[0] >= D.today || r[0] > cur[0]))) first.set(r[1], r);
  }
  for (const [code, r] of first) {
    const g = NOTE[code][2];
    if (perGroup[g]) perGroup[g].push(r);
  }
  let shown = 0;
  const html = D.groupOrder.filter(g => perGroup[g].length).map(g => {
    const rs = perGroup[g].sort((a, b) => a[0] < b[0] ? -1 : 1);
    shown += rs.length;
    return '<div class="gbox"><h3>' + esc(g) + '<span class="gn">' + rs.length + '종목</span></h3>' +
      rs.map(r => {
        const past = r[0] < D.today;
        return '<div class="grow"><span class="gc">' + esc(r[1]) + '</span>' +
          '<span class="gk">' + esc(NOTE[r[1]][0]) +
          ' <span class="ge">' + esc(NOTE[r[1]][1]) + '</span></span>' +
          '<span class="gd' + (past ? ' past' : '') + '">' + r[0].slice(5) +
          ' <span class="qtag">' + esc(r[4]) + '</span></span></div>';
      }).join('') + '</div>';
  }).join('');
  document.getElementById('groups').innerHTML = html;
  document.getElementById('gMeta').textContent =
    shown + '종목 / 사전 등재 ' + Object.keys(NOTE).length + '종목';
}

/* ── 일자별 막대 ──────────────────────────────────────────── */
function renderBars() {
  const days = D.okDays.filter(d => (byDate.get(d) || []).length);
  const W = 1400, H = 260, PAD_L = 8, PAD_B = 46, PAD_T = 24;
  const n = days.length || 1;
  const bw = (W - PAD_L * 2) / n;
  const max = Math.max(...days.map(d => byDate.get(d).length), 1);
  const wk = new Set(weekDays(week));
  const parts = days.map((d, i) => {
    const v = byDate.get(d).length;
    const h = (H - PAD_B - PAD_T) * v / max;
    const x = PAD_L + i * bw, y = H - PAD_B - h;
    const label = d.slice(5).replace('-', '/');
    return '<g><rect class="b' + (wk.has(d) ? ' wk' : '') + '" x="' + (x + bw * .12).toFixed(1) +
      '" y="' + y.toFixed(1) + '" width="' + (bw * .76).toFixed(1) + '" height="' +
      Math.max(h, 1).toFixed(1) + '" data-date="' + d + '"><title>' + label + ' · ' +
      v + '건</title></rect>' +
      (v >= max * .45 ? '<text class="vl" x="' + (x + bw / 2).toFixed(1) + '" y="' +
        (y - 5).toFixed(1) + '" text-anchor="middle">' + v + '</text>' : '') +
      '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - PAD_B + 16) +
      '" text-anchor="end" transform="rotate(-60 ' + (x + bw / 2).toFixed(1) + ' ' +
      (H - PAD_B + 16) + ')">' + label + '</text></g>';
  }).join('');
  document.getElementById('bars').innerHTML = parts;
}

/* ── 전체 표 ──────────────────────────────────────────────── */
let sortKey = 0, sortDir = 1;
function renderTable() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const fs = document.getElementById('fSector').value;
  const fm = document.getElementById('fMarket').value;
  const fk = document.getElementById('fKind').value;
  const tb = document.getElementById('tBig').checked;
  const tw = document.getElementById('tWatch').checked;
  const tf = document.getElementById('tFuture').checked;

  let list = ROWS.filter(r => {
    if (fs && r[5] !== fs) return false;
    if (fm && r[6] !== fm) return false;
    if (fk && r[4] !== fk) return false;
    if (tb && !NOTE[r[1]]) return false;
    if (tw && !watch.has(r[1])) return false;
    if (tf && r[0] < D.today) return false;
    if (q) {
      const en = NOTE[r[1]] ? NOTE[r[1]][1] : '';
      if (!(r[1] + r[2] + r[7] + en).toLowerCase().includes(q)) return false;
    }
    return true;
  });

  list.sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x === y) return a[0] < b[0] ? -1 : 1;
    return (x < y ? -1 : 1) * sortDir;
  });

  const CAP = 600;
  document.getElementById('tBody').innerHTML = list.slice(0, CAP).map(r => {
    const nt = NOTE[r[1]], on = watch.has(r[1]);
    return '<tr data-code="' + esc(r[1]) + '" data-date="' + r[0] + '">' +
      '<td><button class="sbtn' + (on ? ' on' : '') + '" data-star="' + esc(r[1]) + '">' +
      (on ? '★' : '☆') + '</button></td>' +
      '<td class="dim">' + r[0] + '</td>' +
      '<td class="code' + (nt ? ' big' : '') + '">' + esc(r[1]) + '</td>' +
      '<td class="' + (r[8] === 0 ? 'guess' : '') + '">' + esc(r[2]) + '</td>' +
      '<td class="jp">' + esc(r[7]) + '</td>' +
      '<td><span class="qtag' + (r[4] === '본결산' ? ' q4' : '') + '">' + esc(r[4]) + '</span></td>' +
      '<td class="dim">' + esc(r[3]) + '</td>' +
      '<td class="dim">' + esc(r[5]) + '</td>' +
      '<td class="dim">' + esc(r[6]) + '</td></tr>';
  }).join('');
  document.getElementById('tCnt').innerHTML =
    '<b>' + list.length.toLocaleString() + '</b>건' +
    (list.length > CAP ? ' 중 ' + CAP + '건 표시 (검색으로 좁혀보세요)' : '');
}

/* ── 상세 모달 ────────────────────────────────────────────── */
let mdCode = null;
function openModal(code, dt) {
  const r = (byDate.get(dt) || []).find(x => x[1] === code) ||
            ROWS.find(x => x[1] === code);
  if (!r) return;
  mdCode = code;
  const nt = NOTE[code];
  document.getElementById('mdTitle').textContent = r[2] + ' (' + code + ')';
  document.getElementById('mdSub').textContent =
    r[7] + (nt ? ' · ' + nt[1] : '') + (r[8] === 0 ? ' · 한글 표기는 기계 변환' : '');
  const dd = Math.round((parse(r[0]) - parse(D.today)) / 86400000);
  document.getElementById('mdList').innerHTML =
    '<dt>발표 예정일</dt><dd>' + r[0] + ' (' + DOW[(parse(r[0]).getDay()+6)%7] + ') ' +
    (dd === 0 ? '· 오늘' : dd > 0 ? '· D-' + dd : '· ' + (-dd) + '일 전') + '</dd>' +
    '<dt>분기</dt><dd>' + esc(r[4]) + ' · ' + esc(r[3]) + '</dd>' +
    '<dt>업종</dt><dd>' + esc(r[5]) + '</dd>' +
    '<dt>시장</dt><dd>' + esc(r[6]) + '</dd>' +
    (nt ? '<dt>테마</dt><dd>' + esc(nt[2]) + '</dd>' : '');
  document.getElementById('mdNikkei').href = 'https://www.nikkei.com/nkd/company/?scode=' + code;
  document.getElementById('mdIr').href = 'https://www.nikkei.com/nkd/company/kigyo/?scode=' + code;
  const sb = document.getElementById('mdStar');
  sb.textContent = watch.has(code) ? '★ 관심종목 해제' : '☆ 관심종목 담기';
  document.getElementById('mdBack').hidden = false;
}
function closeModal() { document.getElementById('mdBack').hidden = true; mdCode = null; }
document.getElementById('mdClose').onclick = closeModal;
document.getElementById('mdBack').onclick = e => {
  if (e.target.id === 'mdBack') closeModal();
};
document.getElementById('mdStar').onclick = () => { if (mdCode) { toggleWatch(mdCode); closeModal(); } };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* ── .ics 내보내기 ────────────────────────────────────────── */
function icsEscape(s) { return String(s).replace(/([,;\\])/g, '\\$1').replace(/\n/g, '\\n'); }

/* RFC 5545는 한 줄을 75옥텟으로 제한하고, 넘치면 다음 줄을 공백 한 칸으로
   시작해 잇게 한다. 한글·일본어는 글자당 3바이트라 DESCRIPTION이 쉽게 넘어간다.
   너그러운 클라이언트도 많지만, 엄격한 파서에서 통째로 깨지는 걸 막는다.
   바이트로 재되 글자 중간에서는 자르지 않는다. */
const ICS_ENC = new TextEncoder();
function icsFold(line) {
  if (ICS_ENC.encode(line).length <= 75) return line;
  const out = [];
  let cur = '', len = 0;
  for (const ch of line) {                 // 코드포인트 단위로 순회
    const n = ICS_ENC.encode(ch).length;
    const cap = out.length ? 74 : 75;      // 이어지는 줄은 공백 한 칸을 먹는다
    if (len + n > cap) { out.push(cur); cur = ''; len = 0; }
    cur += ch; len += n;
  }
  if (cur) out.push(cur);
  return out[0] + out.slice(1).map(s => '\r\n ' + s).join('');
}

function makeIcs(rows, calName) {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
  const L = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//CB//JP Earnings Calendar//KO',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:' + icsEscape(calName), 'X-WR-TIMEZONE:Asia/Tokyo'];
  for (const r of rows) {
    const d = r[0].replace(/-/g, '');
    const end = addDays(r[0], 1).replace(/-/g, '');
    const nt = NOTE[r[1]];
    L.push('BEGIN:VEVENT',
      'UID:jpe-' + r[1] + '-' + d + '@cb-earnings',
      'DTSTAMP:' + stamp,
      'DTSTART;VALUE=DATE:' + d,
      'DTEND;VALUE=DATE:' + end,
      'SUMMARY:' + icsEscape('[결산] ' + r[2] + ' ' + r[1] + ' · ' + r[4]),
      'DESCRIPTION:' + icsEscape(
        r[7] + (nt ? ' / ' + nt[1] : '') + '\n' +
        r[4] + ' · ' + r[3] + ' · ' + r[5] + ' · ' + r[6] + '증시' +
        '\nhttps://www.nikkei.com/nkd/company/?scode=' + r[1]),
      'TRANSP:TRANSPARENT',
      'BEGIN:VALARM', 'TRIGGER:-P1D', 'ACTION:DISPLAY',
      'DESCRIPTION:' + icsEscape('내일 결산발표 — ' + r[2]),
      'END:VALARM', 'END:VEVENT');
  }
  L.push('END:VCALENDAR');
  return L.map(icsFold).join('\r\n') + '\r\n';
}
function download(name, text) {
  const blob = new Blob([text], { type: 'text/calendar;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
document.getElementById('icsWatch').onclick = () => {
  const rows = ROWS.filter(r => watch.has(r[1]));
  if (!rows.length) { alert('관심종목이 없습니다. ☆ 를 눌러 먼저 담아주세요.'); return; }
  download('jp-earnings-watchlist.ics', makeIcs(rows, '일본 결산발표 — 관심종목'));
};
document.getElementById('icsWeek').onclick = () => {
  const days = new Set(weekDays(week));
  const rows = ROWS.filter(r => days.has(r[0]));
  if (!rows.length) { alert('이번 주에는 발표 일정이 없습니다.'); return; }
  download('jp-earnings-' + week + '.ics', makeIcs(rows, '일본 결산발표 ' + fmtWeek(week)));
};
document.getElementById('clearWatch').onclick = () => {
  if (!watch.size) return;
  if (!confirm('관심종목 ' + watch.size + '개를 모두 비웁니다.')) return;
  watch.clear(); saveWatch(); renderAll();
};

/* ── 이벤트 위임 ──────────────────────────────────────────── */
document.addEventListener('click', e => {
  const more = e.target.closest('.more');
  if (more) {
    const k = more.dataset.key;
    expanded.has(k) ? expanded.delete(k) : expanded.add(k);
    renderCal();
    return;
  }
  const star = e.target.closest('[data-star]');
  if (star) { e.stopPropagation(); toggleWatch(star.dataset.star); return; }

  const chipEl = e.target.closest('.chip');
  if (chipEl) {
    // 칩 안의 ★ 영역을 누르면 담기, 나머지는 상세 열기
    if (e.target.closest('.st')) toggleWatch(chipEl.dataset.code);
    else openModal(chipEl.dataset.code, chipEl.dataset.date);
    return;
  }
  const tr = e.target.closest('#tBody tr');
  if (tr) { openModal(tr.dataset.code, tr.dataset.date); return; }

  const bar = e.target.closest('rect.b');
  if (bar) {
    const d = parse(bar.dataset.date);
    go(iso(new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay()+6)%7))));
    document.getElementById('cal').scrollIntoView({ behavior:'smooth', block:'center' });
  }
});

document.querySelectorAll('#tAll thead th[data-k]').forEach(th => {
  th.onclick = () => {
    const k = +th.dataset.k;
    sortDir = (k === sortKey) ? -sortDir : 1;
    sortKey = k;
    document.querySelectorAll('#tAll thead th').forEach(x => x.classList.remove('asc','desc'));
    th.classList.add(sortDir === 1 ? 'asc' : 'desc');
    th.querySelector('.ar').textContent = sortDir === 1 ? '▴' : '▾';
    renderTable();
  };
});

for (const [id, arr] of [['fSector', D.sectors], ['fMarket', D.markets],
                         ['fKind', ['1Q','2Q','3Q','본결산']]]) {
  const sel = document.getElementById(id);
  for (const v of arr) {
    const o = document.createElement('option'); o.value = o.textContent = v; sel.appendChild(o);
  }
  sel.onchange = renderTable;
}
document.getElementById('q').oninput = renderTable;
for (const id of ['tBig','tWatch','tFuture']) document.getElementById(id).onchange = renderTable;
for (const id of ['onlyBig','onlyWatch']) document.getElementById(id).onchange = renderCal;
document.getElementById('jpToggle').onchange = e => { showJp = e.target.checked; renderCal(); };

document.getElementById('srcLink').innerHTML =
  '<a href="' + D.sourceUrl + '" target="_blank" rel="noopener">' + D.sourceUrl + '</a>';

/* 수집 구간에 구멍이 있으면 숨기지 않고 적는다. 빈 칸이 '발표가 없는 날'인지
   '아직 못 받은 날'인지 구분되지 않으면 캘린더를 믿을 수 없다. */
(function () {
  const gaps = [];
  for (let d = D.okDays[0]; d <= D.okDays[D.okDays.length - 1]; d = addDays(d, 1)) {
    if (!okDays.has(d)) gaps.push(d);
  }
  document.getElementById('gapNote').innerHTML = gaps.length
    ? '미수집 ' + gaps.length + '일 (' + gaps.join(', ') + ') — 캘린더에 ' +
      '<b>미수집 구간</b>으로 표시됩니다. 다시 수집하면 채워집니다.<br>'
    : '';
})();

function renderAll() { renderCal(); renderAlert(); renderGroups(); renderBars(); renderTable(); }
renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
