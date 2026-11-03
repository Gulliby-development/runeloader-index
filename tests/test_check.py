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


if __name__ == "__main__":
    unittest.main()
