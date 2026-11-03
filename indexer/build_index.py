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

from . import forever

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "runeloader-index"

# Code search: the most precise signals first. Each query is also split by file
# size, because one search returns at most 1000 results.
SIZE_SLICES = ("size:<600", "size:600..1200", "size:1201..2500", "size:>2500")
TOC_QUERIES = ("filename:_Forever.toc", "filename:_Camelot.toc", "filename:-Forever.toc",
               '"## Interface: 160" extension:toc')
PACKAGER_QUERIES = ("BigWigsMods/packager path:.github/workflows",)
REPO_QUERIES = ("topic:wow-addon", "topic:world-of-warcraft-addon", "topic:wow-addons",
                "topic:worldofwarcraft-addon", "topic:wow-forever", "wow forever addon in:name,description,readme")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class GitHub:
    def __init__(self, token):
        self.token = token
        self.calls = 0
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
                if e.code in (403, 429) and self._wait_for_limit(e):
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

    def _wait_for_limit(self, e):
        reset = e.headers.get("X-RateLimit-Reset")
        retry = e.headers.get("Retry-After")
        if retry:
            wait = int(retry)
        elif reset and e.headers.get("X-RateLimit-Remaining") == "0":
            wait = max(1, int(reset) - int(time.time()) + 2)
        else:
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
        """Yield items of a code or repo search, 100 per page, up to 1000."""
        for page in range(1, max_pages + 1):
            gap = 6.5 if kind == "code" else 2.2      # code search: 10/min, repo search: 30/min
            time.sleep(max(0.0, self.last_search + gap - time.time()))
            self.last_search = time.time()
            url = "%s/search/%s?q=%s&per_page=100&page=%d" % (API, kind, urllib.parse.quote(q), page)
            try:
                data = self.json(url)
            except urllib.error.HTTPError as e:
                log("search failed (%s): %s" % (e.code, q))
                return
            items = data.get("items") or []
            yield from items
            if len(items) < 100:
                return


class Candidate:
    def __init__(self, repo):
        self.repo = repo
        self.reasons = set()
        self.toc_paths = []   # Forever TOCs seen in code search


def discover(gh, seeds):
    found = {}

    def add(repo, reason, toc=None):
        key = repo.lower()
        c = found.get(key) or found.setdefault(key, Candidate(repo))
        c.reasons.add(reason)
        if toc and toc not in c.toc_paths:
            c.toc_paths.append(toc)

    for repo in seeds:
        add(repo, "seed")
    for q in TOC_QUERIES:
        for s in SIZE_SLICES:
            for it in gh.search("code", "%s %s" % (q, s)):
                path = it.get("path", "")
                repo = (it.get("repository") or {}).get("full_name")
                if not repo:
                    continue
                if forever.is_sample_path(path):
                    continue
                if q.startswith('"## Interface') or forever.is_forever_toc_path(path):
                    add(repo, "toc", path)
    for q in PACKAGER_QUERIES:
        for s in SIZE_SLICES:
            for it in gh.search("code", "%s %s" % (q, s)):
                repo = (it.get("repository") or {}).get("full_name")
                if repo:
                    add(repo, "packager")
    for q in REPO_QUERIES:
        for it in gh.search("repositories", q):
            if not it.get("fork") and not it.get("archived"):
                add(it["full_name"], "topic")
    log("candidates: %d" % len(found))
    return found


PRIORITY = {"seed": 0, "toc": 1, "topic": 2, "packager": 3}

REPO_FIELDS = """
  nameWithOwner name description stargazerCount isArchived isFork
  owner { login } defaultBranchRef { name }
  repositoryTopics(first: 10) { nodes { topic { name } } }
  latestRelease { tagName publishedAt isPrerelease
    releaseAssets(first: 50) { nodes { name size downloadCount downloadUrl } } }
"""


def fetch_repos(gh, candidates, batch=40):
    """Repo metadata and latest release for many repos, 40 per GraphQL query.

    One query costs about one rate-limit point, so a few thousand repos fit in the
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


def check(gh, c, node, cache):
    """Return an index entry for a candidate, or None when it has no Forever build."""
    if node.get("isArchived") or (node.get("isFork") and "seed" not in c.reasons):
        return None
    rel = node.get("latestRelease")
    if not rel:
        return None
    assets = (rel.get("releaseAssets") or {}).get("nodes") or []
    entry = None
    rj_asset = next((a for a in assets if a.get("name") == "release.json"), None)
    if rj_asset:
        url = rj_asset["downloadUrl"]
        if url not in cache:
            try:
                cache[url] = forever.pick_from_release_json(json.loads(gh._request(url, accept="application/json")))
            except (ValueError, urllib.error.HTTPError, urllib.error.URLError):
                return None   # try again next run
        pick = cache[url]
        if pick:
            fname = pick.get("filename", "")
            asset = next((a for a in assets if a.get("name") == fname), None)
            if asset:
                entry = {"file": fname, "version": pick.get("version") or forever.version_from_file(fname),
                         "verified": True, "size": asset.get("size"), "downloads": asset.get("downloadCount"),
                         "url": asset.get("downloadUrl")}
    if entry is None and not rj_asset:
        asset, guessed = forever.pick_from_assets(assets)
        # A guessed zip counts only when the repo itself ships a Forever TOC.
        names = [node["name"]] + [p.rsplit("/", 2)[-2] if "/" in p else node["name"] for p in c.toc_paths]
        if asset and (not guessed or ("toc" in c.reasons and forever.guess_matches(asset["name"], names))):
            entry = {"file": asset["name"], "version": rel.get("tagName") or forever.version_from_file(asset["name"]),
                     "verified": not guessed, "size": asset.get("size"), "downloads": asset.get("downloadCount"),
                     "url": asset.get("downloadUrl")}
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
    toc_meta(gh, node, c, entry)
    return entry


def toc_meta(gh, node, c, entry):
    """Title, notes, author, folder and other-site ids from the Forever TOC on the default branch."""
    paths = list(c.toc_paths)
    if not paths:
        return
    path = sorted(paths, key=lambda p: (p.count("/"), len(p)))[0]
    branch = (node.get("defaultBranchRef") or {}).get("name", "main")
    url = "%s/%s/%s/%s" % (RAW, node["nameWithOwner"], branch, urllib.parse.quote(path))
    try:
        text = gh._request(url, accept="text/plain")
    except urllib.error.HTTPError:
        return
    f = forever.parse_toc(text)
    if f.get("title"):
        entry["name"] = forever.clean(f["title"]) or entry["name"]
    if f.get("notes"):
        entry["summary"] = forever.clean(f["notes"])
    if f.get("author"):
        entry["author"] = forever.clean(f["author"])
    folder = path.rsplit("/", 2)[-2] if "/" in path else node["name"]
    entry["folders"] = [folder]
    ids = forever.source_ids(f)
    if ids:
        entry["ids"] = ids


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="site")
    p.add_argument("--seeds", default="seeds.txt")
    p.add_argument("--max-checks", type=int, default=int(os.environ.get("MAX_CHECKS", "2500")))
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

    found = discover(gh, seeds)
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
