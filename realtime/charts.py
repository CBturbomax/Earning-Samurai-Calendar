"""텔레그램으로 보낼 차트 — 회원님이 고른 다섯 장(10/29).

  1. 연간 표(억 원 환산) + 멀티플 — 사이트 상세창 fcstTable 과 같은 셈
  2. 분기 매출 막대 + 영업이익률 선 / 아래 매출 성장률(YoY)
  3. 가이던스 변천(일본) — 회계연도마다 최초·1Q·2Q·3Q·실제
  4. 매출 breakdown(사업부·지역) — 부문마다 선
  5. 수주 — 자료가 있을 때만(지금은 없음)

숫자는 차트가 말하고 글은 '왜'만 적는다. 어닝사무라이 색(짙은 남색 · 파랑 막대 · 주황 선),
마지막(이번) 분기는 노랗게.
"""
import io
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib import font_manager                 # noqa: E402
from matplotlib import patheffects as pe            # noqa: E402

OUTLINE = [pe.withStroke(linewidth=3, foreground="#1b2633")]

for name in ("Noto Sans CJK KR", "Noto Sans CJK JP", "NanumGothic"):
    if any(name in f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = name
        break
plt.rcParams["axes.unicode_minus"] = False

BG, PANEL, GRID, FG, MUTE = "#22303f", "#2b3a4f", "#3a4b60", "#ffffff", "#9fb0c0"
BAR, LAST, LINE, LINE2 = "#5B9BD5", "#FFCB05", "#ED7D31", "#7FD1A4"
SEGC = ["#8b7bb8", "#c4708a", "#5B9BD5", "#7FD1A4", "#e8c46a", "#ED7D31", "#93a4b1", "#4f8a8b"]
UNIT = {"USD": ("$", 1e6, "M"), "JPY": ("¥", 1e8, "억"), "HKD": ("HK$", 1e6, "M"),
        "CNY": ("¥", 1e6, "M"), "EUR": ("€", 1e6, "M"), "GBP": ("£", 1e6, "M")}


def _unit(cur):
    return UNIT.get(cur, ("", 1e6, "M"))


def _fig(title, sub):
    fig, ax = plt.subplots(figsize=(13, 6.4), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=MUTE, labelsize=10)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    fig.suptitle(title, color=FG, fontsize=16, fontweight="bold", x=0.02, ha="left", y=0.985)
    ax.set_title(sub, color=MUTE, fontsize=11, pad=10)
    return fig, ax


def _png(fig):
    buf = io.BytesIO()
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()



def _yoy(pts, freq):
    lag = 2 if freq == "H" else 4
    out = []
    for i, p in enumerate(pts):
        prev = pts[i - lag][1] if i >= lag else None
        out.append((p[1] / prev - 1) * 100 if prev and p[1] is not None else None)
    return out


def _style(ax):
    ax.set_facecolor(PANEL)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=MUTE, labelsize=9.5)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _num(v):
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else f"{v:,.1f}" if a >= 10 else f"{v:,.2f}"


# ── 1. 연간 표 ──────────────────────────────────────────────────────────
def ann_cols(F, G=None):
    """사이트 build.py 의 fcstTable 과 같은 셈. 열 넷: 직전·당해·차기·차차기."""
    y = F.get("y") or []
    if not y:
        return None
    a = y[0]
    b = y[1] if len(y) > 1 else None
    yr = lambda d: int(d[:4]) if d else None                       # noqa: E731
    shares = F["m"] / F["p"] if F.get("p") else (b[3] / b[4] if b and b[3] and b[4] else None)
    n = F.get("n") or [None, None]
    cols = [
        {"name": "직전년도", "end": a[0], "rev": a[1], "opi": a[2], "ni": a[3]},
        {"name": "당해년도", "end": b[0] if b else None, "rev": b and b[1], "opi": b and b[2], "ni": b and b[3]},
        {"name": "차기년도", "end": None, "rev": n[0], "opi": None, "eps": n[1]},
        {"name": "차차기년도", "end": None, "rev": None, "opi": None, "eps": None},
    ]
    base = cols[1]["end"] or cols[0]["end"]
    mo = lambda d: int(d[:4]) * 12 + int(d[5:7])                   # noqa: E731
    for j, i in enumerate((2, 3)):
        c = cols[i]
        c["end"] = f"{yr(base) + (j + 1 if cols[1]['end'] else j + 2)}{base[4:]}"
        q = next((x for x in F.get("q") or [] if abs(mo(x[0]) - mo(c["end"])) <= 1), None)
        if c.get("eps") is None and q:
            c["eps"] = q[1]
        c["ni"] = c["eps"] * shares if c.get("eps") is not None and shares else None
    if G and cols[1]["end"] and G.get("end", "")[:7] == cols[1]["end"][:7]:
        for k in ("rev", "opi", "ni"):
            if cols[1].get(k) is None and G.get(k) is not None:
                cols[1][k] = G[k]
    for x in F.get("b") or []:
        c = next((c for c in cols if c["end"] and yr(c["end"]) == x[0]), None)
        if not c or c is cols[0]:
            continue
        if c is cols[1]:
            if c.get("opi") is None:
                c["opi"] = x[2]
            continue
        for k, v in zip(("rev", "opi", "ni"), x[1:4]):
            if v is not None:
                c[k] = v
    return cols


def table_chart(ev, F, G=None):
    cols = ann_cols(F, G)
    if not cols:
        return None
    k = F.get("k") or 0
    won = lambda v: None if v is None or not k else f"{round(v * k / 1e8):,}"   # noqa: E731

    def ratio(num, den):
        if not den or den <= 0:
            return "적자" if den and den < 0 else None
        return f"{num / den:.1f}"
    rows = [
        ("매출", [won(c.get("rev")) for c in cols]),
        ("영업이익", [won(c.get("opi")) for c in cols]),
        ("순이익", [won(c.get("ni")) for c in cols]),
        ("PER", [None if c.get("ni") is None else ratio(F["m"], c["ni"]) for c in cols]),
        ("PBR", [ratio(F["m"], F["e"]) if i == 0 and F.get("e") else None for i, c in enumerate(cols)]),
        ("PSR", [None if c.get("rev") is None else ratio(F["m"], c["rev"]) for c in cols]),
    ]
    W, H = 10.5, 5.2
    fig = plt.figure(figsize=(W, H), dpi=110)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    x0, cw, lw = 0.35, 2.15, 1.55
    top, hh, rh = 0.95, 0.85, 0.55
    ax.text(x0, 0.5, f"[{ev['code']}] {ev['name']}  연간 실적·컨센서스", color=FG, fontsize=15,
            fontweight="bold", va="center")
    # 머리줄
    ax.add_patch(plt.Rectangle((x0, top), lw + cw * 4, hh, color="#2f4a6b", lw=0))
    ax.add_patch(plt.Rectangle((x0 + lw + cw, top), cw, hh, color="#3d6390", lw=0))
    ax.text(x0 + 0.12, top + hh / 2, "단위: 억 원 환산", color=MUTE, fontsize=9, va="center")
    for i, c in enumerate(cols):
        cx = x0 + lw + cw * (i + 0.5)
        ax.text(cx, top + hh * 0.36, c["name"], color=FG, fontsize=12, fontweight="bold", ha="center", va="center")
        ax.text(cx, top + hh * 0.74, (c["end"] or "")[2:7].replace("-", "."), color="#b8c7d6",
                fontsize=9, ha="center", va="center")
    y = top + hh
    for r, (lab, vals) in enumerate(rows):
        per = lab == "PER"
        if per:
            ax.add_patch(plt.Rectangle((x0, y), lw + cw * 4, rh, color="#4a3d1c", lw=0))
            ax.add_patch(plt.Rectangle((x0, y), 0.05, rh, color=LAST, lw=0))
        else:
            ax.add_patch(plt.Rectangle((x0 + lw + cw, y), cw, rh, color="#263a52", lw=0))
        ax.plot([x0, x0 + lw + cw * 4], [y + rh, y + rh], color=GRID, lw=0.7)
        ax.text(x0 + 0.15, y + rh / 2, lab, color=LAST if per else FG if r < 3 else MUTE,
                fontsize=12, fontweight="bold" if per else None, va="center")
        for i, v in enumerate(vals):
            ax.text(x0 + lw + cw * (i + 1) - 0.18, y + rh / 2, v if v is not None else "—",
                    color=LAST if per else (FG if v is not None else "#5d6f82"),
                    fontsize=12.5, fontweight="bold" if i == 1 or per else None, ha="right", va="center")
        y += rh
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ── 2. 분기 매출 · 영업이익률 / 성장률 ─────────────────────────────────
def rev_chart(ev, pts, freq):
    sign, div, suf = _unit(ev["cur"])
    pts = pts[-22:] if freq != "H" else pts[-12:]
    yoy = _yoy(pts, freq)
    labs = [p[0] for p in pts]
    vals = [(p[1] or 0) / div for p in pts]
    opm = [(p[2] / p[1] * 100) if len(p) > 2 and p[2] is not None and p[1] else None for p in pts]
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(13, 8.2), dpi=110, gridspec_kw={"height_ratios": [2.3, 1]})
    fig.patch.set_facecolor(BG)
    _style(ax)
    _style(bx)
    unit = f"{sign} {suf}".strip()
    fig.suptitle(f"[{ev['code']}] {ev['name']}  분기 매출 · 영업이익률", color=FG, fontsize=16,
                 fontweight="bold", x=0.02, ha="left", y=0.985)
    fig.text(0.98, 0.962, f"단위: {unit} · 최신 {labs[-1]}", color=LAST, fontsize=11, ha="right")
    x = range(len(vals))
    cols = [BAR] * len(vals)
    cols[-1] = LAST
    ax.bar(x, vals, color=cols, width=0.64)
    top = max(vals) if vals else 1
    for i, v in enumerate(vals):
        ax.text(i, v + top * 0.012, _num(v), ha="center", va="bottom", color=FG, fontsize=8,
                fontweight="bold" if i == len(vals) - 1 else None, path_effects=OUTLINE)
    ax.set_xticks(list(x), labs)
    ax.set_ylim(0, top * 1.75)                             # 막대는 아래 57%, 위는 이익률 선 자리
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    xs = [i for i, v in enumerate(opm) if v is not None]
    if len(xs) >= 2:
        a2 = ax.twinx()
        ys = [opm[i] for i in xs]
        a2.plot(xs, ys, color=LINE, linewidth=2.2, marker="o", markersize=5,
                markerfacecolor=LINE, markeredgecolor="white")
        for xx, yy in zip(xs, ys):
            a2.text(xx, yy + 0.4, f"{yy:.0f}%", color=LINE, fontsize=8.5, ha="center", va="bottom",
                    fontweight="bold", path_effects=OUTLINE)
        lo, hi = min(ys), max(ys)
        span = max(hi - lo, 4)
        a2.set_ylim(lo - span * 1.9, hi + span * 0.25)       # 선은 막대 위 빈 띠에 눕힌다
        a2.tick_params(colors=LINE, labelsize=9)
        a2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
        for s in a2.spines.values():
            s.set_visible(False)
        ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=BAR), plt.Line2D([], [], color=LINE, lw=2)],
                  labels=["매출 (왼쪽)", "영업이익률 (오른쪽)"], loc="upper left", fontsize=9, frameon=False,
                  labelcolor=FG)
    xs = [i for i, y in enumerate(yoy) if y is not None]
    bx.set_title("매출 성장률 (YoY)", color=FG, fontsize=12, loc="left", fontweight="bold")
    if xs:
        ys = [yoy[i] for i in xs]
        bx.plot(xs, ys, color="#7fb3e6", linewidth=2, marker="o", markersize=4)
        for xx, yy in zip(xs, ys):
            bx.text(xx, yy, f"{yy:+.0f}%".replace("+0%", "0%").replace("-0%", "0%"), color=LAST if xx == len(pts) - 1 else "#9cc6ee", fontsize=8.5,
                    ha="center", va="bottom", fontweight="bold", path_effects=OUTLINE)
        lo, hi = min(ys + [0]), max(ys + [0])
        span = max(hi - lo, 5)
        bx.set_ylim(lo - span * 0.15, hi + span * 0.3)
        bx.axhline(0, color=MUTE, linewidth=0.8)
    bx.set_xlim(-0.6, len(pts) - 0.4)
    ax.set_xlim(-0.6, len(pts) - 0.4)
    bx.set_xticks(list(x), labs)
    bx.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    return _png(fig)


# ── 3. 가이던스 변천(일본) ──────────────────────────────────────────────
STAGES = ["최초", "1Q", "2Q", "3Q"]


def guide_chart(ev, gh):
    """gh: {"YYYY-MM": {"최초": [rev, opi], "1Q": …, "실제": [rev, opi]}} — 회계연도 끝 달 순."""
    fys = sorted(gh)[-5:]
    have = [f for f in fys if sum(1 for s in STAGES if s in gh[f]) >= 1]
    if not have or sum(len(gh[f]) for f in have) < 3:
        return None
    fig, axes = plt.subplots(2, 1, figsize=(13, 9), dpi=110)
    fig.patch.set_facecolor(BG)
    fig.suptitle(f"[{ev['code']}] {ev['name']}  연도별 가이던스 변천", color=FG, fontsize=16,
                 fontweight="bold", x=0.02, ha="left", y=0.99)
    fig.text(0.98, 0.968, "단위 억엔 · 회사 연간 전망(결산단신)", color=MUTE, fontsize=10, ha="right")
    last = None
    for f in have:
        for s in STAGES:
            if s in gh[f]:
                last = (f, s)
    for k, (ax, title) in enumerate(zip(axes, ("매출 가이던스", "영업이익 가이던스"))):
        _style(ax)
        ax.set_title(title, color=FG, fontsize=12.5, loc="left", fontweight="bold")
        xs, ticks, groups = [], [], []
        x = 0
        top = 0
        for f in have:
            g0 = x
            stg = STAGES
            if f == have[-1] and "실제" not in gh[f]:     # 올해는 아직 안 온 분기 자리를 비워 두지 않는다
                got = [s for s in STAGES if s in gh[f]]
                stg = STAGES[:STAGES.index(got[-1]) + 1]
            for s in stg + ["실제"]:
                v = gh[f].get(s)
                if s == "실제" and not v:
                    continue
                val = v[k] / 1e8 if v and v[k] is not None else None
                if s in stg or val is not None:
                    color = "#d9dde2" if s == "실제" else (LAST if (f, s) == last else BAR)
                    if val is not None:
                        ax.bar(x, val, color=color, width=0.8)
                        ax.text(x, val, f"{val:,.0f}", ha="center", va="bottom", fontsize=8,
                                color=LAST if (f, s) == last else FG, fontweight="bold", path_effects=OUTLINE)
                        top = max(top, val)
                    else:
                        ax.text(x, 0, "–", ha="center", va="bottom", color=MUTE, fontsize=8)
                    xs.append(x)
                    ticks.append(s)
                    x += 1
            groups.append(((g0 + x - 1) / 2, f"FY{f[2:4]}"))
            x += 0.8
        ax.set_xticks(xs, ticks, fontsize=8)
        for gx, gl in groups:
            ax.text(gx, -0.14, gl, transform=ax.get_xaxis_transform(), ha="center", va="top",
                    color=FG, fontsize=11, fontweight="bold")
        ax.set_ylim(0, (top or 1) * 1.15)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in (BAR, "#d9dde2", LAST)],
               labels=["가이던스", "실제", "최신"], loc="upper left", bbox_to_anchor=(0.02, 0.955),
               ncol=3, fontsize=9.5, frameon=False, labelcolor=FG)
    fig.tight_layout(rect=(0, 0.02, 1, 0.93), h_pad=3.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ── 4. 매출 breakdown ───────────────────────────────────────────────────
def _seg_name(n):
    n = re.sub(r"(?i)\s*segment(s)?$", "", n or "").strip()
    return n[:26]


def seg_chart(ev, seg, axis=""):
    names, pts = seg.get("names") or [], seg.get("pts") or []
    pts = pts[-20:]
    if len(names) < 2 or len(pts) < 3:
        return None
    sign, div, suf = _unit(ev["cur"])
    # 마지막 분기 큰 순으로 여섯까지만 — 선이 많으면 아무것도 안 보인다.
    last = pts[-1]
    order = sorted(range(len(names)), key=lambda j: -((last[j + 1] or 0) if j + 1 < len(last) else 0))[:6]
    fig, ax = _fig(f"[{ev['code']}] {ev['name']}  매출 breakdown" + (f" ({axis})" if axis else ""),
                   f"단위: {sign} {suf}")
    x = range(len(pts))
    for n, j in enumerate(order):
        ys = [((p[j + 1] or 0) / div) if j + 1 < len(p) and p[j + 1] is not None else None for p in pts]
        xx = [i for i, v in enumerate(ys) if v is not None]
        yy = [ys[i] for i in xx]
        c = SEGC[n % len(SEGC)]
        ax.plot(xx, yy, color=c, linewidth=2.2, marker="o", markersize=4, label=_seg_name(names[j]))
        for a, b in zip(xx, yy):
            ax.text(a, b, _num(b), color=c, fontsize=7.5, ha="center", va="bottom", fontweight="bold",
                    path_effects=OUTLINE)
    ax.set_xticks(list(x), [p[0] for p in pts], rotation=0)
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), fontsize=9.5, frameon=False,
                    ncol=min(4, len(order)))
    for t in leg.get_texts():
        t.set_color(FG)
    return _png(fig)


def render(ev, pts, seg, freq, F=None, G=None, gh=None):
    """보낼 차례: 표 → 실적 → 가이던스 → breakdown. 못 그리는 것은 건너뛴다."""
    out = []
    for f in (lambda: F and table_chart(ev, F, G),
              lambda: len(pts) >= 2 and rev_chart(ev, pts, freq),
              lambda: gh and guide_chart(ev, gh),
              lambda: seg and seg_chart(ev, seg)):
        try:
            png = f()
        except Exception:                                    # noqa: BLE001
            import traceback
            traceback.print_exc(limit=3)
            png = None
        if png:
            out.append(png)
    return out
