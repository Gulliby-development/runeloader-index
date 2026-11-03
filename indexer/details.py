"""Turn README and release notes into the plain text the app shows, and guess a category."""

import re
import urllib.parse

# The description stops at the first heading about installing, building or history.
_STOP = re.compile(r"^(install|installation|how to install|download|changelog|change log|changes|history|"
                   r"build|building|development|contribut|license|licence|credits|support|faq|known issues|todo)",
                   re.IGNORECASE)
_BADGE = re.compile(r"shields\.io|badge|travis-ci|circleci|codecov|/workflows/|\.svg(\?|$)|discord|ko-fi|patreon|paypal",
                    re.IGNORECASE)
_IMG_MD = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_IMG_HTML = re.compile(r"<img[^>]*\ssrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)

# Category keys the app knows, checked in this order.
CATEGORIES = (
    ("quests", ("quest", "leveling", "levelling", "guide", "questie", "rxp")),
    ("map", ("map", "minimap", "coordinates", "navigation", "waypoint", "tomtom")),
    ("bags", ("bag", "inventory", "bank", "item", "loot", "auction", "vendor", "gear", "transmog")),
    ("combat", ("combat", "boss", "raid", "dungeon", "dps", "heal", "tank", "pvp", "aura", "cooldown",
                "threat", "damage", "rotation", "dbm", "bigwigs", "timer")),
    ("ui", ("ui", "interface", "unitframe", "unit-frames", "actionbar", "action-bars", "nameplate", "hud",
            "frames", "skin", "tooltip", "chat", "font", "media")),
    ("tools", ("utility", "utilities", "tool", "library", "lib", "misc", "macro", "profession", "automation")),
)


# Tag for addons made for a gamepad or a handheld. Only clear signals count:
# a topic, or a word in the TOC title/notes/category, the repo name or its summary.
GAMEPAD_TOPICS = {"gamepad", "controller", "controller-support", "consoleport", "steam-deck", "steamdeck",
                  "handheld", "joystick", "crossbar", "gamepad-ui"}
_GAMEPAD_WORDS = re.compile(r"(?<![a-z])(gamepad|game pad|controller|consoleport|console port|steam ?deck|handheld|"
                            r"crossbar|joystick|dualsense|xbox|back paddles?)(?![a-z])", re.IGNORECASE)
# "controller" that is not about a game controller.
_NOT_GAMEPAD = re.compile(r"(midi|loot|addon|raid|camera|mouse ?look) controller", re.IGNORECASE)


def is_gamepad(topics, toc_fields=None, *texts):
    """True when an addon is clearly made for playing with a gamepad or on a handheld."""
    if GAMEPAD_TOPICS & {str(t).lower() for t in topics or []}:
        return True
    f = toc_fields or {}
    parts = [f.get("x-category", ""), f.get("title", ""), f.get("notes", "")] + list(texts)
    text = _NOT_GAMEPAD.sub(" ", " ".join(p or "" for p in parts))
    return bool(_GAMEPAD_WORDS.search(text))


def mentions_gamepad(text):
    """A softer signal (the README): logged for review, never tagged on its own."""
    return bool(_GAMEPAD_WORDS.search(_NOT_GAMEPAD.sub(" ", text or "")))


def plain_text(md, limit=4000):
    """Markdown or simple HTML to readable plain text: keeps headings, lists and paragraphs."""
    if not md:
        return ""
    t = md.replace("\r\n", "\n")
    t = re.sub(r"<!--.*?-->", "", t, flags=re.DOTALL)
    t = re.sub(r"```.*?```", "", t, flags=re.DOTALL)
    t = _IMG_MD.sub("", t)
    t = re.sub(r"<img[^>]*>", "", t, flags=re.IGNORECASE)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"</?(p|div|h\d|li|ul|ol|tr|table)[^>]*>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)          # [text](url) -> text
    t = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", t)         # [text][ref] -> text
    t = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$", r"\1", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*+]\s+", "• ", t, flags=re.MULTILINE)
    t = re.sub(r"(\*\*|__)(.*?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])[*_]([^*_\n]+)[*_](?![\w*])", r"\1", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s*(---+|\*\*\*+|___+)\s*$", "", t, flags=re.MULTILINE)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > limit:
        cut = t.rfind("\n", 0, limit)
        t = t[:cut if cut > limit // 2 else limit].rstrip() + "…"
    return t


def readme(text, base_url, limit=4000, max_shots=6):
    """(description, screenshots) from a README: the part before the install or changelog section."""
    if not text:
        return "", []
    lines, kept, seen_heading = text.replace("\r\n", "\n").split("\n"), [], False
    for line in lines:
        m = re.match(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", line) or re.match(r"^\s*<h\d[^>]*>(.*?)</h\d>", line, re.I)
        if m:
            title = re.sub(r"[^\w\s]", "", re.sub(r"<[^>]+>", "", m.group(1))).strip()
            if seen_heading and _STOP.match(title):
                break
            seen_heading = True
        kept.append(line)
    part = "\n".join(kept)
    shots = []
    for url in _IMG_MD.findall(part) + _IMG_HTML.findall(part):
        if _BADGE.search(url):
            continue
        full = urllib.parse.urljoin(base_url, url)
        full = re.sub(r"^https://github\.com/([^/]+/[^/]+)/blob/", r"https://raw.githubusercontent.com/\1/", full)
        if full.startswith("https://") and full not in shots:
            shots.append(full)
        if len(shots) == max_shots:
            break
    desc = plain_text(part, limit)
    # Drop a first line that only repeats the project name.
    first, _, rest = desc.partition("\n")
    if rest and len(first) < 60 and "." not in first:
        desc = rest.strip()
    return desc, shots


def category(x_category, topics, text=""):
    """One of quests|map|bags|combat|ui|tools, or None."""
    words = " ".join([x_category or "", " ".join(topics or [])]).lower()
    for source in (words, (text or "").lower()):
        if not source.strip():
            continue
        tokens = set(re.split(r"[^a-z0-9-]+", source))
        for key, marks in CATEGORIES:
            if any(m in tokens or (len(m) > 4 and m in source) for m in marks):
                return key
    return None
