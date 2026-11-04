import unittest

from indexer import review


def zip_of(files):
    """RemoteZip-like entries and a reader from {name: text}."""
    entries = {n.lower(): (n,) for n in files}
    return entries, lambda n: files[n].encode()


class ZipChecks(unittest.TestCase):
    def test_clean_forever_addon(self):
        e, r = zip_of({"Bags/Bags_Forever.toc": "## Interface: 16001\n## Title: Bags\nBags.lua\nLocale\\enUS.lua\n",
                       "Bags/Bags.lua": "", "Bags/Locale/enUS.lua": ""})
        res = review.zip_checks(e, r)
        self.assertEqual(res["roots"], ["Bags"])
        self.assertEqual(res["problems"], [])

    def test_no_addon_folder(self):
        e, r = zip_of({"README.md": "", "src/main.lua": ""})
        self.assertEqual(review.zip_checks(e, r)["problems"], [["no_addon", ""]])

    def test_retail_only_toc_and_placeholders(self):
        e, r = zip_of({"Map/Map.toc": "## Interface: 110200\n## Version: @project-version@\n## Author: @project-author@\nMap.lua\n",
                       "Map/Map.lua": ""})
        codes = {p[0]: p[1] for p in review.zip_checks(e, r)["problems"]}
        self.assertIn("Map (Interface 110200)", codes["no_forever_toc"])
        self.assertEqual(codes["placeholder"], "@project-author@ @project-version@")

    def test_mainline_toc_with_forever_interface_is_fine(self):
        e, r = zip_of({"Map/Map_Mainline.toc": "## Interface: 160010\nMap.lua\n", "Map/Map.lua": ""})
        self.assertEqual(review.zip_checks(e, r)["problems"], [])

    def test_missing_files_skip_comments_and_variables(self):
        e, r = zip_of({"Q/Q_Forever.toc": "#@no-lib-strip@\nLibs\\Lib.xml\n#@end-no-lib-strip@\nQ.lua\nGone.lua\n"
                                          "Core_[Family].lua\n", "Q/Q.lua": ""})
        p = dict(review.zip_checks(e, r)["problems"])
        self.assertEqual(p["missing_files"], "Q/Libs/Lib.xml, Q/Gone.lua")

    def test_dependencies_outside_the_zip(self):
        e, r = zip_of({"Wheel/Wheel_Forever.toc": "## Dependencies: Core, Blizzard_UI\n## OptionalDeps: Masque\n",
                       "Pack/Pack_Forever.toc": "## RequiredDeps: Wheel\n"})
        self.assertEqual(review.zip_checks(e, r)["deps"], ["Core"])

    def test_wrapped_repo_archive(self):
        e, r = zip_of({"o-Bags-1a2b/Bags.toc": "## Interface: 16001\n"})
        res = review.zip_checks(e, r)
        self.assertEqual(res["roots"], ["Bags"])
        self.assertEqual(res["problems"], [])


class Build(unittest.TestCase):
    def addon(self, repo, url, folders, verified=True):
        return {"repo": repo, "url": url, "version": "1", "folders": folders, "verified": verified, "file": "x.zip"}

    def test_findings_new_and_seen(self):
        a = self.addon("o/A", "u1", ["Shared"])
        b = self.addon("o/B", "u2", ["Shared", "B"], verified=False)
        c = self.addon("o/C", "u3", ["C"])
        zips = {"u1": {"checks": {"problems": [], "deps": ["Core"]}}, "u2": {"checks": {"problems": [], "deps": ["C"]}}}
        data, new = review.build([a, b, c], zips, today="2026-11-04")
        p = {r: [x[0] for x in v["problems"]] for r, v in data["addons"].items()}
        self.assertEqual(p, {"o/A": ["missing_dependency", "shared_folder"], "o/B": ["shared_folder", "guessed"]})
        self.assertEqual(len(new), 2)

        # next day, same releases: nothing new; a new release brings its findings back
        data2, new2 = review.build([a, b, c], zips, old=data, today="2026-11-05")
        self.assertEqual(new2, [])
        self.assertEqual(data2["addons"]["o/A"]["since"], "2026-11-04")
        b2 = dict(b, url="u9")
        _, new3 = review.build([a, b2, c], zips, old=data, today="2026-11-05")
        self.assertEqual([n[0] for n in new3], ["o/B"])

    def test_ignore(self):
        b = self.addon("o/B", "u2", ["B"], verified=False)
        data, new = review.build([b], {}, zip_errors=["o/B"], ignore={"o/b": {"guessed"}})
        self.assertEqual(data["addons"]["o/B"]["problems"], [["zip_error", ""]])
        data, new = review.build([b], {}, ignore={"o/b": {"*"}})
        self.assertEqual((data["addons"], new), ({}, []))

    def test_modules_and_left_out(self):
        core = self.addon("o/Pack", "u1", ["Pack"])
        wheel = dict(self.addon("o/Pack", "u2", ["Pack", "Pack_Wheel"]), module="Wheel")
        other = self.addon("x/Fork", "u3", ["Pack_Wheel"])
        gone = dict(self.addon("o/Docs", "u4", []), file="docs.zip")
        data, new = review.build([core, wheel, other], {}, left_out=[gone])
        self.assertNotIn("o/Pack", data["addons"])   # its own modules may share the core folder
        self.assertEqual(data["addons"]["o/Pack#Wheel"]["problems"], [["shared_folder", "x/Fork"]])
        self.assertEqual(data["addons"]["o/Docs"]["problems"], [["no_addon", "docs.zip"]])
        self.assertIn("[o/Pack#Wheel](https://github.com/o/Pack)", review.report(new, 3))

    def test_report_is_cut(self):
        new = [("o/R%d" % i, "1", [["placeholder", "@x@ " * 50]]) for i in range(500)]
        text = review.report(new, 500, limit=5000)
        self.assertLess(len(text), 5200)
        self.assertIn("The list is cut", text)
        self.assertTrue(set(review.CHECKS) >= {"placeholder"})


if __name__ == "__main__":
    unittest.main()
