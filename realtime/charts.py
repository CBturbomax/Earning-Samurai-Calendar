"""텔레그램으로 보낼 차트 — 매출·성장률 / 영업이익률 / 사업부별 매출.

회원님이 보여 준 봇 화면(짙은 남색 바탕 · 막대 위 금액 · 주황 성장률 선)과 같은 결로,
어닝사무라이 색(--a2 파랑 · --a3 주황 · --a1 빨강)을 쓴다. 마지막(이번) 분기는 노랗게.
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
    fig.suptitle(title, color=FG, fontsize=17, fontweight="bold", y=0.985)
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


def rev_chart(ev, pts, freq):
    sign, div, suf = _unit(ev["cur"])
    pts = pts[-20:] if freq != "H" else pts[-12:]
    yoy = _yoy(pts, freq)
    labs = [p[0] for p in pts]
    vals = [(p[1] or 0) / div for p in pts]
    fig, ax = _fig(f"[{ev['code']}] {ev['name']} 매출액 & 성장률", f"단위: {sign} {suf}")
    cols = [BAR] * len(vals)
    cols[-1] = LAST
    ax.bar(range(len(vals)), vals, color=cols, width=0.62)
    top = max(vals) if vals else 1
    for i, v in enumerate(vals):
        ax.text(i, v + top * 0.015, f"{sign}{v:,.0f}{suf}", ha="center", va="bottom", color=FG,
                fontsize=8.5, fontweight="bold" if i == len(vals) - 1 else None, path_effects=OUTLINE)
    ax.set_xticks(range(len(labs)), labs)
    ax.set_ylim(0, top * 1.18)
    ax.set_yticks([])
    ax2 = ax.twinx()
    xs = [i for i, y in enumerate(yoy) if y is not None]
    ys = [yoy[i] for i in xs]
    if xs:
        ax2.plot(xs, ys, color=LINE, linewidth=2.4, marker="o", markersize=6,
                 markerfacecolor="white", markeredgecolor=LINE)
        for x, y in zip(xs, ys):
            ax2.text(x, y + 0.6, f"{y:+.1f}%", color=LINE, fontsize=8.5, ha="center", va="bottom",
                     fontweight="bold", path_effects=OUTLINE)
        # 성장률 선은 막대 아래쪽 띠(아래 40%)에 눕힌다 — 막대 위 금액과 안 겹치게.
        lo, hi = min(ys), max(ys)
        span = max(hi - lo, 5)
        ax2.set_ylim(lo - span * 0.15, hi + span * 1.7)
    ax2.set_yticks([])
    for s in ax2.spines.values():
        s.set_visible(False)
    return _png(fig)


def margin_chart(ev, pts):
    pts = [p for p in pts if p[1]][-20:]
    opm = [(p[2] / p[1] * 100) if p[2] is not None else None for p in pts]
    if sum(v is not None for v in opm) < 3:
        return None
    fig, ax = _fig(f"[{ev['code']}] {ev['name']} 영업이익률", "영업이익 ÷ 매출 (%)")
    xs = [i for i, v in enumerate(opm) if v is not None]
    ys = [opm[i] for i in xs]
    ax.plot(xs, ys, color=LINE2, linewidth=2.6, marker="o", markersize=6,
            markerfacecolor="white", markeredgecolor=LINE2)
    for x, y in zip(xs, ys):
        ax.text(x, y + 0.5, f"{y:.1f}%", color=LAST if x == len(pts) - 1 else LINE2, fontsize=9,
                ha="center", va="bottom", fontweight="bold", path_effects=OUTLINE)
    ax.axhline(0, color=MUTE, linewidth=0.8)
    ax.set_xticks(range(len(pts)), [p[0] for p in pts])
    lo, hi = min(ys), max(ys)
    span = max(hi - lo, 4)
    ax.set_ylim(min(0, lo) - span * 0.25, hi + span * 0.3)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    return _png(fig)


def _seg_name(n):
    n = re.sub(r"(?i)\s*segment(s)?$", "", n or "").strip()
    return n[:28]


def seg_chart(ev, seg):
    names, pts = seg.get("names") or [], seg.get("pts") or []
    pts = pts[-20:]
    if len(names) < 2 or len(pts) < 3:
        return None
    sign, div, suf = _unit(ev["cur"])
    fig, ax = _fig(f"[{ev['code']}] {ev['name']} 사업부별 매출", f"단위: {sign} {suf}")
    bottom = [0.0] * len(pts)
    for j, nm in enumerate(names):
        vals = [((p[j + 1] or 0) / div) if j + 1 < len(p) and (p[j + 1] or 0) > 0 else 0 for p in pts]
        ax.bar(range(len(pts)), vals, bottom=bottom, color=SEGC[j % len(SEGC)], width=0.66,
               label=_seg_name(nm))
        tot = max(sum(r) for r in zip(*[bottom, vals])) or 1
        for i, v in enumerate(vals):
            if v > tot * 0.06:
                ax.text(i, bottom[i] + v / 2, f"{v:,.0f}", ha="center", va="center", color=FG, fontsize=7.5)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(range(len(pts)), [p[0] for p in pts])
    ax.set_yticks([])
    leg = ax.legend(loc="upper left", fontsize=9, frameon=False, ncol=min(4, len(names)))
    for t in leg.get_texts():
        t.set_color(FG)
    ax.set_ylim(0, max(bottom) * 1.15)
    return _png(fig)


def render(ev, pts, seg, freq):
    out = [rev_chart(ev, pts, freq)]
    m = margin_chart(ev, pts)
    if m:
        out.append(m)
    if seg:
        s = seg_chart(ev, seg)
        if s:
            out.append(s)
    return out
