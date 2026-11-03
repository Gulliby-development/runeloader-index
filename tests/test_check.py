import json
import unittest

from indexer import build_index as b


class FakeGitHub:
    def __init__(self, files):
        self.files = files

    def _request(self, url, accept=None):
        if url in self.files:
            return self.files[url]
        import urllib.error
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)


def release(tag, *assets, pre=False, notes=""):
    return {"tagName": tag, "publishedAt": "2026-11-01T00:00:00Z", "isPrerelease": pre, "description": notes,
            "releaseAssets": {"nodes": [{"name": n, "size": 10, "downloadCount": 5,
                                         "downloadUrl": "https://github.com/o/Quester/releases/download/%s/%s" % (tag, n)}
                                        for n in assets]}}


class Check(unittest.TestCase):
    def test_entry_with_release_json_toc_and_readme(self):
        rj = {"releases": [{"filename": "Quester-2.0.zip", "version": "2.0", "nolib": False,
                            "metadata": [{"flavor": "forever"}]}]}
        latest = release("v2.0", "Quester-2.0.zip", "release.json", notes="## Changes\n- Fixed **bug**")
        node = {"nameWithOwner": "o/Quester", "name": "Quester", "description": "Quests", "stargazerCount": 7,
                "isArchived": False, "isFork": False, "owner": {"login": "o"}, "defaultBranchRef": {"name": "main"},
                "repositoryTopics": {"nodes": [{"topic": {"name": "wow-addon"}}, {"topic": {"name": "quests"}}]},
                "latestRelease": latest,
                "releases": {"nodes": [release("v2.1-beta", "Quester-2.1-classic.zip", pre=True), latest]}}
        gh = FakeGitHub({
            "https://github.com/o/Quester/releases/download/v2.0/release.json": json.dumps(rj),
            "https://raw.githubusercontent.com/o/Quester/main/Quester/Quester_Forever.toc":
                "## Title: Quester\n## Notes: Quest helper\n## X-Curse-Project-ID: 42\n",
            "https://raw.githubusercontent.com/o/Quester/main/README.md": "# Quester\n\nHelps with quests.\n",
        })
        c = b.Candidate("o/Quester")
        c.reasons.add("toc")
        c.toc_paths.append("Quester/Quester_Forever.toc")
        e = b.check(gh, c, node, {})
        self.assertEqual(e["version"], "2.0")
        self.assertTrue(e["verified"])
        self.assertEqual(e["summary"], "Quest helper")
        self.assertEqual(e["ids"], {"curseforge": "42"})
        self.assertEqual(e["folders"], ["Quester"])
        self.assertEqual(e["category"], "quests")
        self.assertEqual(e["description"], "Helps with quests.")
        self.assertIn("• Fixed bug", e["changelog"])
        self.assertEqual([r["tag"] for r in e["releases"]], ["v2.0"])   # the beta has no Forever build

    def test_no_forever_build(self):
        node = {"nameWithOwner": "o/X", "name": "X", "isArchived": False, "isFork": False,
                "latestRelease": release("1", "X-classic.zip"), "releases": {"nodes": []}}
        c = b.Candidate("o/X")
        c.reasons.add("topic")
        self.assertIsNone(b.check(FakeGitHub({}), c, node, {}))


class Discover(unittest.TestCase):
    def test_keeps_earlier_candidates_when_search_fails(self):
        class NoSearch:
            last_total = 0

            def search(self, kind, q, max_pages=10):
                return iter(())
        known = {"o/a": {"repo": "o/A", "reasons": ["toc"], "toc_paths": ["A/A_Forever.toc"]}}
        found = b.discover(NoSearch(), ["s/Seed"], known)
        self.assertEqual(sorted(c.repo for c in found.values()), ["o/A", "s/Seed"])
        self.assertEqual(found["o/a"].toc_paths, ["A/A_Forever.toc"])
        self.assertEqual(b.remember(found), {"o/a": {"repo": "o/A", "reasons": ["toc"], "toc_paths": ["A/A_Forever.toc"]}})


class Select(unittest.TestCase):
    def test_listed_addons_come_first_and_skip_the_limit(self):
        found = {}
        for r, why in (("n/New1", "toc"), ("n/New2", "packager"), ("o/Old", "packager")):
            found[r.lower()] = b.Candidate(r)
            found[r.lower()].reasons.add(why)
        listed = {"o/old": {"repo": "o/Old"}, "o/lost": {"repo": "o/Lost", "found_by": ["toc"]}}
        got = [c.repo for c in b.select(found, listed, 1)]
        self.assertEqual(got, ["o/Lost", "o/Old", "n/New1"])


class Run(unittest.TestCase):
    def test_keeps_listed_addons_when_github_does_not_answer(self):
        import os
        import tempfile

        class Down:
            last_total = 0
            calls = 0
            search_deadline = 0

            def __init__(self, token):
                pass

            def search(self, kind, q, max_pages=10):
                return iter(())

            def graphql(self, q):
                raise RuntimeError("graphql gave up")

        old = [{"repo": "o/A%d" % i, "url": "https://github.com/o/A%d/releases/download/v1/a.zip" % i, "stars": i,
                "found_by": ["toc"]} for i in range(30)]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "index.json"), "w") as f:
                json.dump({"addons": old}, f)
            real_gh, real_zip, env = b.GitHub, b.zip_meta, os.environ.get("GITHUB_TOKEN")
            b.GitHub, b.zip_meta = Down, lambda e, cache, out: None
            os.environ["GITHUB_TOKEN"] = "t"
            try:
                rc = b.main(["--out", d, "--seeds", os.path.join(d, "none.txt"), "--search-minutes", "0"])
            finally:
                b.GitHub, b.zip_meta = real_gh, real_zip
                if env is None:
                    del os.environ["GITHUB_TOKEN"]
                else:
                    os.environ["GITHUB_TOKEN"] = env
            self.assertEqual(rc, 0)
            with open(os.path.join(d, "index.json")) as f:
                self.assertEqual(json.load(f)["count"], 30)


if __name__ == "__main__":
    unittest.main()
