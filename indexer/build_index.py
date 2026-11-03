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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import details, forever

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "runeloader-index"

# Code search: the most precise signals first. Each query is also split by file
# size, because one search returns at most 1000 results.
SIZE_SLICES = ("size:<600", "size:600..1200", "size:1201..2500", "size:>2500")
TOC_QUERIES = ("filename:_Forever.toc", "filename:_Camelot.toc", '"## Interface: 160" extension:toc')
PACKAGER_QUERIES = ("BigWigsMods/packager path:.github/workflows",)
REPO_QUERIES = ("topic:wow-addon", "topic:world-of-warcraft-addon", "topic:wow-addons",
                "topic:worldofwarcraft-addon", "topic:wow-forever", "wow forever addon in:name,description,readme")


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


PRIORITY = {"seed": 0, "toc": 1, "topic": 2, "packager": 3}

RELEASE_FIELDS = "tagName publishedAt isPrerelease description releaseAssets(first: 40) { nodes { name size downloadCount downloadUrl } }"

REPO_FIELDS = """
  nameWithOwner name description stargazerCount isArchived isFork
  owner { login } defaultBranchRef { name }
  repositoryTopics(first: 10) { nodes { topic { name } } }
  latestRelease { %s }
  releases(first: 6, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { %s } }
""" % ((RELEASE_FIELDS,) * 2)


def fetch_repos(gh, candidates, batch=30):
    """Repo metadata and recent releases for many repos, 30 per GraphQL query.

    One query costs a few rate-limit points, so a few thousand repos fit in the
    1000 points per hour the Actions token gets.
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


def check(gh, c, node, cache):
    """Return an index entry for a candidate, or None when it has no Forever build."""
    if node.get("isArchived") or (node.get("isFork") and "seed" not in c.reasons):
        return None
    rel = node.get("latestRelease")
    if not rel:
        return None
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
    notes = details.plain_text(rel.get("description") or "", limit=4000)
    if notes:
        entry["changelog"] = notes

    releases = []
    for r in (node.get("releases") or {}).get("nodes") or []:
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

    fields = toc_meta(gh, node, c, entry)
    readme_meta(gh, node, entry)
    cat = details.category(fields.get("x-category", ""), entry["topics"], entry["name"] + " " + entry["summary"])
    if cat:
        entry["category"] = cat
    return entry


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
        return


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
    if f.get("author"):
        entry["author"] = forever.clean(f["author"])
    folder = path.rsplit("/", 2)[-2] if "/" in path else forever.toc_base(path) or node["name"]
    entry["folders"] = [folder]
    ids = forever.source_ids(f)
    if ids:
        entry["ids"] = ids
    return f


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="site")
    p.add_argument("--seeds", default="seeds.txt")
    p.add_argument("--max-checks", type=int, default=int(os.environ.get("MAX_CHECKS", "2500")))
    p.add_argument("--search-minutes", type=int, default=int(os.environ.get("SEARCH_MINUTES", "45")),
                   help="time for searches; candidates from earlier runs are checked either way")
    p.add_argument("--min-ratio", type=float, default=0.7, help="refuse to publish if the index shrinks below this share")
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    state_path = os.path.join(args.out, "state.json")
    index_path = os.path.join(args.out, "index.json")
    state = load(state_path, {})
    old = load(index_path, {"addons": []})

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log("GITHUB_TOKEN is not set; code search and GraphQL need it")
        return 1
    gh = GitHub(token)
    seeds = [l.split("#")[0].strip() for l in open(args.seeds, encoding="utf-8")] if os.path.exists(args.seeds) else []
    seeds = [s for s in seeds if "/" in s]

    gh.search_deadline = time.time() + args.search_minutes * 60
    found = discover(gh, seeds, state.get("candidates"))
    state["candidates"] = remember(found)
    ordered = sorted(found.values(), key=lambda c: (min(PRIORITY.get(r, 9) for r in c.reasons), c.repo.lower()))
    ordered = ordered[:args.max_checks]
    nodes = fetch_repos(gh, ordered)
    log("repos fetched: %d of %d, api calls %d" % (len(nodes), len(ordered), gh.calls))
    cache = state.setdefault("release_json", {})
    addons, errors, seen = [], 0, set()
    for c in ordered:
        node = nodes.get(c.repo.lower())
        if not node:
            continue
        try:
            e = check(gh, c, node, cache)
        except Exception as ex:  # one bad repo must not stop the run
            errors += 1
            log("error %s: %s" % (c.repo, ex))
            continue
        if e and e["repo"].lower() not in seen:   # renamed repos can show up twice
            seen.add(e["repo"].lower())
            addons.append(e)

    addons.sort(key=lambda a: (-a.get("stars", 0), a["repo"].lower()))
    old_n = len(old.get("addons") or [])
    if old_n > 20 and len(addons) < old_n * args.min_ratio:
        log("index shrank from %d to %d; not publishing" % (old_n, len(addons)))
        return 2

    out = {"schema": 1, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "count": len(addons), "addons": addons}
    write(index_path, out)
    live = {a["url"].rsplit("/", 1)[0] for a in addons if a.get("url")}
    state["release_json"] = {u: v for u, v in cache.items() if u.rsplit("/", 1)[0] in live}
    write(state_path, state)
    log("done: %d addons, %d candidates, %d errors, %d api calls" % (len(addons), len(found), errors, gh.calls))
    return 0


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
