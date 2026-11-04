import unittest

from indexer import forever


class ReleaseJson(unittest.TestCase):
    def rel(self, name, *flavors, nolib=False):
        return {"filename": name, "nolib": nolib, "metadata": [{"flavor": f} for f in flavors]}

    def test_prefers_forever_over_camelot(self):
        data = {"releases": [self.rel("a-camelot.zip", "camelot"), self.rel("a-forever.zip", "forever")]}
        self.assertEqual(forever.pick_from_release_json(data)["filename"], "a-forever.zip")

    def test_prefers_build_with_libraries(self):
        data = {"releases": [self.rel("a-nolib.zip", "forever", nolib=True), self.rel("a.zip", "forever")]}
        self.assertEqual(forever.pick_from_release_json(data)["filename"], "a.zip")

    def test_no_forever_build(self):
        data = {"releases": [self.rel("a.zip", "mainline"), self.rel("a-classic.zip", "classic")]}
        self.assertIsNone(forever.pick_from_release_json(data))

    def test_bad_input(self):
        self.assertIsNone(forever.pick_from_release_json(None))
        self.assertIsNone(forever.pick_from_release_json({"releases": [None, "x", {"metadata": [None]}]}))


class Assets(unittest.TestCase):
    def names(self, *names):
        return [{"name": n} for n in names]

    def test_named_forever_is_sure(self):
        a, guessed = forever.pick_from_assets(self.names("A-1.0-classic.zip", "A-1.0-Forever.zip"))
        self.assertEqual((a["name"], guessed), ("A-1.0-Forever.zip", False))

    def test_plain_zip_is_a_guess(self):
        a, guessed = forever.pick_from_assets(self.names("A-1.0-classic.zip", "A-1.0-nolib.zip", "A-1.0.zip"))
        self.assertEqual((a["name"], guessed), ("A-1.0.zip", True))

    def test_skips_desktop_programs(self):
        self.assertEqual(forever.pick_from_assets(self.names("wow-ui-sim-v0.1.39-forever-windows-x86_64.zip",
                                                             "TypeWhisper-1.2.2-win-x64-candidate.zip")), (None, False))
        self.assertFalse(forever.is_app_binary("Questie-v12.4.0+v1.0.10.zip"))
        self.assertFalse(forever.is_app_binary("Masque-12.1.8.zip"))

    def test_clean_author_drops_email(self):
        self.assertEqual(forever.clean_author("MooreaTV moorea@ymail.com"), "MooreaTV")
        self.assertEqual(forever.clean_author("Jane Doe <jane.doe@example.org>"), "Jane Doe")
        self.assertEqual(forever.clean_author("Bob (mailto:bob@x.io), Ann"), "Bob, Ann")
        self.assertEqual(forever.clean_author("|cffff0000Tekkub|r"), "Tekkub")
        self.assertEqual(forever.clean_author("someone@example.com"), "")
        self.assertEqual(forever.clean_author("@project-author@"), "")
        self.assertEqual(forever.clean_author("Yaxal @ Spineshatter"), "Yaxal @ Spineshatter")
        self.assertEqual(forever.clean("Quester @project-version@"), "Quester")

    def test_only_other_flavors(self):
        self.assertEqual(forever.pick_from_assets(self.names("A-classic.zip", "notes.txt")), (None, False))


class TocPath(unittest.TestCase):
    def test_forever_toc_of_its_folder(self):
        self.assertTrue(forever.is_forever_toc_path("Bagnon/Bagnon_Forever.toc"))
        self.assertTrue(forever.is_forever_toc_path("src/Questie/Questie_Camelot.toc"))
        self.assertTrue(forever.is_forever_toc_path("MyAddon-Forever.toc"))

    def test_addon_named_forever_is_not_forever_toc(self):
        self.assertFalse(forever.is_forever_toc_path("Bagnon_Forever/Bagnon_Forever.toc"))

    def test_other_suffixes(self):
        self.assertFalse(forever.is_forever_toc_path("A/A_Mainline.toc"))
        self.assertFalse(forever.is_forever_toc_path("A/A.toc"))


class Samples(unittest.TestCase):
    def test_fixture_paths(self):
        self.assertTrue(forever.is_sample_path("crates/core/tests/fixtures/A/A_Forever.toc"))
        self.assertTrue(forever.is_sample_path("template/Hello/Hello_Forever.toc"))
        self.assertFalse(forever.is_sample_path("Questie/Questie_Forever.toc"))

    def test_guess_needs_addon_name(self):
        self.assertTrue(forever.guess_matches("Quester-1.2.zip", ["Quester"]))
        self.assertFalse(forever.guess_matches("mrgl-windows-x64.zip", ["SoloAddon", "mrgl-core"]))


class Toc(unittest.TestCase):
    TEXT = ("﻿## Interface: 16001, 110200\n## Title: |cff33ff99My|r Addon |TInterface\\\\Icons\\\\x:16|t\n"
            "## Notes: Does things\n## X-Curse-Project-ID: 123\n## X-WoWI-ID: 0\n## X-Wago-ID: abC9\nfile.lua\n")

    def test_fields_and_ids(self):
        f = forever.parse_toc(self.TEXT)
        self.assertEqual(forever.clean(f["title"]), "My Addon")
        self.assertTrue(forever.interface_is_forever(f["interface"]))
        self.assertEqual(forever.source_ids(f), {"curseforge": "123", "wago": "abC9"})

    def test_version_from_file(self):
        self.assertEqual(forever.version_from_file("Questie-v10.2.3.zip"), "v10.2.3")


class Modules(unittest.TestCase):
    def rel(self, tag, pre=False):
        return {"tagName": tag, "isPrerelease": pre}

    def test_module_tags(self):
        self.assertEqual(forever.module_of("Wheel-v0.10.0"), "Wheel")
        self.assertEqual(forever.module_of("Core-v1.2"), "Core")
        self.assertIsNone(forever.module_of("v1.2"))
        self.assertIsNone(forever.module_of("bundle/v12.4.0"))

    def test_core_of_a_module_repo(self):
        rels = [self.rel("Camp-v0.1.0"), self.rel("Core-v0.9.0-beta1", pre=True), self.rel("Wheel-v0.10.0"),
                self.rel("Core-v0.8.2"), self.rel("v0.1")]
        self.assertEqual(forever.core_release(rels, "ImprovedForever")["tagName"], "Core-v0.8.2")
        named = [self.rel("Wheel-v2"), self.rel("ImprovedForever-v1")]
        self.assertEqual(forever.core_release(named, "ImprovedForever")["tagName"], "ImprovedForever-v1")

    def test_plain_repos_are_not_module_repos(self):
        self.assertIsNone(forever.core_release([self.rel("Questie-v12"), self.rel("Questie-v11")], "Questie"))
        self.assertIsNone(forever.core_release([self.rel("v2"), self.rel("v1")], "X"))


if __name__ == "__main__":
    unittest.main()
