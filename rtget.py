"""실시간 서버(Railway)에서 JSON 받기 — gzip 으로 달라고 한다.

빌드·수집기가 몇 분마다 /api/jpfinraw·/api/guide(각 10MB)·/api/jpsegraw(12MB)를 압축 없이 받아 가
Railway 의 밖으로 나가는 양이 한 달 1,096GB · 55달러였다(11/28). gzip 이면 6분의 1 안팎.
서버가 압축을 안 해 줘도(옛 판) 그대로 읽는다."""
import gzip
import json
import urllib.request


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "es-build", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            b = gzip.decompress(b)
    return json.loads(b)
