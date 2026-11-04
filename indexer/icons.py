"""Addon logos and folders from the release zip, read with range requests."""

import os
import re

from . import forever, images, remotezip, review

TOC_CHAIN = ("_Forever", "_Camelot", "_Mainline", "")
SIDE = 128
MAX_PNG = 256 * 1024


def icon_name(repo):
    return "icons/%s.png" % re.sub(r"[^A-Za-z0-9._-]", "_", repo.replace("/", "__").replace("#", "__"))


def _toc_for(z, folder):
    for suffix in TOC_CHAIN:
        name = "%s/%s%s.toc" % (folder, folder, suffix)
        if name.lower() in z.entries:
            return name
    return None


def _resolve(z, texture):
    """Interface\\AddOns\\X\\Media\\logo -> the matching file in the zip, or None."""
    t = texture.replace("\\", "/").strip().strip('"')
    low = t.lower()
    if not low.startswith("interface/addons/"):
        return None   # a game icon such as Interface\\Icons\\..., not shipped in the zip
    rel = t[len("interface/addons/"):]
    has_ext = os.path.splitext(rel)[1].lower() in (".blp", ".tga", ".png")
    for name in ([rel] if has_ext else [rel + ".blp", rel + ".tga", rel + ".png"]):
        if name.lower() in z.entries:
            return z.entries[name.lower()][0]
    return None


def from_zip(url, repo, preferred, out_dir, log=print):
    """Return {"folders", "icon", "tocs", "deps", "title"} for a release zip.

    repo names the icon file ("owner/Repo" or "owner/Repo#Module"). tocs counts the .toc files:
    0 means the app's installer would find no addon in the zip. deps are the folders the zip's
    addons require that it does not ship; title is the TOC title of its main folder; checks are the
    install checks of review.zip_checks.
    """
    z = remotezip.RemoteZip(url)
    names = z.names()
    folders = remotezip.top_folders(names)
    read = {}

    def small(name):
        if name not in read:
            try:
                read[name] = z.read(name, max_size=256 * 1024)
            except remotezip.RemoteZipError:
                read[name] = None
        return read[name]

    result = {"folders": folders, "icon": None, "tocs": sum(n.lower().endswith(".toc") for n in names),
              "deps": [], "title": "", "checks": review.zip_checks(z.entries, small)}
    order = [f for f in preferred if f in folders] + [f for f in folders if f not in preferred]
    repo_name = repo.split("/", 1)[1].split("#")[0].lower()
    order.sort(key=lambda f: (f not in preferred, f.lower() != repo_name, len(f)))
    own = {f.lower() for f in folders}
    tocs = {}
    for folder in order[:12]:
        toc = _toc_for(z, folder)
        if toc:
            tocs[folder] = forever.parse_toc((small(toc) or b"").decode("utf-8", "replace"))
    for folder, fields in tocs.items():
        for d in forever.toc_deps(fields):
            if d.lower() not in own and d not in result["deps"]:
                result["deps"].append(d)
        if not result["title"] and fields.get("title"):
            result["title"] = forever.clean(fields["title"])[:120]
    for folder in order[:6]:
        fields = tocs.get(folder)
        if not fields:
            continue
        texture = fields.get("icontexture")
        if not texture:
            continue
        name = _resolve(z, texture)
        if not name:
            continue
        try:
            data = z.read(name, max_size=4 * 1024 * 1024)
            if name.lower().endswith(".png"):
                if len(data) > MAX_PNG or data[:8] != b"\x89PNG\r\n\x1a\n":
                    continue
                out = data
            else:
                img = images.decode(name, data)
                if images.is_blank(img):
                    continue
                out = images.png(images.fit(img, SIDE))
        except (images.ImageError, remotezip.RemoteZipError, ValueError) as e:
            log("icon %s: %s" % (repo, e))
            continue
        rel = icon_name(repo)
        os.makedirs(os.path.join(out_dir, "icons"), exist_ok=True)
        with open(os.path.join(out_dir, rel), "wb") as f:
            f.write(out)
        result["icon"] = rel
        break
    return result
