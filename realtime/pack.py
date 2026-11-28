"""사이트 payload 를 회사별 JSON 조각으로 들고 있기 — 서버 메모리 850MB → 수십 MB.

서버는 사이트 payload(약 32MB JSON)를 통째로 파이썬 객체로 풀어 늘 들고 있었다. 11/28 실측 python 850MB,
Railway RAM 은 GB·월 10달러. 회사별 묶음(fin·seg·ann·geo…, 열쇠가 'us:'·'jp:'·'hk:')은 회사 하나씩 JSON 바이트로
두고 그 회사가 필요할 때만 푼다(Packed). 풀기는 자식 프로세스에서 한다 — 파이썬은 잘게 쪼갠 객체 수백 MB 를
풀어도 OS 에 잘 돌려주지 않는다. 자식이 끝나면 그 메모리는 통째로 돌아간다.

되돌리기: Railway 변수 PACK_D=0 → 예전처럼 통째로 푼다(app.History.refresh).
"""
import json
import pickle
import re
import sys
from collections.abc import Mapping

_MKT = re.compile(r"^(us|jp|hk):")
TAG = '<script id="payload" type="application/json">'


class Packed(Mapping):
    """읽기 전용 {종목: 값}. 값은 꺼낼 때마다 JSON 바이트에서 새로 푼다 — 꺼낸 것을 고쳐도 원본은 그대로."""

    __slots__ = ("_raw",)

    def __init__(self, raw):
        self._raw = raw

    def __getitem__(self, k):
        return json.loads(self._raw[k])

    def get(self, k, default=None):
        b = self._raw.get(k)
        return default if b is None else json.loads(b)

    def __contains__(self, k):
        return k in self._raw

    def __iter__(self):
        return iter(self._raw)

    def __len__(self):
        return len(self._raw)

    def keys(self):
        return self._raw.keys()


def packable(v):
    return isinstance(v, dict) and len(v) > 50 and all(_MKT.match(str(x)) for x in list(v)[:50])


def pack_payload(text):
    """index.html 글 -> {"plain": 작은 것 그대로, "packed": {이름: {종목: JSON 바이트}}}"""
    i = text.index(TAG)
    i = text.index(">", i) + 1
    D = json.loads(text[i:text.index("</script>", i)])
    plain, packed = {}, {}
    for k, v in D.items():
        if packable(v):
            packed[k] = {key: json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode() for key, x in v.items()}
        else:
            plain[k] = v
    return {"plain": plain, "packed": packed, "order": list(D)}


def unpickle(blob):
    d = pickle.loads(blob)
    return {k: Packed(d["packed"][k]) if k in d["packed"] else d["plain"][k] for k in d["order"]}   # 원래 차례 그대로


if __name__ == "__main__":                                      # 자식 프로세스: 표준입력 index.html -> 표준출력 pickle
    sys.stdout.buffer.write(pickle.dumps(pack_payload(sys.stdin.buffer.read().decode("utf-8")), protocol=4))
