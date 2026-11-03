"""Read single files from a zip on a web server with HTTP range requests.

Addon zips can be hundreds of MB; the index only needs the file list and one
or two small files from each, so it never downloads the whole archive.
"""

import struct
import urllib.request
import zlib

UA = "runeloader-index"
_TAIL = 65536 + 22   # end-of-central-directory record plus the longest zip comment


class RemoteZipError(Exception):
    pass


class RemoteZip:
    def __init__(self, url, timeout=30):
        self.url = url
        self.timeout = timeout
        self.requests = 0
        self.entries = self._read_directory()   # lower-case name -> (name, method, csize, usize, offset)

    def _range(self, start, end=None):
        rng = "bytes=%d-%s" % (start, "" if end is None else end)
        req = urllib.request.Request(self.url, headers={"User-Agent": UA, "Range": rng})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            self.requests += 1
            if r.status != 206:
                raise RemoteZipError("server ignored the range request")
            total = r.headers.get("Content-Range", "").rpartition("/")[2]
            return r.read(), int(total) if total.isdigit() else None

    def _read_directory(self):
        # Release storage refuses suffix ranges ("bytes=-N"), so learn the size first.
        _, size = self._range(0, 0)
        if not size:
            raise RemoteZipError("server did not report the file size")
        tail, _ = self._range(max(0, size - _TAIL), size - 1)
        i = tail.rfind(b"PK\x05\x06")
        if i < 0:
            raise RemoteZipError("no end of central directory")
        count, cd_size, cd_offset = struct.unpack("<HII", tail[i + 10:i + 20])
        if cd_offset == 0xFFFFFFFF or count == 0xFFFF:
            j = tail.rfind(b"PK\x06\x06")          # zip64
            if j < 0:
                raise RemoteZipError("zip64 without its record")
            count, cd_size, cd_offset = struct.unpack("<QQQ", tail[j + 32:j + 56])
        if cd_size > 8 * 1024 * 1024:
            raise RemoteZipError("central directory too large")
        start_in_tail = cd_offset - (size - len(tail))
        if 0 <= start_in_tail and start_in_tail + cd_size <= len(tail):
            cd = tail[start_in_tail:start_in_tail + cd_size]
        else:
            cd, _ = self._range(cd_offset, cd_offset + cd_size - 1)
        entries, p = {}, 0
        while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02":
            method, = struct.unpack("<H", cd[p + 10:p + 12])
            csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
            nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
            offset, = struct.unpack("<I", cd[p + 42:p + 46])
            name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace").replace("\\", "/")
            extra = cd[p + 46 + nlen:p + 46 + nlen + elen]
            if 0xFFFFFFFF in (csize, usize, offset):
                usize, csize, offset = _zip64_extra(extra, usize, csize, offset)
            entries[name.lower()] = (name, method, csize, usize, offset)
            p += 46 + nlen + elen + clen
        return entries

    def names(self):
        return [e[0] for e in self.entries.values()]

    def read(self, name, max_size=4 * 1024 * 1024):
        e = self.entries.get(name.lower().replace("\\", "/"))
        if not e:
            return None
        _, method, csize, usize, offset = e
        if usize > max_size or csize > max_size:
            raise RemoteZipError("%s is too large" % name)
        head, _ = self._range(offset, offset + 30 + 1024 + csize - 1)
        if head[:4] != b"PK\x03\x04":
            raise RemoteZipError("bad local header for " + name)
        nlen, elen = struct.unpack("<HH", head[26:30])
        start = 30 + nlen + elen
        data = head[start:start + csize]
        if len(data) < csize:
            more, _ = self._range(offset + 30 + 1024 + csize, offset + start + csize - 1)
            data += more
        if method == 0:
            return data
        if method == 8:
            return zlib.decompress(data, -15)
        raise RemoteZipError("unsupported compression %d" % method)


def _zip64_extra(extra, usize, csize, offset):
    p = 0
    while p + 4 <= len(extra):
        tag, size = struct.unpack("<HH", extra[p:p + 4])
        if tag == 1:
            vals, q = [], p + 4
            for v in (usize, csize, offset):
                if v == 0xFFFFFFFF:
                    vals.append(struct.unpack("<Q", extra[q:q + 8])[0])
                    q += 8
                else:
                    vals.append(v)
            return tuple(vals)
        p += 4 + size
    return usize, csize, offset


def top_folders(names):
    """Addon folders in a release zip: top-level folders that hold a .toc of their own."""
    out = []
    for n in names:
        parts = n.split("/")
        if len(parts) == 2 and parts[1].lower().endswith(".toc") and parts[0] not in out:
            out.append(parts[0])
    return sorted(out, key=str.lower)
