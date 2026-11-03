# Runeloader index

A daily list of World of Warcraft addons on GitHub whose latest release has a **WoW Forever** build. The [Runeloader](https://github.com/Gulliby-development/runeloader) app uses it to search GitHub.

**Index:** https://raw.githubusercontent.com/Gulliby-development/runeloader-index/gh-pages/index.json

## How an addon gets in

1. **Found.** The daily job finds repositories by Forever TOC files (`_Forever.toc`, `_Camelot.toc`, `## Interface: 160xx`), by BigWigs packager use, by topics such as `wow-addon`, and from [`seeds.txt`](seeds.txt).
2. **Checked.** Its latest release must have a Forever build: a `release.json` entry with the `forever` or `camelot` flavor, or a zip named for Forever.
3. **Listed.** Name, author, summary, version, download URL, folders, description and screenshots from the README, category, changelog, the last five Forever releases, and the addon's CurseForge, WoWInterface and Wago ids from its TOC.

Missing an addon? Add `owner/repo` to `seeds.txt` in a pull request, or publish your releases with [BigWigs packager](https://github.com/BigWigsMods/packager) and a Forever TOC.

## Run it

```sh
GITHUB_TOKEN=<token> python -m indexer.build_index --out site
python -m unittest
```

Python 3.10+, standard library only. The token needs no scopes: it is used for code search and GraphQL on public repositories.
