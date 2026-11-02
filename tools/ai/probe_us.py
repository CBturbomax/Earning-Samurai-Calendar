"""probe 용 — 실시간 서버의 오늘 미국 실적 처리 상태."""
import json, sys, urllib.request
U = "https://earning-samurai-calendar-production.up.railway.app"
def g(p):
    with urllib.request.urlopen(U + p, timeout=30) as r:
        return r.read().decode("utf-8", "replace")
day = sys.argv[1]
print("== log us")
for l in g("/api/log").splitlines():
    if any(k in l for k in ("실적 us:", "표 읽기", "보도자료", "8-K", "완성 기다림")):
        print(l)
print("== sent us")
for r in json.loads(g(f"/api/sent?day={day}"))["rows"]:
    if r[0].startswith("us:"):
        print(r)
print("== live us")
d = json.loads(g("/api/live"))
ev = d.get("events") if isinstance(d, dict) else d
for e in (ev or [])[:80]:
    if e.get("mkt") == "us":
        print(e.get("key"), e.get("kstDate"), e.get("kstTime"), "point" if e.get("point") else "NOPOINT", e.get("src"), e.get("tgWhy"))
