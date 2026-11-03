# Runeloader index

A daily list of World of Warcraft addons on GitHub that have a **WoW Forever** build in their latest release. The [Runeloader](https://github.com/Gulliby-development/runeloader) app uses it to search for addons and check for updates.

**Index:** https://raw.githubusercontent.com/Gulliby-development/runeloader-index/gh-pages/index.json (updated daily at 03:17 UTC)

## How an addon gets in

1. **Found.** Its repository has a Forever TOC (`_Forever.toc`, `_Camelot.toc` or `## Interface: 160xx`), is built with BigWigs packager, has a topic such as `wow-addon`, or is listed in [`seeds.txt`](seeds.txt).
2. **Checked.** Its latest GitHub release has a Forever build: a `release.json` entry with the `forever` or `camelot` flavor, or a zip named for Forever.
3. **Listed.** With name, author, summary, version, download link, logo from the TOC `IconTexture`, description and screenshots from the README, category, changelog and the last five Forever releases.

## Addon authors

Your addon shows up on its own if you publish GitHub releases with [BigWigs packager](https://github.com/BigWigsMods/packager) and ship a `YourAddon_Forever.toc`. Otherwise add `owner/repo` to `seeds.txt` in a pull request.

## Entry example

```json
{"repo": "Questie/Questie", "name": "Questie", "version": "v10.2.3", "verified": true,
 "url": "https://github.com/Questie/Questie/releases/download/v10.2.3/Questie-v10.2.3.zip",
 "folders": ["Questie"], "category": "quests", "stars": 1200}
```

## Run it

```sh
GITHUB_TOKEN=<token> python -m indexer.build_index --out site
python -m unittest
```

Python 3.10+, standard library only. The token needs no scopes. See [AGENTS.md](AGENTS.md) for how the index works.
