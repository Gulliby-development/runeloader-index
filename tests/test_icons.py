import io
import struct
import unittest
import zipfile
import zlib

from indexer import icons, images, remotezip


def tga(w, h, rgba, top_down=True):
    head = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 0x28 if top_down else 0x08)
    body = b"".join(bytes((b, g, r, a)) for r, g, b, a in rgba)
    return head + body


def blp_raw(w, h, rgba):
    hdr = b"BLP2" + struct.pack("<I", 1) + bytes((3, 8, 0, 0)) + struct.pack("<II", w, h)
    offset = 148 + 1024
    body = b"".join(bytes((b, g, r, a)) for r, g, b, a in rgba)
    offs = struct.pack("<16I", offset, *([0] * 15))
    sizes = struct.pack("<16I", len(body), *([0] * 15))
    return hdr + offs + sizes + b"\0" * 1024 + body


class Images(unittest.TestCase):
    PX = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 255, 128)]

    def test_tga_top_down_and_bottom_up(self):
        self.assertEqual(images.decode_tga(tga(2, 2, self.PX))[2][:4], bytes((255, 0, 0, 255)))
        flipped = images.decode_tga(tga(2, 2, self.PX, top_down=False))[2]
        self.assertEqual(flipped[:4], bytes((0, 0, 255, 255)))   # last stored row comes first

    def test_blp_uncompressed(self):
        w, h, px = images.decode_blp(blp_raw(2, 2, self.PX))
        self.assertEqual((w, h, px[12:16]), (2, 2, bytes((255, 255, 255, 128))))

    def test_dxt1_block(self):
        # c0 = pure red, c1 = pure blue, every texel index 0 -> red
        block = struct.pack("<HHI", 0xF800, 0x001F, 0)
        px = images._dxt(block, 4, 4, 1, 0)
        self.assertEqual(px[:4], bytes((255, 0, 0, 255)))

    def test_fit_and_png(self):
        img = (256, 256, bytes((10, 20, 30, 255)) * 256 * 256)
        w, h, px = images.fit(img, 128)
        self.assertEqual((w, h, px[:4]), (128, 128, bytes((10, 20, 30, 255))))
        out = images.png((w, h, px))
        self.assertTrue(out.startswith(b"\x89PNG"))
        raw = zlib.decompress(out[out.index(b"IDAT") + 4:out.index(b"IEND") - 8])
        self.assertEqual(len(raw), h * (1 + w * 4))

    def test_blank(self):
        self.assertTrue(images.is_blank((2, 1, bytes((0, 0, 0, 0)) * 2)))
        self.assertFalse(images.is_blank((2, 2, b"".join(bytes(p) for p in self.PX))))


class FakeRemoteZip(remotezip.RemoteZip):
    def __init__(self, data):
        self.data = data
        self.requests = 0
        self.entries = self._read_directory()

    def _range(self, start, end=None):
        end = len(self.data) - 1 if end is None else min(end, len(self.data) - 1)
        return self.data[start:end + 1], len(self.data)


class Zip(unittest.TestCase):
    def make(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
        return buf.getvalue()

    def test_reads_entries_and_folders(self):
        data = self.make({"Foo/Foo_Forever.toc": "## IconTexture: Interface\\AddOns\\Foo\\logo\n",
                          "Foo/logo.tga": tga(2, 2, Images.PX), "Foo_Options/Foo_Options.toc": "",
                          "Foo/sub/x.lua": "x"})
        z = FakeRemoteZip(data)
        self.assertEqual(remotezip.top_folders(z.names()), ["Foo", "Foo_Options"])
        self.assertEqual(z.read("foo/foo_forever.toc").decode()[:15], "## IconTexture:")
        self.assertEqual(icons._resolve(z, "Interface\\AddOns\\Foo\\logo"), "Foo/logo.tga")
        self.assertIsNone(icons._resolve(z, "Interface\\Icons\\INV_Misc_Bag_08"))
        self.assertEqual(icons._toc_for(z, "Foo"), "Foo/Foo_Forever.toc")


if __name__ == "__main__":
    unittest.main()
