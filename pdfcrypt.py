# -*- coding: utf-8 -*-
"""암호가 걸린 PDF 를 연다 — 표준 라이브러리만.

월매출 공시 중 열넷이 암호로 잠겨 있었다. 떠 보니 **전부 표준 보안 핸들러에
빈 사용자 암호**였다(`/P -1324`). 회사가 '인쇄·편집 금지'만 걸어 둔 것이라
누구나 열어 보는 문서고, 열쇠는 문서 안의 값으로 계산된다 — 암호를 깨는 것이
아니라 **규격대로 여는 것**이다.

  AESV3 (V5/R6, AES-256)  11건
  AESV2 (V4/R4, AES-128)   3건

그래서 AES 를 직접 썼다. 복호만 있으면 될 것 같지만 R6 의 열쇠 만들기
(Algorithm 2.B)가 AES-128-CBC **암호화**를 쓰므로 양쪽 다 있어야 한다.

FIPS-197 시험 벡터로 맞춰 두었다(`python pdfcrypt.py`).
"""
import hashlib
import re
import struct

__all__ = ["file_key", "decrypt", "encrypt_dict"]

# ── AES ──────────────────────────────────────────────────────────────
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_INV = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV[_v] = _i
_INV = bytes(_INV)
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
         0x6C, 0xD8, 0xAB, 0x4D]


def _xt(a):
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _mul_slow(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xt(a)
        b >>= 1
    return r


# **곱셈을 미리 표로 만들어 둔다.** 순수 파이썬이라 갈루아 곱을 그때그때
# 돌리면 한 블록에 수천 번이 돌아 250KB 짜리 한 장에 1분이 걸렸다 — 워크플로
# 단계가 그대로 잘렸다. 여섯 개 표(2·3·9·11·13·14)면 곱셈이 조회로 바뀐다.
_M = {k: bytes(_mul_slow(a, k) for a in range(256))
      for k in (2, 3, 9, 11, 13, 14)}
_M2, _M3, _M9, _M11, _M13, _M14 = (_M[2], _M[3], _M[9], _M[11], _M[13], _M[14])


def _expand(key: bytes):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return w, nr


def _block(inp, w, nr, enc):
    s = [list(inp[i::4]) for i in range(4)]        # 열 우선 -> 행 우선

    def ark(r):
        for c in range(4):
            for rr in range(4):
                s[rr][c] ^= w[4 * r + c][rr]

    if enc:
        ark(0)
        for rnd in range(1, nr + 1):
            for rr in range(4):
                for c in range(4):
                    s[rr][c] = _SBOX[s[rr][c]]
            for rr in range(1, 4):
                s[rr] = s[rr][rr:] + s[rr][:rr]
            if rnd != nr:
                for c in range(4):
                    a = [s[rr][c] for rr in range(4)]
                    s[0][c] = _M2[a[0]] ^ _M3[a[1]] ^ a[2] ^ a[3]
                    s[1][c] = a[0] ^ _M2[a[1]] ^ _M3[a[2]] ^ a[3]
                    s[2][c] = a[0] ^ a[1] ^ _M2[a[2]] ^ _M3[a[3]]
                    s[3][c] = _M3[a[0]] ^ a[1] ^ a[2] ^ _M2[a[3]]
            ark(rnd)
    else:
        ark(nr)
        for rnd in range(nr - 1, -1, -1):
            for rr in range(1, 4):
                s[rr] = s[rr][-rr:] + s[rr][:-rr]
            for rr in range(4):
                for c in range(4):
                    s[rr][c] = _INV[s[rr][c]]
            ark(rnd)
            if rnd:
                for c in range(4):
                    a = [s[rr][c] for rr in range(4)]
                    s[0][c] = (_M14[a[0]] ^ _M11[a[1]] ^ _M13[a[2]] ^ _M9[a[3]])
                    s[1][c] = (_M9[a[0]] ^ _M14[a[1]] ^ _M11[a[2]] ^ _M13[a[3]])
                    s[2][c] = (_M13[a[0]] ^ _M9[a[1]] ^ _M14[a[2]] ^ _M11[a[3]])
                    s[3][c] = (_M11[a[0]] ^ _M13[a[1]] ^ _M9[a[2]] ^ _M14[a[3]])
    out = bytearray(16)
    for c in range(4):
        for rr in range(4):
            out[4 * c + rr] = s[rr][c]
    return bytes(out)


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    w, nr = _expand(key)
    out, prev = bytearray(), iv
    for i in range(0, len(data) - 15, 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = _block(blk, w, nr, True)
        out += prev
    return bytes(out)


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes, unpad=True) -> bytes:
    w, nr = _expand(key)
    out, prev = bytearray(), iv
    for i in range(0, len(data) - 15, 16):
        blk = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_block(blk, w, nr, False), prev))
        prev = blk
    if unpad and out:
        n = out[-1]
        if 1 <= n <= 16:
            out = out[:-n]
    return bytes(out)


# ── RC4 ──────────────────────────────────────────────────────────────
def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out, i, j = bytearray(), 0, 0
    for ch in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(ch ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


# ── /Encrypt 사전 읽기 ───────────────────────────────────────────────
PAD = bytes([
    0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41, 0x64, 0x00, 0x4E, 0x56,
    0xFF, 0xFA, 0x01, 0x08, 0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
    0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A])

ENC_REF = re.compile(rb"/Encrypt\s+(\d+)\s+\d+\s+R")
ID_RE = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]*)>", re.S)


def _pdf_string(body: bytes, name: bytes):
    """`/O(…)` 또는 `/O<hex>` 를 바이트로. 괄호 안의 이스케이프를 푼다."""
    m = re.search(re.escape(name) + rb"\s*\(", body)
    if m:
        i, depth, out = m.end(), 1, bytearray()
        while i < len(body) and depth:
            c = body[i]
            if c == 0x5C and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt in b"nrtbf":
                    out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8,
                                0x66: 12}[nxt]); i += 2; continue
                if 0x30 <= nxt <= 0x37:
                    j, o = i + 1, b""
                    while j < len(body) and len(o) < 3 and 0x30 <= body[j] <= 0x37:
                        o += body[j:j + 1]; j += 1
                    out.append(int(o, 8) & 0xFF); i = j; continue
                out.append(nxt); i += 2; continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if not depth:
                    break
            out.append(c); i += 1
        return bytes(out)
    m = re.search(re.escape(name) + rb"\s*<([0-9A-Fa-f\s]*)>", body)
    if m:
        h = re.sub(rb"\s", b"", m.group(1))
        return bytes.fromhex(h.decode("ascii")) if len(h) % 2 == 0 else b""
    return b""


def encrypt_dict(buf: bytes, objs: dict):
    """(암호 사전 바이트, 문서 ID) 또는 (None, None)."""
    m = ENC_REF.search(buf[-4096:]) or ENC_REF.search(buf)
    if not m:
        return None, None
    got = objs.get(int(m.group(1)))
    body = got[0] if got else b""
    if not body:
        o = re.search(rb"(?<![0-9])" + m.group(1) + rb"\s+\d+\s+obj(.*?)endobj",
                      buf, re.S)
        body = o.group(1) if o else b""
    idm = ID_RE.search(buf)
    fid = bytes.fromhex(idm.group(1).decode("ascii")) if idm and \
        len(idm.group(1)) % 2 == 0 else b""
    return body, fid


def _hash_2b(pw: bytes, salt: bytes, udata: bytes) -> bytes:
    """R6 의 굳힌 해시(Algorithm 2.B). AES-128-CBC **암호화**를 쓴다."""
    k = hashlib.sha256(pw + salt + udata).digest()
    i = 0
    while True:
        k1 = (pw + k + udata) * 64
        e = aes_cbc_encrypt(k[:16], k[16:32], k1)
        k = [hashlib.sha256, hashlib.sha384,
             hashlib.sha512][sum(e[:16]) % 3](e).digest()
        i += 1
        if i >= 64 and e[-1] <= i - 32:
            return k[:32]


def file_key(body: bytes, fid: bytes):
    """(열쇠, 방식) — 방식은 'AESV2'·'AESV3'·'RC4'. 못 풀면 (None, None).

    **빈 사용자 암호만 다룬다.** 진짜 암호가 걸린 문서는 열지 않는다 —
    그건 공개된 공시가 아니다.
    """
    v = int((re.search(rb"/V\s+(\d+)", body) or [0, b"0"])[1])
    r = int((re.search(rb"/R\s+(\d+)", body) or [0, b"0"])[1])
    cfm = re.search(rb"/CFM\s*/(\w+)", body)
    cfm = cfm.group(1).decode() if cfm else ("RC4" if v <= 2 else "")
    o = _pdf_string(body, b"/O")
    u = _pdf_string(body, b"/U")

    if r >= 5:                                   # AES-256
        ue = _pdf_string(body, b"/UE")
        if len(u) < 48 or len(ue) < 32:
            return None, None
        vsalt, ksalt = u[32:40], u[40:48]
        if _hash_2b(b"", vsalt, b"") != u[:32]:
            return None, None                    # 빈 암호가 아니다
        ikey = _hash_2b(b"", ksalt, b"")
        return aes_cbc_decrypt(ikey, b"\0" * 16, ue[:32], unpad=False), "AESV3"

    length = int((re.search(rb"/Length\s+(\d+)", body) or [0, b"40"])[1])
    n = max(5, min(16, length // 8))
    p = int((re.search(rb"/P\s+(-?\d+)", body) or [0, b"-1"])[1])
    h = hashlib.md5()
    h.update(PAD)
    h.update(o[:32])
    h.update(struct.pack("<i", p))
    h.update(fid)
    if r >= 4 and re.search(rb"/EncryptMetadata\s+false", body):
        h.update(b"\xff\xff\xff\xff")
    key = h.digest()
    if r >= 3:
        for _ in range(50):
            key = hashlib.md5(key[:n]).digest()
    return key[:n], (cfm or "RC4")


def decrypt(key: bytes, cfm: str, num: int, gen: int, data: bytes) -> bytes:
    """객체 하나를 푼다. AES-256 은 파일 열쇠를 그대로, 나머지는 객체마다 섞는다."""
    if not data:
        return data
    if cfm == "AESV3":
        return aes_cbc_decrypt(key, data[:16], data[16:])
    ok = hashlib.md5(key + bytes([num & 0xFF, (num >> 8) & 0xFF,
                                  (num >> 16) & 0xFF, gen & 0xFF,
                                  (gen >> 8) & 0xFF])
                     + (b"sAlT" if cfm == "AESV2" else b"")).digest()
    ok = ok[:min(len(key) + 5, 16)]
    if cfm == "AESV2":
        return aes_cbc_decrypt(ok, data[:16], data[16:])
    return rc4(ok, data)


if __name__ == "__main__":
    # FIPS-197 시험 벡터
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    for kh, ch in [
            ("000102030405060708090a0b0c0d0e0f",
             "69c4e0d86a7b0430d8cdb78070b4c55a"),
            ("000102030405060708090a0b0c0d0e0f1011121314151617",
             "dda97ca4864cdfe06eaf70a0ec0d7191"),
            ("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
             "8ea2b7ca516745bfeafc49904b496089")]:
        k = bytes.fromhex(kh)
        w, nr = _expand(k)
        got = _block(pt, w, nr, True).hex()
        back = _block(bytes.fromhex(ch), w, nr, False).hex()
        print(f"AES-{len(k)*8}: 암호화 {'OK' if got == ch else '틀림 ' + got} · "
              f"복호 {'OK' if back == pt.hex() else '틀림 ' + back}")
    print("RC4:", rc4(b"Key", b"Plaintext").hex(), "(bbf316e8d940af0ad3 이어야 한다)")
