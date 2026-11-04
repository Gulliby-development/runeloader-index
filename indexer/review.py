"""Flag index entries whose release zip may not install or load well.

After each run the indexer checks every release zip it reads and publishes the
findings as review.json. New findings (a new addon, a new release or a new kind
of problem) also go into a Markdown report that the daily job posts as a GitHub
issue, so someone can look at the addon and fix the index or the app.

Accepted findings are silenced in review-ignore.txt: "owner/repo code" or
"owner/repo *".
"""

import posixpath
import re

from . import forever

# code -> what it means, shown in the issue. A new check gets a code here and a test.
CHECKS = {
    "no_addon": "the app finds no addon folder with a .toc in the zip (an entry whose zip has no .toc at all is left out of the index)",
    "no_forever_toc": "no folder has a _Forever or _Camelot TOC or Interface 160xx, so the game may call it out of date",
    "placeholder": "unreplaced packager tokens such as @project-version@ in the TOC (zipped without the packager)",
    "missing_files": "the TOC lists files that are not in the zip",
    "missing_dependency": "a required addon is neither in the zip nor in the index",
    "shared_folder": "another repo in the index installs the same folder, so one overwrites the other",
    "guessed": "no release.json; the Forever zip was guessed from asset names",
    "zip_error": "the release zip could not be read",
}

TOC_CHAIN = ("_Forever", "_Camelot", "_Mainline", "")
_SUFFIX = re.compile(r"[_-](forever|camelot|mainline|standard|classic|vanilla|bcc|tbc|wrath|wotlk|cata|mists|vengeance)$",
                     re.IGNORECASE)
_TOKEN = re.compile(r"@[a-z][a-z0-9-]*@")
MAX_ROOTS = 12   # TOCs read per zip; packs with dozens of folders get the first ones checked


def toc_base(file_name):
    """"Bagnon_Forever.toc" -> "Bagnon", "Bagnon.toc" -> "Bagnon" (the app's Toc.baseName)."""
    plain = file_name[:-4] if file_name.lower().endswith(".toc") else file_name
    return _SUFFIX.sub("", plain)


def find_roots(names):
    """{prefix in the zip: AddOns folder name}, the same choice as the app's Installer.findRoots."""
    candidates, loose = {}, {}
    for raw in names:
        name = raw.replace("\\", "/")
        if not name.lower().endswith(".toc"):
            continue
        parent, _, file = name.rpartition("/")
        base = toc_base(file)
        if not base:
            continue
        if not parent:
            loose[""] = base
            continue
        folder = parent.rpartition("/")[2]
        if base.lower() == folder.lower() or file[:-4].lower() == folder.lower():
            candidates[parent + "/"] = folder
        else:
            loose.setdefault(parent + "/", base)
    if not candidates:
        for prefix, base in loose.items():   # a repo archive wraps the addon in "owner-repo-sha/"
            if prefix == "" or prefix.find("/") == len(prefix) - 1:
                candidates[prefix] = base
                break
    roots, taken = {}, set()
    for p in sorted(candidates, key=len):
        if any(r and p.startswith(r) for r in roots):
            continue   # a sub-addon inside another addon folder
        folder = candidates[p]
        if folder.lower() in taken:
            continue
        taken.add(folder.lower())
        roots[p] = folder
    if "" in roots and len(roots) > 1:
        del roots[""]
    return roots


def _pick_toc(entries, prefix, folder):
    for suffix in TOC_CHAIN:
        name = "%s%s%s.toc" % (prefix, folder, suffix)
        if name.lower() in entries:
            return entries[name.lower()][0], suffix
    return None, None


def toc_files(text):
    """Files a TOC loads, as written; lines with [Variables] are skipped."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "[" in line:
            continue
        out.append(line.replace("\\", "/"))
    return out


def zip_checks(entries, read):
    """Install checks for one zip.

    entries: lower-case name -> (name, ...), as RemoteZip.entries.
    read(name) returns the bytes of one small file.
    Returns {"roots": [...], "problems": [[code, detail], ...], "deps": [...]}.
    """
    roots = find_roots([e[0] for e in entries.values()])
    if not roots:
        return {"roots": [], "problems": [["no_addon", ""]], "deps": []}
    problems, deps, no_forever, tokens, missing, checked = [], [], [], set(), [], 0
    folders = {f.lower() for f in roots.values()}
    for prefix, folder in list(roots.items())[:MAX_ROOTS]:
        name, suffix = _pick_toc(entries, prefix, folder)
        if not name:   # a TOC at the zip root or a wrapped one under its own name
            name = next((e[0] for k, e in entries.items() if k.startswith(prefix.lower()) and k.endswith(".toc")
                         and "/" not in k[len(prefix):]), None)
            suffix = ""
        if not name:
            continue
        checked += 1
        text = (read(name) or b"").decode("utf-8", "replace")
        fields = forever.parse_toc(text)
        if suffix not in ("_Forever", "_Camelot") and not forever.interface_is_forever(fields.get("interface")):
            no_forever.append("%s (Interface %s)" % (folder, fields.get("interface") or "missing"))
        for key in ("title", "version", "author", "notes", "interface"):
            tokens.update(_TOKEN.findall(fields.get(key, "")))
        base_dir = posixpath.dirname(name)
        for f in toc_files(text):
            tokens.update(_TOKEN.findall(f))
            path = posixpath.normpath(posixpath.join(base_dir, f)) if base_dir else posixpath.normpath(f)
            if path.lower() not in entries and not _TOKEN.search(f):
                missing.append(path)
        deps += [d for d in forever.toc_deps(fields)
                 if d.lower() not in folders and not d.lower().startswith("blizzard_") and d not in deps]
    if no_forever and len(no_forever) == checked:   # packs often ship a few retail-only folders on purpose
        problems.append(["no_forever_toc", ", ".join(no_forever[:5])])
    if tokens:
        problems.append(["placeholder", " ".join(sorted(tokens)[:5])])
    if missing:
        more = " and %d more" % (len(missing) - 3) if len(missing) > 3 else ""
        problems.append(["missing_files", ", ".join(missing[:3]) + more])
    return {"roots": sorted(roots.values(), key=str.lower), "problems": problems, "deps": deps}


def load_ignore(path):
    """review-ignore.txt: {repo_lower: {codes}}; "*" silences every code."""
    out = {}
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            parts = line.split("#")[0].split()
            if parts and "/" in parts[0]:
                out.setdefault(parts[0].lower(), set()).update(parts[1:] or ["*"])
    return out


def entry_key(a):
    """The index key: repo, plus #module for one module of a repo (build_index.entry_key keeps the case)."""
    return a["repo"] + ("#" + a["module"] if a.get("module") else "")


def build(addons, zip_info, zip_errors=(), ignore=None, old=None, today="", left_out=()):
    """Return (review, new) for this run.

    zip_info: url -> zip_checks() result (or the cached zip meta that holds it in "checks").
    review: {"addons": {repo: {"version", "url", "problems", "since"}}} for review.json.
    new: [(key, version, [[code, detail], ...])] of findings not seen for that release before.
    zip_errors: keys whose zip could not be read; left_out: entries dropped for a zip without any .toc.
    ignore matches by repo, so one line covers every module of it.
    """
    ignore = ignore or {}
    old_addons = (old or {}).get("addons") or {}
    owners = {}
    for a in addons:
        for f in a.get("folders") or []:
            owners.setdefault(f.lower(), []).append(a["repo"])
    index_folders = set(owners)
    zip_errors = {z.lower() for z in zip_errors}

    out, new = {}, []
    for a in list(addons) + list(left_out):
        repo, key = a["repo"], entry_key(a)
        checks = zip_info.get(a.get("url")) or {}
        checks = checks.get("checks", checks)
        problems = [list(p) for p in checks.get("problems") or []]
        lost = [d for d in checks.get("deps") or [] if d.lower() not in index_folders]
        if lost:
            problems.append(["missing_dependency", ", ".join(lost[:5])])
        # modules of one repo may ship the same folder on purpose; other repos overwrite each other
        shared = sorted({r for f in a.get("folders") or [] for r in owners.get(f.lower(), []) if r.lower() != repo.lower()})
        if shared:
            problems.append(["shared_folder", ", ".join(shared[:5])])
        if not a.get("verified"):
            problems.append(["guessed", a.get("file", "")])
        if a in left_out:
            problems = [["no_addon", a.get("file", "")]]
        if key.lower() in zip_errors:
            problems.append(["zip_error", ""])
        skip = ignore.get(repo.lower(), set())
        problems = [p for p in problems if "*" not in skip and p[0] not in skip]
        if not problems:
            continue
        before = old_addons.get(key) or {}
        same_release = before.get("url") == a.get("url")
        seen = {p[0] for p in before.get("problems") or []} if same_release else set()
        out[key] = {"version": a.get("version", ""), "url": a.get("url", ""), "problems": problems,
                     "since": before.get("since", today) if same_release else today}
        fresh = [p for p in problems if p[0] not in seen]
        if fresh:
            new.append((key, a.get("version", ""), fresh))
    return {"generated": today, "checks": CHECKS, "addons": out}, new


def report(new, total, limit=60000):
    """Markdown for the issue: the new findings grouped by addon, cut to fit an issue body."""
    lines = ["%d addons have new findings (%d flagged in total, see `review.json` on gh-pages)." % (len(new), total),
             "", "Each line is a check that may break the install or load of that release. Fix the index or the app,"
             " or add `owner/repo code` to `review-ignore.txt` when it is fine.", ""]
    used = set()
    for key, version, problems in sorted(new, key=lambda n: n[0].lower()):
        lines.append("- [ ] **[%s](https://github.com/%s)** %s" % (key, key.split("#")[0], version))
        for code, detail in problems:
            used.add(code)
            lines.append("  - `%s`%s" % (code, ": " + detail.replace("\n", " ")[:300] if detail else ""))
    lines += ["", "<details><summary>Checks</summary>", ""]
    lines += ["- `%s`: %s" % (c, CHECKS[c]) for c in CHECKS if c in used]
    lines += ["", "</details>"]
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit].rsplit("\n- [ ]", 1)[0] + "\n\n_The list is cut; see `review.json` for the rest._"
    return text
