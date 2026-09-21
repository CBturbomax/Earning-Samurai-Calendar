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

import pdfcrypt

__all__ = ["extract_text", "extract_lines", "is_encrypted"]

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b", re.S)
FILTER_RE = re.compile(rb"/Filter\s*(/\w+|\[[^\]]*\])")
TOUNI_RE = re.compile(rb"/ToUnicode\s+(\d+)\s+\d+\s+R")
FONT_RES_RE = re.compile(rb"/Font\s*<<(.*?)>>", re.S)
FONT_ENT_RE = re.compile(rb"/([A-Za-z0-9#_.+-]+)\s+(\d+)\s+\d+\s+R")
CONTENTS_RE = re.compile(rb"/Contents\s*(?:(\d+)\s+\d+\s+R|\[([^\]]*)\])")
REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
RES_REF_RE = re.compile(rb"/Resources\s+(\d+)\s+\d+\s+R")
ONE_BYTE_RE = re.compile(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>")


ENC_RE = re.compile(rb"/Encrypt\s+\d+\s+\d+\s+R")


def is_encrypted(data: bytes) -> bool:
    """암호가 걸린 PDF 인가. 권한 잠금만 걸어 둔 것이 흔한데, 그래도 스트림이
    RC4/AES 로 싸여 있어 zlib 이 못 푼다. **'못 읽었다'와 '글자가 없다'를
    가르려고** 따로 본다 — 조용히 0줄로 넘기면 스캔 이미지와 구별이 안 된다."""
    return bool(ENC_RE.search(data[-4096:]) or ENC_RE.search(data[:4096]))


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


STREAM_KW = re.compile(rb"\bstream\r?\n")
LEN_RE = re.compile(rb"/Length\s+(\d+)(?!\s+\d+\s+R)")


def _objects(buf: bytes, crypt=None):
    """번호 -> (사전 바이트, 푼 스트림 바이트 또는 None).

    **`endobj` 로 잘라서는 안 된다.** 압축된 스트림 안에 그 바이트열이 그대로
    들어 있는 일이 흔해서, 그러면 몸통이 앞에서 잘려 `endstream` 을 못 찾는다.
    길이가 `/Length 1234` 로 직접 적혀 있으면 그것을 쓰고, 간접 참조라
    당장 못 읽으면 `endstream` 을 찾는다. 울타리는 **다음 객체 머리**다.
    """
    objs = {}
    # 울타리는 **다음 객체 머리가 시작하는 자리**다. 앞서 한 번 '머리의 끝에서
    # 40 을 뺀 자리'로 잡았는데, 짧은 객체에서는 그 값이 지금 객체의 시작보다
    # 앞이라 몸통이 통째로 빈 문자열이 됐다(자체 시험에서 잡혔다).
    starts = [(int(m.group(1)), int(m.group(2)), m.start(), m.end())
              for m in OBJ_RE.finditer(buf)]
    for i, (num, gen, head_at, pos) in enumerate(starts):
        fence = starts[i + 1][2] if i + 1 < len(starts) else len(buf)
        region = buf[pos:max(pos, fence)]
        sm = STREAM_KW.search(region)
        if not sm:
            end = region.find(b"endobj")
            objs[num] = (region[:end if end > 0 else len(region)], None)
            continue
        head = region[:sm.start()]
        body = region[sm.end():]
        lm = LEN_RE.search(head)
        raw = None
        if lm:
            n = int(lm.group(1))
            if n <= len(body) and body[n:n + 20].lstrip()[:9] == b"endstream":
                raw = body[:n]
        if raw is None:
            e = body.find(b"endstream")
            raw = body[:e if e > 0 else len(body)]
        # **암호가 걸렸으면 풀고 나서 압축을 푼다.** 차례가 바뀌면 zlib 이
        # 쓰레기를 받아 빈 결과를 내고, 화면에서는 '글자가 없는 공시'와
        # 구별되지 않는다.
        if crypt and b"/XRef" not in head:
            raw = pdfcrypt.decrypt(crypt[0], crypt[1], num, gen, raw)
        f = FILTER_RE.search(head)
        data = _inflate(raw) if (f and b"Flate" in f.group(1)) else raw
        objs[num] = (head, data)
    return objs


def _load(buf: bytes):
    """객체를 읽는다. 암호가 걸려 있으면 열쇠를 찾아 다시 읽는다.

    열쇠는 문서 안의 값으로 계산된다 — 암호를 깨는 것이 아니라 규격대로
    여는 것이다(`pdfcrypt`). **빈 사용자 암호**일 때만 열린다.
    """
    objs = _objects(buf)
    body, fid = pdfcrypt.encrypt_dict(buf, objs)
    if body:
        key, cfm = pdfcrypt.file_key(body, fid)
        if key:
            objs = _objects(buf, (key, cfm))
    return _expand_objstm(objs)


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


FIRSTCHAR_RE = re.compile(rb"/FirstChar\s+(\d+)")
WIDTHS_RE = re.compile(rb"/Widths\s*(?:\[([^\]]*)\]|(\d+)\s+\d+\s+R)")
DESC_RE = re.compile(rb"/DescendantFonts\s*(?:\[\s*(\d+)\s+\d+\s+R|(\d+)\s+\d+\s+R)")
DW_RE = re.compile(rb"/DW\s+(\d+)")
W_RE = re.compile(rb"/W\s*\[(.*?)\]\s*(?:/|>>)", re.S)


def _widths(objs: dict, num: int, head: bytes):
    """글자폭 표(1000 단위) 와 기본폭. **어림하면 표가 무너진다.**

    한동안 '글자 크기의 절반'으로 밀었더니 가로 자리가 어긋나, 「7月8月9月10月」
    처럼 열두 칸짜리 머리가 한 칸으로 붙어 버렸다 — 달 이름표를 못 찾으니 그
    공시가 통째로 안 읽혔다. 폰트 사전에 폭이 그대로 적혀 있으므로 읽어 쓴다.
    """
    dm = DESC_RE.search(head)
    if dm:                                   # 두 바이트 CID 폰트
        d = objs.get(int(dm.group(1) or dm.group(2)))
        if not d:
            return {}, 1000.0
        dh = d[0]
        dw = float(DW_RE.search(dh).group(1)) if DW_RE.search(dh) else 1000.0
        w = {}
        wm = W_RE.search(dh)
        if wm:
            toks = re.findall(rb"\[([^\]]*)\]|(-?\d+\.?\d*)", wm.group(1))
            flat, i = [], 0
            for arr, one in toks:
                flat.append(("a", arr) if arr else ("n", float(one)))
            while i < len(flat):
                if flat[i][0] != "n":
                    i += 1
                    continue
                c = int(flat[i][1])
                if i + 1 < len(flat) and flat[i + 1][0] == "a":
                    for j, v in enumerate(re.findall(rb"-?\d+\.?\d*", flat[i + 1][1])):
                        w[c + j] = float(v)
                    i += 2
                elif i + 2 < len(flat) and flat[i + 1][0] == "n" and flat[i + 2][0] == "n":
                    c2, val = int(flat[i + 1][1]), flat[i + 2][1]
                    if c2 - c <= 65535:
                        for k in range(c, c2 + 1):
                            w[k] = val
                    i += 3
                else:
                    i += 1
        return w, dw
    fm = WIDTHS_RE.search(head)               # 한 바이트 폰트
    if fm:
        body = fm.group(1)
        if body is None:
            got = objs.get(int(fm.group(2)))
            body = got[1] if got else b""
        first = int(FIRSTCHAR_RE.search(head).group(1)) if FIRSTCHAR_RE.search(head) else 0
        vals = [float(v) for v in re.findall(rb"-?\d+\.?\d*", body or b"")]
        return {first + i: v for i, v in enumerate(vals)}, 500.0
    return {}, 500.0


def _fonts(objs: dict):
    """폰트 객체 번호 -> (CID 표, 코드 바이트 수, 글자폭 표, 기본폭)."""
    out = {}
    for num, (head, _) in objs.items():
        if b"/Font" not in head and not TOUNI_RE.search(head):
            continue
        if b"/Type0" not in head and b"/TrueType" not in head and \
           b"/Type1" not in head and not TOUNI_RE.search(head):
            continue
        m = TOUNI_RE.search(head)
        cmap, nb = ({}, 2)
        if m:
            tgt = objs.get(int(m.group(1)))
            if tgt:
                cmap, nb = _parse_cmap(tgt[1])
        elif b"/Type0" not in head:
            nb = 1
        w, dw = _widths(objs, num, head)
        out[num] = (cmap, nb, w, dw)
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


def _decode(raw: bytes, cmap, nbytes):
    """(글자, 코드 목록). 코드는 글자폭을 더하는 데 쓴다."""
    step = max(1, nbytes)
    codes = [int.from_bytes(raw[i:i + step], "big")
             for i in range(0, len(raw) - step + 1, step)]
    if not cmap:
        # 표가 없으면 한 바이트 라틴으로 읽어 본다 — 숫자·영문은 이걸로도 나온다.
        return raw.decode("latin-1", "ignore"), list(raw)
    # **표에 없는 한 바이트 코드는 그 글자로 본다.** 표가 일부만 덮은 폰트가
    # 있어서, 없다고 빈 글자로 버리면 숫자가 통째로 사라진다(표의 값이 전부
    # 날아가 표를 못 읽는다). 두 바이트 CID 는 코드가 글자가 아니므로 안 한다.
    if step == 1:
        return ("".join(cmap.get(c) or (chr(c) if 32 <= c < 127 else "")
                        for c in codes), codes)
    return "".join(cmap.get(c, "") for c in codes), codes


TOKEN_RE = re.compile(
    rb"<([0-9A-Fa-f\s]*)>|\((?:\\.|[^\\()])*\)|"
    rb"(-?\d+\.?\d*)|(/[A-Za-z0-9#_.+-]+)|(\[|\]|[A-Za-z'\"*]+)", re.S)

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(m, n):
    """두 아핀 행렬을 곱한다(PDF 차례: m 을 n 에 이어 붙인다)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _run(content: bytes, fonts: dict, res: dict):
    """내용 스트림 -> [(y, x, 글자)] — **장치 좌표**로.

    **`cm`(좌표 변환)을 봐야 한다.** 한동안 Tm 의 y 만 썼더니, 변환이 걸린
    쪽에서 서로 다른 줄이 같은 y 로 떨어져 한 줄로 뭉쳤다(7865 가 그랬다).
    글자가 놓이는 자리는 `문자행렬 × 현재변환행렬` 이다.

    가로 자리도 대충 밀어 준다. 글자를 하나씩 따로 찍는 PDF 가 있어서, 안
    밀면 한 줄 안에서 차례가 뒤섞인다 — 정확한 글자폭(폰트 Widths)까지는
    안 읽고 글자 크기의 절반으로 어림한다. 줄 안의 **차례**만 지키면 된다.
    """
    out = []
    cmap, nb, wmap, dw = {}, 2, {}, 500.0
    size = 12.0
    ctm, tm, tlm = IDENT, IDENT, IDENT
    lead = 0.0
    gs = []
    stack = []
    for m in TOKEN_RE.finditer(content):
        hexs, num, name, op = m.group(1), m.group(2), m.group(3), m.group(4)
        tok = m.group(0)
        if hexs is not None:
            h = re.sub(rb"\s", b"", hexs)
            stack.append(("s", bytes.fromhex(h.decode("ascii"))
                          if len(h) % 2 == 0 else b""))
            continue
        if tok.startswith(b"("):
            stack.append(("s", _unescape(tok[1:-1])))
            continue
        if num is not None:
            stack.append(("n", float(num)))
            continue
        if name is not None:
            stack.append(("k", name))
            continue
        o = op.decode("latin-1")
        # **대괄호에서 쌓아둔 것을 지우면 안 된다.** `[(あ)-250(い)]TJ` 에서
        # 닫는 `]` 를 연산자로 보고 stack 을 비웠더니, 바로 뒤의 TJ 가 빈손이
        # 되어 **TJ 를 쓰는 PDF 가 통째로 0줄**이었다(열 건 중 일곱). Tj 만
        # 쓰는 공시에서는 멀쩡히 나와서 더 안 보였다.
        if o in ("[", "]"):
            continue
        ns = [v for k, v in stack if k == "n"]
        if o == "q":
            gs.append(ctm)
        elif o == "Q":
            if gs:
                ctm = gs.pop()
        elif o == "cm" and len(ns) >= 6:
            ctm = _mul(tuple(ns[-6:]), ctm)
        elif o == "BT":
            tm = tlm = IDENT
        elif o == "Tf":
            for kind, v in reversed(stack):
                if kind == "k":
                    cmap, nb, wmap, dw = fonts.get(res.get(v[1:], -1),
                                                   ({}, 2, {}, 500.0))
                    break
            if ns:
                size = abs(ns[-1]) or 12.0
        elif o == "TL" and ns:
            lead = ns[-1]
        elif o in ("Td", "TD") and len(ns) >= 2:
            if o == "TD":
                lead = -ns[-1]
            tlm = _mul((1.0, 0.0, 0.0, 1.0, ns[-2], ns[-1]), tlm)
            tm = tlm
        elif o == "Tm" and len(ns) >= 6:
            tlm = tm = tuple(ns[-6:])
        elif o == "T*":
            tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -lead), tlm)
            tm = tlm
        elif o in ("Tj", "TJ", "'", '"'):
            if o in ("'", '"'):
                tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -lead), tlm)
                tm = tlm
            txt, adv = "", 0.0
            # TJ 배열의 음수는 글자 사이를 좁히는 값이다(1/1000 em). 표에서는
            # 이것이 칸 사이의 빈틈으로 오는 일이 있어 가로 자리에 같이 넣는다.
            for kind, v in stack:
                if kind == "s":
                    t, codes = _decode(v, cmap, nb)
                    txt += t
                    adv += sum(wmap.get(c, dw) for c in codes) / 1000.0 * size
                elif kind == "n" and o == "TJ":
                    adv -= v / 1000.0 * size
            if txt.strip():
                a, b, c, d, e, f = _mul(tm, ctm)
                out.append((round(f, 1), round(e, 1), txt, size * abs(d or 1.0)))
                tm = _mul((1.0, 0.0, 0.0, 1.0, adv, 0.0), tm)
        stack = []
    return out


def extract_cells(data: bytes, max_pages: int = 40):
    """PDF 바이트 -> [[(x, 글자), …], …] — **줄이 아니라 칸**으로 돌려준다.

    월매출 공시는 거의 전부 표다. 줄로만 붙여 내면 「売上高5月6月7月8月前年同月比
    125.4%」 처럼 뭉쳐서, 그 125.4 가 어느 달의 값인지 알 수 없다 — 문장인 줄
    알고 집으면 **조용히 엉뚱한 칸**을 읽는다. 그래서 x 를 살려 둔다.

    글자 조각은 붙여 준다. 가로 자리를 글자폭으로 어림해 밀어 두었으므로 한
    낱말 안의 조각은 틈이 거의 0 이고, 표의 칸 사이는 그보다 훨씬 넓다.
    """
    objs = _load(data)
    fonts = _fonts(objs)
    pages = [(num, head) for num, (head, _) in objs.items()
             if b"/Type" in head and b"/Page" in head and b"/Pages" not in head]
    pages.sort()
    rows = []
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
        # y 로 줄을 묶는다. 같은 줄인데 글자마다 조금씩 어긋나 오므로 여유를 준다.
        buckets = []
        for y, x, txt, size in sorted(_run(content, fonts, res), key=lambda r: -r[0]):
            if buckets and abs(buckets[-1][0] - y) <= 1.5:
                buckets[-1][1].append((x, txt, size))
            else:
                buckets.append((y, [(x, txt, size)]))
        for _y, frags in buckets:
            frags.sort()
            cells, cx, cur, csz = [], None, "", 10.0
            for x, txt, size in frags:
                if cur and x - cx > max(size, 6.0) * 0.45:
                    cells.append((round(cx0, 1), cur))
                    cur = ""
                if not cur:
                    cx0 = x
                cur += txt
                cx = x + len(txt) * size * 0.5   # 다음 조각과의 틈을 재는 어림
                csz = size
            if cur:
                cells.append((round(cx0, 1), cur))
            if cells:
                rows.append(cells)
    return rows


def extract_lines(data: bytes, max_pages: int = 40):
    """PDF 바이트 -> 줄 목록. 칸을 빈칸으로 이어 붙인 것이다."""
    out = []
    for cells in extract_cells(data, max_pages):
        line = re.sub(r"\s+", " ", " ".join(t for _, t in cells)).strip()
        if line:
            out.append(line)
    return out


def extract_text(data: bytes, max_pages: int = 40) -> str:
    return "\n".join(extract_lines(data, max_pages))


if __name__ == "__main__":
    import sys
    print(extract_text(open(sys.argv[1], "rb").read()))
