"""Rules that decide whether a GitHub release has a WoW Forever build.

These mirror Sources.GitHub and Toc in the Runeloader app
(github.com/Gulliby-development/runeloader). A change here needs the same
change there, with a test on both sides.
"""

import posixpath
import re

FOREVER_FLAVORS = ("forever", "camelot")
OTHER_FLAVOR_MARKS = ("classic", "bcc", "tbc", "wrath", "wotlk", "cata", "mists",
                      "vanilla", "titan", "mainline", "retail")
FOREVER_INTERFACE_PREFIX = "160"

_FOREVER_TOC = re.compile(r"^(?P<base>.+?)[_-](?P<suffix>forever|camelot)\.toc$", re.IGNORECASE)
_COLOR = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t|\|A[^|]*\|a")


def pick_from_release_json(data):
    """Return the release.json entry for Forever, or None.

    Same score as the app: "forever" beats "camelot", and a build with libraries
    beats a "nolib" one.
    """
    best, best_score = None, -1
    for rel in (data or {}).get("releases") or []:
        if not isinstance(rel, dict):
            continue
        score = -1
        for meta in rel.get("metadata") or []:
            flavor = (meta or {}).get("flavor") if isinstance(meta, dict) else None
            if flavor == FOREVER_FLAVORS[0]:
                score = max(score, 2)
            elif flavor == FOREVER_FLAVORS[1]:
                score = max(score, 1)
        if score >= 0 and not rel.get("nolib", False):
            score += 10
        if score >= 0 and score > best_score:
            best, best_score = rel, score
    return best


_BINARY = re.compile(r"(^|[-_. ])(win(dows)?|win64|win32|linux|mac(os)?|osx|darwin|x64|x86(_64)?|amd64|arm64|aarch64|setup|installer|portable|candidate)([-_. ]|$)",
                     re.IGNORECASE)


def is_app_binary(name):
    """True for zips of desktop programs (simulators, launchers) rather than addons."""
    return bool(_BINARY.search(re.sub(r"(?i)\.zip$", "", name or "")))


def pick_from_assets(assets):
    """Return (asset, guessed) for a release without release.json, or (None, False).

    A zip named for Forever is a sure pick. Otherwise the first zip that names no
    other flavor and is not "nolib" is a guess.
    """
    zips = [a for a in assets or [] if str(a.get("name", "")).lower().endswith(".zip") and not is_app_binary(a["name"])]
    for a in zips:
        n = a["name"].lower()
        if "forever" in n or "camelot" in n:
            return a, False
    for a in zips:
        n = a["name"].lower()
        other = any(("-" + m) in n or ("_" + m) in n for m in OTHER_FLAVOR_MARKS)
        if not other and "nolib" not in n:
            return a, True
    return None, False


def is_forever_toc_path(path):
    """True when a repo path is a Forever TOC of its own folder.

    "Bagnon/Bagnon_Forever.toc" is a Forever TOC. "Bagnon_Forever/Bagnon_Forever.toc"
    is the base TOC of an old addon that happens to be called "Bagnon_Forever".
    """
    name = posixpath.basename(path)
    m = _FOREVER_TOC.match(name)
    if not m:
        return False
    parent = posixpath.basename(posixpath.dirname(path))
    if not parent:
        return True  # TOC at the repo root: the repo is the addon folder
    return parent.lower() == m.group("base").lower()


_NOT_ADDON_DIRS = {"test", "tests", "fixture", "fixtures", "example", "examples", "template", "templates",
                   "doc", "docs", "sample", "samples", "tools", "spec", "testdata"}


def is_sample_path(path):
    """True for TOCs in test fixtures, examples or templates: they are not the repo's addon."""
    return any(part.lower() in _NOT_ADDON_DIRS for part in path.split("/")[:-1])


def guess_matches(asset_name, names):
    """A guessed zip must carry the addon or repo name, so tool binaries are not taken for addons."""
    n = re.sub(r"[^a-z0-9]", "", asset_name.lower())
    return any(len(k) >= 3 and re.sub(r"[^a-z0-9]", "", k.lower()) in n for k in names if k)


def toc_base(path):
    """"Bagnon_Forever.toc" -> "Bagnon": the folder name a TOC belongs to."""
    m = _FOREVER_TOC.match(posixpath.basename(path))
    return m.group("base") if m else None


def interface_is_forever(value):
    return any(p.strip().startswith(FOREVER_INTERFACE_PREFIX) for p in str(value or "").split(","))


def clean(text):
    """Strip WoW colour, texture and atlas escapes from a TOC string."""
    return re.sub(r"\s+", " ", _COLOR.sub("", text or "")).strip()


def parse_toc(text):
    """Return the "## Key: value" fields of a TOC, keys lower-cased."""
    fields = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line.startswith("##"):
            continue
        key, sep, value = line[2:].partition(":")
        if sep:
            fields.setdefault(key.strip().lower(), value.strip())
    return fields


def source_ids(fields):
    """Ids of the same addon on other sites, written into the TOC by BigWigs packager."""
    ids = {}
    for key, name in (("x-curse-project-id", "curseforge"), ("x-wowi-id", "wowinterface"), ("x-wago-id", "wago")):
        v = (fields.get(key) or "").strip()
        if v and v != "0":
            ids[name] = v
    return ids


def version_from_file(name):
    n = re.sub(r"(?i)\.zip$", "", name or "")
    m = re.search(r"v?\d+(\.\d+)+\S*", n)
    return m.group(0) if m else n
