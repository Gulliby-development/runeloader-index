"""Build index.json: GitHub repositories whose latest release has a WoW Forever build.

Usage: python -m indexer.build_index --out site
Needs GITHUB_TOKEN (code search only works with a token). Standard library only.

Steps: find candidate repos, check each repo's latest release with the same rules
the app uses (indexer/forever.py), read its Forever TOC for metadata, write
site/index.json and keep site/state.json (release.json picks by URL) for the next run.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import details, forever, icons, review

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "runeloader-index"

# Code search: the most precise signals first. Each query is also split by file
# size, because one search returns at most 1000 results.
SIZE_SLICES = ("size:<600", "size:600..1200", "size:1201..2500", "size:>2500")
TOC_QUERIES = ("filename:_Forever.toc", "filename:_Camelot.toc", '"## Interface: 160" extension:toc')
PACKAGER_QUERIES = ("BigWigsMods/packager path:.github/workflows",)
REPO_QUERIES = ("topic:wow-addon", "topic:world-of-warcraft-addon", "topic:wow-addons",
                "topic:worldofwarcraft-addon", "topic:wow-forever", "wow forever addon in:name,description,readme",
                "wow forever gamepad in:name,description", "wow forever controller in:name,description")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class GitHub:
    def __init__(self, token):
        self.token = token
        self.calls = 0
        self.search_deadline = None
        self.last_total = 0
        self.last_search = 0.0

    def _request(self, url, accept="application/vnd.github+json"):
        headers = {"User-Agent": UA, "Accept": accept}
        if self.token and url.startswith(API):
            # Only the API gets the token: release downloads redirect to signed blob URLs.
            headers["Authorization"] = "Bearer " + self.token
        for attempt in range(4):
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    self.calls += 1
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and self._wait_for_limit(e, url):
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("gave up on " + url)

    def _wait_for_limit(self, e, url=""):
        reset = e.headers.get("X-RateLimit-Reset")
        retry = e.headers.get("Retry-After")
        if retry:
            wait = int(retry)
        elif reset and e.headers.get("X-RateLimit-Remaining") == "0":
            wait = max(1, int(reset) - int(time.time()) + 2)
        else:
            return False
        if "/search/" in url and self.search_deadline and time.time() + wait > self.search_deadline:
            log("search throttled for %ds, past the search time; skipping" % wait)
            return False
        if wait > 900:
            log("rate limit resets in %ds, stopping this source" % wait)
            return False
        log("rate limited, waiting %ds" % wait)
        time.sleep(wait)
        return True

    def json(self, path_or_url, **kw):
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        return json.loads(self._request(url, **kw))

    def graphql(self, query):
        body = json.dumps({"query": query}).encode()
        headers = {"User-Agent": UA, "Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        for attempt in range(4):
            req = urllib.request.Request(API + "/graphql", data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.calls += 1
                    res = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and self._wait_for_limit(e):
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise
        else:
            raise RuntimeError("graphql gave up")
        # A missing or blocked repo comes back as an error next to the other results.
        for err in res.get("errors") or []:
            if err.get("type") not in ("NOT_FOUND", "FORBIDDEN"):
                log("graphql: %s" % err.get("message"))
        return res.get("data")

    def search(self, kind, q, max_pages=10):
        """Yield items of a code or repo search, 100 per page, up to 1000.

        A failed or throttled search ends that query only; the run goes on.
        """
        for page in range(1, max_pages + 1):
            if self.search_deadline and time.time() > self.search_deadline:
                log("search time is up, skipping: %s" % q)
                return
            gap = 6.5 if kind == "code" else 2.2      # code search: 10/min, repo search: 30/min
            time.sleep(max(0.0, self.last_search + gap - time.time()))
            self.last_search = time.time()
            url = "%s/search/%s?q=%s&per_page=100&page=%d" % (API, kind, urllib.parse.quote(q), page)
            try:
                data = self.json(url)
            except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, ValueError) as e:
                log("search failed (%s): %s" % (getattr(e, "code", e), q))
                return
            if page == 1:
                self.last_total = data.get("total_count", 0)
            items = data.get("items") or []
            yield from items
            if len(items) < 100:
                return


class Candidate:
    def __init__(self, repo):
        self.repo = repo
        self.reasons = set()
        self.toc_paths = []   # Forever TOCs seen in code search


def discover(gh, seeds, known):
    """Candidates from seeds, earlier runs and today's searches.

    Candidates found on earlier days are kept in state.json, so a day with a
    throttled search still checks everything found before.
    """
    found = {}

    def add(repo, reason, toc=None):
        key = repo.lower()
        c = found.get(key) or found.setdefault(key, Candidate(repo))
        c.reasons.add(reason)
        if toc and toc not in c.toc_paths:
            c.toc_paths.append(toc)

    for repo in seeds:
        add(repo, "seed")
    for repo, k in (known or {}).items():
        for r in k.get("reasons", []):
            add(k.get("repo", repo), r)
        for t in k.get("toc_paths", []):
            add(k.get("repo", repo), "toc", t)

    def code(q, handle):
        # Split by file size only when one query has more than 1000 results.
        first = list(gh.search("code", q))
        if gh.last_total <= 1000:
            slices = [first]
        else:
            slices = (gh.search("code", "%s %s" % (q, s)) for s in SIZE_SLICES)
        for items in slices:
            for it in items:
                handle(it)

    def toc_hit(q):
        def handle(it):
            path = it.get("path", "")
            repo = (it.get("repository") or {}).get("full_name")
            if repo and not forever.is_sample_path(path) and (q.startswith('"## Interface') or forever.is_forever_toc_path(path)):
                add(repo, "toc", path)
        return handle

    def packager_hit(it):
        repo = (it.get("repository") or {}).get("full_name")
        if repo:
            add(repo, "packager")

    for q in TOC_QUERIES:
        code(q, toc_hit(q))
    for q in REPO_QUERIES:
        for it in gh.search("repositories", q):
            if not it.get("fork") and not it.get("archived"):
                add(it["full_name"], "topic")
    for q in PACKAGER_QUERIES:   # broadest and slowest, so last
        code(q, packager_hit)
    log("candidates: %d" % len(found))
    return found


def remember(found):
    return {k: {"repo": c.repo, "reasons": sorted(c.reasons - {"seed"}), "toc_paths": c.toc_paths}
            for k, c in found.items() if c.reasons - {"seed"}}


PRIORITY = {"seed": 0, "toc": 1, "topic": 2, "dependency": 2, "packager": 3}

RELEASE_FIELDS = "tagName publishedAt isPrerelease description releaseAssets(first: 40) { nodes { name size downloadCount downloadUrl } }"

REPO_FIELDS = """
  nameWithOwner name description stargazerCount isArchived isFork
  owner { login } defaultBranchRef { name }
  repositoryTopics(first: 10) { nodes { topic { name } } }
  latestRelease { %s }
  releases(first: 6, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { %s } }
""" % ((RELEASE_FIELDS,) * 2)


def select(found, listed, max_new):
    """Candidates to check: every addon already listed, then new ones by priority up to max_new."""
    for repo in listed.values():
        key = repo["repo"].lower()
        if key not in found:   # listed but lost from the candidates: check it anyway
            found[key] = Candidate(repo["repo"])
            found[key].reasons.update(repo.get("found_by") or ["listed"])
    rank = lambda c: (min(PRIORITY.get(r, 9) for r in c.reasons), c.repo.lower())
    old = sorted((c for k, c in found.items() if k in listed), key=rank)
    new = sorted((c for k, c in found.items() if k not in listed), key=rank)
    return old + new[:max(0, max_new)]


def fetch_repos(gh, candidates, batch=30, failed=None):
    """Repo metadata and recent releases for many repos, 30 per GraphQL query.

    One query costs a few rate-limit points, so a few thousand repos fit in the
    1000 points per hour the Actions token gets. Repos of a batch that failed go
    into `failed`: they were not answered, which is not the same as gone.
    """
    out = {}
    for i in range(0, len(candidates), batch):
        chunk = candidates[i:i + batch]
        parts = []
        for j, c in enumerate(chunk):
            owner, name = c.repo.split("/", 1)
            parts.append("r%d: repository(owner: %s, name: %s) {%s}" % (j, json.dumps(owner), json.dumps(name), REPO_FIELDS))
        try:
            data = gh.graphql("query {" + " ".join(parts) + "}")
        except Exception as ex:
            log("graphql batch failed: %s" % ex)
            if failed is not None:
                failed.update(c.repo.lower() for c in chunk)
            continue
        for j, c in enumerate(chunk):
            node = (data or {}).get("r%d" % j)
            if node:
                out[c.repo.lower()] = node
    return out


def forever_build(gh, rel, c, node, cache):
    """The Forever asset of one release as {file, version, url, size, downloads, verified}, or None."""
    assets = (rel.get("releaseAssets") or {}).get("nodes") or []
    rj_asset = next((a for a in assets if a.get("name") == "release.json"), None)
    if rj_asset:
        url = rj_asset["downloadUrl"]
        if url not in cache:
            try:
                cache[url] = forever.pick_from_release_json(json.loads(gh._request(url, accept="application/json")))
            except (ValueError, urllib.error.HTTPError, urllib.error.URLError):
                return None   # try again next run
        pick = cache[url]
        if not pick:
            return None
        fname = pick.get("filename", "")
        asset = next((a for a in assets if a.get("name") == fname), None)
        if not asset:
            return None
        return {"file": fname, "version": pick.get("version") or forever.version_from_file(fname), "verified": True,
                "size": asset.get("size"), "downloads": asset.get("downloadCount"), "url": asset.get("downloadUrl")}
    asset, guessed = forever.pick_from_assets(assets)
    if not asset:
        return None
    # A guessed zip counts only when the repo itself ships a Forever TOC and the zip carries its name.
    names = [node["name"]] + [p.rsplit("/", 2)[-2] if "/" in p else node["name"] for p in c.toc_paths]
    if guessed and not ("toc" in c.reasons and forever.guess_matches(asset["name"], names)):
        return None
    return {"file": asset["name"], "version": rel.get("tagName") or forever.version_from_file(asset["name"]),
            "verified": not guessed, "size": asset.get("size"), "downloads": asset.get("downloadCount"),
            "url": asset.get("downloadUrl")}


def entry_key(e):
    """repo, or repo#module for one module of a repo that releases modules separately."""
    return e["repo"].lower() + ("#" + e["module"].lower() if e.get("module") else "")


def check(gh, c, node, cache, gamepad=None):
    """Return the index entries of a candidate: [] when it has no Forever build, one entry for an
    ordinary repo, one per module (core first) for a repo that releases modules separately.

    gamepad is the hand-made list from gamepad.txt: {repo_lower: True (tag) or False (never tag)}.
    """
    if node.get("isArchived") or (node.get("isFork") and "seed" not in c.reasons):
        return []
    rel = node.get("latestRelease")
    if not rel:
        return []
    history = (node.get("releases") or {}).get("nodes") or []
    if forever.module_groups(history + [rel], node["name"]) is None:
        e = describe(gh, c, node, cache, gamepad, rel, history, None)
        return [e] if e else []
    history = module_releases(gh, node["nameWithOwner"]) or history
    groups = forever.module_groups(history, node["name"]) or {}
    out = []
    for g in sorted(groups, key=lambda k: (k != "", k)):
        name, r = groups[g]
        own = [h for h in history if forever.release_group(h.get("tagName"), node["name"]).lower() == g]
        e = describe(gh, c, node, cache, gamepad, r, own, name or None)
        if e:
            out.append(e)
    return out


def describe(gh, c, node, cache, gamepad, rel, history, module):
    """One index entry for release rel (and its own history), or None without a Forever build."""
    entry = forever_build(gh, rel, c, node, cache)
    if entry is None:
        return None

    entry.update({
        "repo": node["nameWithOwner"],
        "name": node["name"],
        "author": (node.get("owner") or {}).get("login"),
        "summary": node.get("description") or "",
        "stars": node.get("stargazerCount", 0),
        "topics": [t["topic"]["name"] for t in (node.get("repositoryTopics") or {}).get("nodes") or []],
        "tag": rel.get("tagName", ""),
        "released": rel.get("publishedAt"),
        "prerelease": bool(rel.get("isPrerelease")),
        "found_by": sorted(c.reasons, key=lambda r: PRIORITY.get(r, 9)),
    })
    if module:
        entry["module"] = module
    notes = details.plain_text(rel.get("description") or "", limit=4000)
    if notes:
        entry["changelog"] = notes

    releases = []
    for r in history:
        b = forever_build(gh, r, c, node, cache)
        if not b:
            continue
        item = {"tag": r.get("tagName", ""), "version": b["version"], "released": r.get("publishedAt"),
                "prerelease": bool(r.get("isPrerelease")), "file": b["file"], "url": b["url"], "size": b["size"]}
        log_text = details.plain_text(r.get("description") or "", limit=4000)
        if log_text:
            item["changelog"] = log_text
        releases.append(item)
        if len(releases) == 5:
            break
    if releases:
        entry["releases"] = releases

    if module:   # the core's TOC and README describe the core; the zip's TOC names the module later
        entry["name"] = "%s: %s" % (node["name"], module)
        fields, readme = {}, ""
    else:
        fields = toc_meta(gh, node, c, entry)
        readme = readme_meta(gh, node, entry)
    cat = details.category(fields.get("x-category", ""), entry["topics"], entry["name"] + " " + entry["summary"])
    if cat:
        entry["category"] = cat
    tag(entry, gamepad, fields, readme)
    return entry


def tag(entry, gamepad, fields=None, readme=""):
    """Set entry["tags"]; the hand-made list wins over the automatic rule."""
    tags = []
    listed = (gamepad or {}).get(entry["repo"].lower())
    if listed is None:
        listed = details.is_gamepad(entry.get("topics"), fields, entry.get("name", ""), entry.get("summary", ""))
        if not listed and details.mentions_gamepad(readme):
            log("gamepad? %s mentions a gamepad in its README; add it to gamepad.txt if it is for one" % entry["repo"])
    if listed:
        tags.append("gamepad")
    if tags:
        entry["tags"] = tags
    else:
        entry.pop("tags", None)


def module_releases(gh, repo):
    """Up to 100 releases, newest first, in the GraphQL shape; [] when GitHub does not answer."""
    try:
        data = gh.json("/repos/%s/releases?per_page=100" % repo)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, RuntimeError):
        return []
    return [{"tagName": r.get("tag_name", ""), "publishedAt": r.get("published_at"), "isPrerelease": bool(r.get("prerelease")),
             "description": r.get("body") or "",
             "releaseAssets": {"nodes": [{"name": a.get("name"), "size": a.get("size"), "downloadCount": a.get("download_count"),
                                          "downloadUrl": a.get("browser_download_url")} for a in r.get("assets") or []]}}
            for r in data if isinstance(r, dict) and not r.get("draft")]


def readme_meta(gh, node, entry):
    branch = (node.get("defaultBranchRef") or {}).get("name", "main")
    base = "%s/%s/%s/" % (RAW, node["nameWithOwner"], branch)
    for name in ("README.md", "readme.md", "Readme.md", "README.MD", "README"):
        try:
            text = gh._request(base + name, accept="text/plain")
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
        desc, shots = details.readme(text, base)
        if desc:
            entry["description"] = desc
        if shots:
            entry["screenshots"] = shots
        return text
    return ""


def toc_meta(gh, node, c, entry):
    """Title, notes, author, folder and other-site ids from the Forever TOC on the default branch."""
    paths = list(c.toc_paths)
    if not paths:
        return {}
    path = sorted(paths, key=lambda p: (p.count("/"), len(p)))[0]
    branch = (node.get("defaultBranchRef") or {}).get("name", "main")
    url = "%s/%s/%s/%s" % (RAW, node["nameWithOwner"], branch, urllib.parse.quote(path))
    try:
        text = gh._request(url, accept="text/plain")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return {}
    f = forever.parse_toc(text)
    if f.get("title"):
        entry["name"] = forever.clean(f["title"]) or entry["name"]
    if f.get("notes"):
        entry["summary"] = forever.clean(f["notes"])
    author = forever.clean_author(f.get("author"))
    if author:
        entry["author"] = author
    folder = path.rsplit("/", 2)[-2] if "/" in path else forever.toc_base(path) or node["name"]
    entry["folders"] = [folder]
    ids = forever.source_ids(f)
    if ids:
        entry["ids"] = ids
    return f


def zip_meta(entry, cache, out_dir):
    """Folders, logo and install checks from the release zip; each zip URL is read once and remembered.

    Returns False when the zip could not be read.
    """
    url = entry.get("url")
    if not url:
        return True
    meta = cache.get(url)
    if meta is None or "tocs" not in meta or "checks" not in meta or (meta.get("icon") and not os.path.exists(os.path.join(out_dir, meta["icon"]))):
        try:
            name = entry["repo"] + ("#" + entry["module"] if entry.get("module") else "")
            meta = icons.from_zip(url, name, entry.get("folders") or [], out_dir, log=log)
        except Exception as ex:   # an odd zip must not stop the run; try again tomorrow
            log("zip %s: %s" % (entry["repo"], ex))
            return False
        cache[url] = meta
    if meta.get("folders"):
        entry["folders"] = meta["folders"]
    if meta.get("icon"):
        entry["icon"] = meta["icon"]
    if entry.get("module") and meta.get("title"):
        entry["name"] = meta["title"]
    entry["_deps"] = meta.get("deps") or []
    entry["_tocs"] = meta.get("tocs")
    return True


def write_review(out_dir, addons, zips, report_path, ignore_path, zip_errors=(), left_out=()):
    """review.json next to the index, and a Markdown report of the new findings for the daily issue."""
    path = os.path.join(out_dir, "review.json")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data, new = review.build(addons, zips, zip_errors, review.load_ignore(ignore_path), load(path, {}), today, left_out)
    write(path, data)
    log("review: %d entries flagged, %d with new findings" % (len(data["addons"]), len(new)))
    if report_path:
        if new:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(review.report(new, len(data["addons"])))
        elif os.path.exists(report_path):
            os.remove(report_path)


def find_dependencies(gh, deps, found, minutes=5):
    """Repos named like a required addon that the index lacks; checked from the next run on.

    Only a repo whose name is the folder name (ignoring case, "-" and "_") counts.
    """
    norm = lambda n: re.sub(r"[-_ .]", "", n.lower())
    gh.search_deadline = time.time() + minutes * 60
    added = 0
    for d in deps[:60]:
        if not re.fullmatch(r"[A-Za-z0-9_.!-]{3,}", d):
            continue
        for it in gh.search("repositories", "%s in:name" % d.lstrip("!"), max_pages=1):
            if norm(it.get("name", "")) == norm(d.lstrip("!")) and not it.get("fork") and not it.get("archived"):
                key = it["full_name"].lower()
                c = found.get(key) or found.setdefault(key, Candidate(it["full_name"]))
                added += "dependency" not in c.reasons
                c.reasons.add("dependency")
    log("dependencies: %d missing, %d repos added as candidates" % (len(deps), added))


def link_requires(addons):
    """entry["requires"]: the keys of other listed entries that ship a folder the entry's TOCs require.

    A folder shipped by more than one repo stays unresolved, except when one of them is the entry's own repo.
    """
    owners = {}
    for e in addons:
        for f in e.get("folders") or []:
            owners.setdefault(f.lower(), []).append(e)
    for e in addons:
        req = []
        for d in e.pop("_deps", []):
            cands = [o for o in owners.get(d.lower(), []) if o is not e]
            same = [o for o in cands if o["repo"].lower() == e["repo"].lower()]
            pick = same[0] if same else cands[0] if len(cands) == 1 else None
            if pick is not None and entry_key(pick) not in req:
                req.append(entry_key(pick))
        if req:
            e["requires"] = req
        else:
            e.pop("requires", None)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="site")
    p.add_argument("--seeds", default="seeds.txt")
    p.add_argument("--gamepad", default="gamepad.txt")
    p.add_argument("--max-checks", type=int, default=int(os.environ.get("MAX_CHECKS", "5000")),
                   help="limit for new candidates; addons already listed are always checked")
    p.add_argument("--search-minutes", type=int, default=int(os.environ.get("SEARCH_MINUTES", "45")),
                   help="time for searches; candidates from earlier runs are checked either way")
    p.add_argument("--min-ratio", type=float, default=0.7, help="refuse to publish if the index shrinks below this share")
    p.add_argument("--tags-only", action="store_true",
                   help="only update the tags of the addons in the existing index.json, without GitHub calls")
    p.add_argument("--review-only", action="store_true",
                   help="only check the release zips of the existing index.json and write review.json, without a token")
    p.add_argument("--report", default="", help="write the new review findings here as Markdown (for the daily issue)")
    p.add_argument("--review-ignore", default="review-ignore.txt")
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    state_path = os.path.join(args.out, "state.json")
    index_path = os.path.join(args.out, "index.json")
    state = load(state_path, {})
    old = load(index_path, {"addons": []})

    if args.tags_only:
        gamepad = load_gamepad(args.gamepad)
        for e in old.get("addons") or []:
            tag(e, gamepad)
        write(index_path, old)
        log("tags: %d of %d addons tagged gamepad"
            % (sum("gamepad" in e.get("tags", []) for e in old.get("addons") or []), len(old.get("addons") or [])))
        return 0

    if args.review_only:
        addons = old.get("addons") or []
        zips = state.setdefault("zips", {})
        errors = []
        for i, e in enumerate(addons):
            if not zip_meta(dict(e), zips, args.out):
                errors.append(review.entry_key(e))
            if i % 100 == 0:
                log("zips checked: %d/%d" % (i, len(addons)))
        write_review(args.out, addons, zips, args.report, args.review_ignore, errors)
        write(state_path, state)
        return 0

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log("GITHUB_TOKEN is not set; code search and GraphQL need it")
        return 1
    gh = GitHub(token)
    seeds = [l.split("#")[0].strip() for l in open(args.seeds, encoding="utf-8")] if os.path.exists(args.seeds) else []
    seeds = [s for s in seeds if "/" in s]
    gamepad = load_gamepad(args.gamepad)
    seeds += [r for r, tag in gamepad.items() if tag]

    gh.search_deadline = time.time() + args.search_minutes * 60
    found = discover(gh, seeds, state.get("candidates"))
    state["candidates"] = remember(found)
    listed = {a["repo"].lower(): a for a in old.get("addons") or [] if a.get("repo")}
    listed_all = {}
    for a in old.get("addons") or []:
        if a.get("repo"):
            listed_all.setdefault(a["repo"].lower(), []).append(a)
    ordered = select(found, listed, args.max_checks)
    failed = set()
    nodes = fetch_repos(gh, ordered, failed=failed)
    log("repos fetched: %d of %d, api calls %d" % (len(nodes), len(ordered), gh.calls))
    cache = state.setdefault("release_json", {})
    addons, errors, kept, seen = [], 0, 0, set()
    for c in ordered:
        key = c.repo.lower()
        node = nodes.get(key)
        got = []
        if node:
            try:
                got = check(gh, c, node, cache, gamepad)
            except Exception as ex:  # one bad repo must not stop the run
                errors += 1
                log("error %s: %s" % (c.repo, ex))
                got = listed_all.get(key, [])
                kept += len(got)
        elif key in failed and key in listed_all:
            got = listed_all[key]   # not answered this time: keep yesterday's entries
            kept += len(got)
        for e in got:
            if entry_key(e) not in seen:   # renamed repos can show up twice
                seen.add(entry_key(e))
                addons.append(e)

    zips = state.setdefault("zips", {})
    zip_errors = []
    for i, e in enumerate(addons):
        if not zip_meta(e, zips, args.out):
            zip_errors.append(review.entry_key(e))
        if i % 100 == 0:
            log("zips read: %d/%d" % (i, len(addons)))
    no_addon = [e for e in addons if e.get("_tocs") == 0]
    for e in no_addon:
        log("left out %s: its zip %s has no addon (.toc) to install" % (entry_key(e), e["file"]))
    addons = [e for e in addons if e.get("_tocs") != 0]
    for e in addons:
        e.pop("_tocs", None)
        if not e.get("folders"):   # addon folders deeper than the zip's top level
            e["folders"] = ((zips.get(e["url"]) or {}).get("checks") or {}).get("roots") or []
    left = review.doubtful(addons, zips)
    for e in left:
        log("left out %s: %s" % (entry_key(e), e["_left_detail"]))
    addons = [e for e in addons if not any(e is x for x in left)]
    no_addon += left
    find_dependencies(gh, review.unresolved_deps(addons, zips), found)
    state["candidates"] = remember(found)
    link_requires(addons)
    zips_live = {e["url"] for e in addons}
    state["zips"] = {u: v for u, v in zips.items() if u in zips_live}
    used = {e["icon"] for e in addons if e.get("icon")}
    icon_dir = os.path.join(args.out, "icons")
    for f in os.listdir(icon_dir) if os.path.isdir(icon_dir) else []:
        if "icons/" + f not in used:
            os.remove(os.path.join(icon_dir, f))

    addons.sort(key=lambda a: (-a.get("stars", 0), a["repo"].lower()))
    old_n = len(old.get("addons") or [])
    if old_n > 20 and len(addons) < old_n * args.min_ratio:
        log("index shrank from %d to %d; not publishing" % (old_n, len(addons)))
        return 2

    write_review(args.out, addons, zips, args.report, args.review_ignore, zip_errors, no_addon)
    out = {"schema": 1, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "count": len(addons), "addons": addons}
    write(index_path, out)
    live = {a["url"].rsplit("/", 1)[0] for a in addons if a.get("url")}
    state["release_json"] = {u: v for u, v in cache.items() if u.rsplit("/", 1)[0] in live}
    write(state_path, state)
    log("done: %d addons (%d kept from the last run), %d candidates, %d errors, %d api calls"
        % (len(addons), kept, len(found), errors, gh.calls))
    return 0


def load_gamepad(path):
    """gamepad.txt: "owner/repo" tags a repo as made for a gamepad, "-owner/repo" never tags it."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if "/" in line:
                out[line.lstrip("-").strip().lower()] = not line.startswith("-")
    return out


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
