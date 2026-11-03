"""Decode the TOC icon formats WoW uses (BLP2, TGA) and write a small PNG.

Pure Python: icons are tiny (usually 64x64), so speed does not matter.
An image is a (width, height, rgba bytes) tuple.
"""

import struct
import zlib


class ImageError(Exception):
    pass


MAX_SIDE = 1024


# ───────────────────────── TGA ─────────────────────────

def decode_tga(data):
    if len(data) < 18:
        raise ImageError("short TGA")
    id_len, cmap_type, img_type = data[0], data[1], data[2]
    cmap_len = struct.unpack("<H", data[5:7])[0]
    cmap_bits = data[7]
    w, h = struct.unpack("<HH", data[12:16])
    bpp, desc = data[16], data[17]
    if img_type not in (2, 10) or bpp not in (24, 32) or not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise ImageError("unsupported TGA type %d/%d bpp" % (img_type, bpp))
    p = 18 + id_len + (cmap_len * ((cmap_bits + 7) // 8) if cmap_type else 0)
    step = bpp // 8
    n = w * h
    px = bytearray()
    if img_type == 2:
        raw = data[p:p + n * step]
        if len(raw) < n * step:
            raise ImageError("truncated TGA")
        for i in range(n):
            b, g, r = raw[i * step:i * step + 3]
            a = raw[i * step + 3] if step == 4 else 255
            px += bytes((r, g, b, a))
    else:
        while len(px) < n * 4:
            if p >= len(data):
                raise ImageError("truncated RLE TGA")
            head = data[p]
            p += 1
            count = (head & 0x7F) + 1
            if head & 0x80:
                b, g, r = data[p:p + 3]
                a = data[p + 3] if step == 4 else 255
                p += step
                px += bytes((r, g, b, a)) * count
            else:
                for _ in range(count):
                    b, g, r = data[p:p + 3]
                    a = data[p + 3] if step == 4 else 255
                    p += step
                    px += bytes((r, g, b, a))
        px = px[:n * 4]
    if not desc & 0x20:   # stored bottom-up
        rows = [px[y * w * 4:(y + 1) * w * 4] for y in range(h)]
        px = bytearray(b"".join(reversed(rows)))
    return w, h, bytes(px)


# ───────────────────────── BLP2 ─────────────────────────

def decode_blp(data):
    if data[:4] != b"BLP2" or len(data) < 148:
        raise ImageError("not a BLP2 file")
    compression, alpha_depth, alpha_type, _mips = data[8], data[9], data[10], data[11]
    w, h = struct.unpack("<II", data[12:20])
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise ImageError("bad BLP size")
    offset = struct.unpack("<I", data[20:24])[0]
    size = struct.unpack("<I", data[84:88])[0]
    mip = data[offset:offset + size]
    if compression == 1:
        palette = data[148:148 + 1024]
        return w, h, _blp_palette(mip, palette, w, h, alpha_depth)
    if compression == 2:
        if alpha_type == 0 or alpha_depth <= 1:
            return w, h, _dxt(mip, w, h, 1, alpha_depth)
        return w, h, _dxt(mip, w, h, 3 if alpha_type == 1 else 5, alpha_depth)
    if compression == 3:
        px = bytearray()
        for i in range(w * h):
            b, g, r, a = mip[i * 4:i * 4 + 4]
            px += bytes((r, g, b, a))
        return w, h, bytes(px)
    raise ImageError("unsupported BLP compression %d" % compression)


def _blp_palette(mip, pal, w, h, alpha_depth):
    n = w * h
    if len(mip) < n:
        raise ImageError("truncated BLP")
    px = bytearray()
    alpha = mip[n:]
    for i in range(n):
        idx = mip[i] * 4
        b, g, r = pal[idx], pal[idx + 1], pal[idx + 2]
        if alpha_depth == 8:
            a = alpha[i] if i < len(alpha) else 255
        elif alpha_depth == 4:
            v = alpha[i // 2] if i // 2 < len(alpha) else 0xFF
            a = ((v >> 4) if i & 1 else (v & 0x0F)) * 17
        elif alpha_depth == 1:
            v = alpha[i // 8] if i // 8 < len(alpha) else 0xFF
            a = 255 if (v >> (i % 8)) & 1 else 0
        else:
            a = 255
        px += bytes((r, g, b, a))
    return bytes(px)


def _rgb565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def _color_block(block, dxt1_alpha):
    c0, c1, bits = struct.unpack("<HHI", block)
    a, b = _rgb565(c0), _rgb565(c1)
    if c0 > c1 or not dxt1_alpha:
        cols = [a + (255,), b + (255,),
                tuple((2 * x + y) // 3 for x, y in zip(a, b)) + (255,),
                tuple((x + 2 * y) // 3 for x, y in zip(a, b)) + (255,)]
    else:
        cols = [a + (255,), b + (255,), tuple((x + y) // 2 for x, y in zip(a, b)) + (255,), (0, 0, 0, 0)]
    return [cols[(bits >> (2 * i)) & 3] for i in range(16)]


def _dxt(mip, w, h, kind, alpha_depth):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bsize = 8 if kind == 1 else 16
    if len(mip) < bw * bh * bsize:
        raise ImageError("truncated DXT data")
    out = bytearray(w * h * 4)
    p = 0
    for by in range(bh):
        for bx in range(bw):
            alphas = None
            if kind == 3:
                bits = int.from_bytes(mip[p:p + 8], "little")
                alphas = [((bits >> (4 * i)) & 15) * 17 for i in range(16)]
                p += 8
            elif kind == 5:
                a0, a1 = mip[p], mip[p + 1]
                bits = int.from_bytes(mip[p + 2:p + 8], "little")
                if a0 > a1:
                    table = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
                else:
                    table = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
                alphas = [table[(bits >> (3 * i)) & 7] for i in range(16)]
                p += 8
            cols = _color_block(mip[p:p + 8], kind == 1 and alpha_depth > 0)
            p += 8
            for i in range(16):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                if x < w and y < h:
                    r, g, b, a = cols[i]
                    if alphas is not None:
                        a = alphas[i]
                    q = (y * w + x) * 4
                    out[q:q + 4] = bytes((r, g, b, a))
    return bytes(out)


# ───────────────────────── output ─────────────────────────

def fit(img, side=128):
    """Scale down with a box filter so the longer side is at most `side`."""
    w, h, px = img
    if max(w, h) <= side:
        return img
    scale = max(w, h) / side
    nw, nh = max(1, round(w / scale)), max(1, round(h / scale))
    out = bytearray()
    for y in range(nh):
        y0, y1 = int(y * h / nh), max(int(y * h / nh) + 1, int((y + 1) * h / nh))
        for x in range(nw):
            x0, x1 = int(x * w / nw), max(int(x * w / nw) + 1, int((x + 1) * w / nw))
            acc = [0, 0, 0, 0]
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    q = (yy * w + xx) * 4
                    for k in range(4):
                        acc[k] += px[q + k]
            cnt = (y1 - y0) * (x1 - x0)
            out += bytes(v // cnt for v in acc)
    return nw, nh, bytes(out)


def is_blank(img):
    """True when every pixel is transparent or the image is one flat colour."""
    w, h, px = img
    if all(px[i] == 0 for i in range(3, len(px), 4)):
        return True
    first = px[:4]
    return all(px[i:i + 4] == first for i in range(0, len(px), 4))


def png(img):
    w, h, px = img
    raw = b"".join(b"\x00" + px[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def decode(name, data):
    n = name.lower()
    if n.endswith(".blp"):
        return decode_blp(data)
    if n.endswith(".tga"):
        return decode_tga(data)
    raise ImageError("unsupported icon format: " + name)
