import unittest

from indexer import details

README = """# Questie

[![Build](https://img.shields.io/badge/build-ok-green.svg)](x)

Questie shows quests on the map.

![Map](docs/map.png)
<img src="https://example.com/shot.jpg" width="400">

## Features
- Quest **markers**
- Tracker with [links](https://x.y)

## Installation
Unzip into Interface/AddOns.

![Ignored](later.png)
"""


class Readme(unittest.TestCase):
    def test_cuts_at_install_and_collects_screenshots(self):
        desc, shots = details.readme(README, "https://raw.githubusercontent.com/Questie/Questie/master/")
        self.assertIn("Questie shows quests on the map.", desc)
        self.assertIn("• Quest markers", desc)
        self.assertIn("Tracker with links", desc)
        self.assertNotIn("Unzip", desc)
        self.assertEqual(shots, ["https://raw.githubusercontent.com/Questie/Questie/master/docs/map.png",
                                 "https://example.com/shot.jpg"])

    def test_limit(self):
        self.assertTrue(len(details.plain_text("word " * 2000, limit=100)) <= 101)


class Category(unittest.TestCase):
    def test_from_topics_and_toc(self):
        self.assertEqual(details.category("", ["wow-addon", "nameplates", "ui"]), "ui")
        self.assertEqual(details.category("Quests", []), "quests")
        self.assertEqual(details.category("", ["wow-addon"], "Boss timers for raids"), "combat")
        self.assertIsNone(details.category("", ["wow-addon"], "Hello"))


class Gamepad(unittest.TestCase):
    def test_clear_signals(self):
        self.assertTrue(details.is_gamepad(["wow-addon", "gamepad"]))
        self.assertTrue(details.is_gamepad([], {"notes": "Extra slots for the Steam Deck back paddles"}))
        self.assertTrue(details.is_gamepad([], {}, "Tally", "Bag slots on WoW: Forever's gamepad HUD"))
        self.assertTrue(details.is_gamepad([], {}, "Ubermoc UI", "Controller-first interface"))

    def test_other_controllers_and_plain_addons(self):
        self.assertFalse(details.is_gamepad(["wow-addon"], {}, "RaidLootController", "Fair raid loot without DKP"))
        self.assertFalse(details.is_gamepad([], {}, "MusicianMIDI", "Play live with a MIDI controller"))
        self.assertFalse(details.is_gamepad(["quests"], {"title": "Questie"}, "Questie", "Quest helper"))

    def test_readme_is_only_a_hint(self):
        self.assertTrue(details.mentions_gamepad("Works with ConsolePort."))
        self.assertFalse(details.mentions_gamepad("A raid loot controller."))


if __name__ == "__main__":
    unittest.main()
