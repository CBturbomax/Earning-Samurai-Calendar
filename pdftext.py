# -*- coding: utf-8 -*-
"""PDF 에서 글자를 꺼낸다 — 표준 라이브러리만.

**왜 직접 쓰나.** 일본 월매출(月次)의 알맹이는 TDnet 첨부 PDF 안에만 있다.
한때 "CID 인코딩이라 표준 라이브러리로는 못 읽는다"고 적어 두었는데, 그건
**압축을 안 풀고 원문 바이트에서 괄호 문자열만 긁어 본 것**이었다. 실제로는
스트림이 대개 Flate 라 zlib 으로 풀리고, 폰트마다 `/ToUnicode` CMap 이 붙어
있어 CID -> 유니코드 표가 파일 안에 함께 들어 있다. 그 표를 읽으면 된다.

하는 일은 딱 넷이다.

  1) `N G obj … endobj` 를 훑어 객체를 모은다. 객체 스트림(ObjStm)도 푼다 —
     요즘 PDF 는 폰트 사전을 거기 넣어서, 안 풀면 ToUnicode 를 못 찾는다.
  2) 폰트마다 `/ToUnicode` 스트림을 읽어 `beginbfchar`/`beginbfrange` 로
     CID -> 문자 표를 만든다. 두 바이트 CID(Identity-H)와 한 바이트를 가른다.
  3) 내용 스트림에서 `Tf`(폰트 바꾸기)·`Tj`/`TJ`/`'`/`"`(글자 찍기)·
     `Td`/`TD`/`Tm`/`T*`(자리 옮기기)만 본다.
  4) y 좌표로 줄을 묶고 x 로 정렬해 **표의 줄 모양을 살려서** 돌려준다 —
     월매출 표는 「既存店 103.4% 全店 107.1%」처럼 한 줄에 값이 늘어서므로
     줄이 뒤섞이면 어느 수치인지 알 수 없게 된다.

**못 읽는 PDF 가 있다.** `/ToUnicode` 없이 `UniJIS-UCS2-H` 같은 미리 정해진
CMap 만 쓰는 파일은 표가 우리 손에 없어 글자를 되살릴 수 없고, 스캔 이미지로
낸 공시는 애초에 글자가 없다. 그런 건 **못 읽었다고 답한다** — 지어내지 않는다.
"""
import re
import zlib

__all__ = ["extract_text", "extract_lines"]

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b", re.S)
FILTER_RE = re.compile(rb"/Filter\s*(/\w+|\[[^\]]*\])")
TOUNI_RE = re.compile(rb"/ToUnicode\s+(\d+)\s+\d+\s+R")
FONT_RES_RE = re.compile(rb"/Font\s*<<(.*?)>>", re.S)
FONT_ENT_RE = re.compile(rb"/([A-Za-z0-9#_.+-]+)\s+(\d+)\s+\d+\s+R")
CONTENTS_RE = re.compile(rb"/Contents\s*(?:(\d+)\s+\d+\s+R|\[([^\]]*)\])")
REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
RES_REF_RE = re.compile(rb"/Resources\s+(\d+)\s+\d+\s+R")
ONE_BYTE_RE = re.compile(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>")


def _inflate(raw: bytes):
    """Flate 를 푼다. 끝이 잘린 스트림도 받아준다(받은 만큼 쓴다)."""
    try:
        return zlib.decompress(raw)
    except zlib.error:
        pass
    d = zlib.decompressobj()
    try:
        out = d.decompress(raw)
        return out + d.flush()
    except zlib.error:
        return b""


def _objects(buf: bytes):
    """번호 -> (사전 바이트, 푼 스트림 바이트 또는 None)."""
    objs = {}
    for m in OBJ_RE.finditer(buf):
        num = int(m.group(1))
        end = buf.find(b"endobj", m.end())
        body = buf[m.end():end if end > 0 else len(buf)]
        s = body.find(b"stream")
        if s < 0:
            objs[num] = (body, None)
            continue
        head = body[:s]
        p = s + 6
        if body[p:p + 2] == b"\r\n":
            p += 2
        elif body[p:p + 1] in (b"\n", b"\r"):
            p += 1
        e = body.find(b"endstream", p)
        raw = body[p:e if e > 0 else len(body)]
        f = FILTER_RE.search(head)
        data = _inflate(raw) if (f and b"Flate" in f.group(1)) else raw
        objs[num] = (head, data)
    return objs


def _expand_objstm(objs: dict):
    """객체 스트림을 풀어 안에 든 객체를 바깥과 같은 자리에 올린다.
    안 풀면 폰트 사전이 통째로 안 보여 ToUnicode 를 못 찾는다."""
    add = {}
    for head, data in list(objs.values()):
        if not data or b"/ObjStm" not in head:
            continue
        m = re.search(rb"/N\s+(\d+)", head)
        f = re.search(rb"/First\s+(\d+)", head)
        if not (m and f):
            continue
        n, first = int(m.group(1)), int(f.group(1))
        nums = data[:first].split()
        for i in range(n):
            try:
                num = int(nums[2 * i])
                off = int(nums[2 * i + 1])
            except (IndexError, ValueError):
                break
            nxt = int(nums[2 * i + 3]) if 2 * i + 3 < len(nums) else len(data) - first
            add[num] = (data[first + off:first + nxt], None)
    for k, v in add.items():
        objs.setdefault(k, v)
    return objs


def _parse_cmap(data: bytes):
    """ToUnicode CMap -> (표, 코드 바이트 수)."""
    if not data:
        return {}, 2
    nbytes = 2
    m = ONE_BYTE_RE.search(data)
    if m:
        nbytes = max(1, len(m.group(1)) // 2)
    table = {}

    def uni(h: bytes) -> str:
        try:
            b = bytes.fromhex(h.decode("ascii"))
        except ValueError:
            return ""
        # UTF-16BE. 서러게이트 쌍도 그대로 풀린다.
        return b.decode("utf-16-be", "ignore")

    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            table[int(src, 16)] = uni(dst)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        # <lo> <hi> <dst>  또는  <lo> <hi> [<d1> <d2> …]
        for lo, hi, rest in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])",
                blk, re.S):
            a, b = int(lo, 16), int(hi, 16)
            if b - a > 65535:
                continue
            if rest.startswith(b"["):
                for i, d in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", rest)):
                    table[a + i] = uni(d)
            else:
                d = rest[1:-1]
                base = uni(d)
                if not base:
                    continue
                first = ord(base[-1])
                for i in range(b - a + 1):
                    table[a + i] = base[:-1] + chr(first + i)
    return table, nbytes


def _fonts(objs: dict):
    """폰트 객체 번호 -> (CID 표, 코드 바이트 수)."""
    out = {}
    for num, (head, _) in objs.items():
        m = TOUNI_RE.search(head)
        if not m:
            continue
        tgt = objs.get(int(m.group(1)))
        if tgt:
            out[num] = _parse_cmap(tgt[1])
    return out


def _resources(objs: dict, page_head: bytes):
    """페이지의 /Resources 에서 이름 -> 폰트객체번호."""
    res = page_head
    m = RES_REF_RE.search(page_head)
    if m:
        got = objs.get(int(m.group(1)))
        if got:
            res = got[0]
    fm = FONT_RES_RE.search(res)
    if not fm:
        return {}
    return {name: int(ref) for name, ref in FONT_ENT_RE.findall(fm.group(1))}


def _unescape(s: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):          # 역슬래시
            nxt = s[i + 1]
            if nxt in b"nrtbf":
                out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[nxt])
                i += 2
            elif 0x30 <= nxt <= 0x37:
                j = i + 1
                oct_ = b""
                while j < len(s) and len(oct_) < 3 and 0x30 <= s[j] <= 0x37:
                    oct_ += s[j:j + 1]
                    j += 1
                out.append(int(oct_, 8) & 0xFF)
                i = j
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _decode(raw: bytes, cmap, nbytes) -> str:
    if not cmap:
        # 표가 없으면 한 바이트 라틴으로 읽어 본다 — 숫자·영문은 이걸로도 나온다.
        return raw.decode("latin-1", "ignore")
    out = []
    step = max(1, nbytes)
    for i in range(0, len(raw) - step + 1, step):
        code = int.from_bytes(raw[i:i + step], "big")
        out.append(cmap.get(code, ""))
    return "".join(out)


TOKEN_RE = re.compile(
    rb"<([0-9A-Fa-f\s]*)>|\((?:\\.|[^\\()])*\)|"
    rb"(-?\d+\.?\d*)|(/[A-Za-z0-9#_.+-]+)|(\[|\]|[A-Za-z'\"*]+)", re.S)


def _run(content: bytes, fonts: dict, res: dict):
    """내용 스트림 -> [(y, x, 글자)]"""
    out = []
    cmap, nb = {}, 2
    tx = ty = 0.0
    lead = 0.0
    stack = []
    for m in TOKEN_RE.finditer(content):
        hexs, num, name, op = m.group(1), m.group(2), m.group(3), m.group(4)
        tok = m.group(0)
        if hexs is not None:
            stack.append(("s", bytes.fromhex(re.sub(rb"\s", b"", hexs).decode("ascii"))
                          if len(re.sub(rb"\s", b"", hexs)) % 2 == 0 else b""))
        elif tok.startswith(b"("):
            stack.append(("s", _unescape(tok[1:-1])))
        elif num is not None:
            stack.append(("n", float(num)))
        elif name is not None:
            stack.append(("k", name))
        else:
            o = op.decode("latin-1")
            if o == "Tf":
                for kind, v in reversed(stack):
                    if kind == "k":
                        cmap, nb = fonts.get(res.get(v[1:].encode("latin-1"), -1),
                                             ({}, 2))
                        break
            elif o in ("Td", "TD"):
                ns = [v for k, v in stack if k == "n"]
                if len(ns) >= 2:
                    tx += ns[-2]
                    ty += ns[-1]
                if o == "TD" and len(ns) >= 1:
                    lead = -ns[-1]
            elif o == "Tm":
                ns = [v for k, v in stack if k == "n"]
                if len(ns) >= 6:
                    tx, ty = ns[-2], ns[-1]
            elif o == "TL":
                ns = [v for k, v in stack if k == "n"]
                if ns:
                    lead = ns[-1]
            elif o == "T*":
                ty -= lead
            elif o in ("Tj", "TJ", "'", '"'):
                if o in ("'", '"'):
                    ty -= lead
                txt = "".join(_decode(v, cmap, nb) for k, v in stack if k == "s")
                if txt.strip():
                    out.append((round(ty, 1), round(tx, 1), txt))
            elif o == "BT":
                tx = ty = 0.0
            stack = []
    return out


def extract_lines(data: bytes, max_pages: int = 40):
    """PDF 바이트 -> 줄 목록. 표의 가로줄을 살린다."""
    objs = _expand_objstm(_objects(data))
    fonts = _fonts(objs)
    pages = [(num, head) for num, (head, _) in objs.items()
             if b"/Type" in head and b"/Page" in head and b"/Pages" not in head]
    pages.sort()
    lines = []
    for num, head in pages[:max_pages]:
        res = _resources(objs, head)
        cm = CONTENTS_RE.search(head)
        if not cm:
            continue
        refs = ([int(cm.group(1))] if cm.group(1)
                else [int(x) for x in REF_RE.findall(cm.group(2) or b"")])
        content = b"".join((objs.get(r) or (b"", b""))[1] or b"" for r in refs)
        if not content:
            continue
        rows = {}
        for y, x, txt in _run(content, fonts, res):
            rows.setdefault(y, []).append((x, txt))
        for y in sorted(rows, reverse=True):
            line = " ".join(t for _, t in sorted(rows[y]))
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
    return lines


def extract_text(data: bytes, max_pages: int = 40) -> str:
    return "\n".join(extract_lines(data, max_pages))


if __name__ == "__main__":
    import sys
    print(extract_text(open(sys.argv[1], "rb").read()))
