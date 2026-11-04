# Runeloader index

A daily list of World of Warcraft addons on GitHub that have a **WoW Forever** build in their latest release. The [Runeloader](https://github.com/Gulliby-development/runeloader) app uses it to search for addons and check for updates.

**Index:** https://raw.githubusercontent.com/Gulliby-development/runeloader-index/gh-pages/index.json (updated daily at 03:17 UTC)

## How an addon gets in

1. **Found.** Its repository has a Forever TOC (`_Forever.toc`, `_Camelot.toc` or `## Interface: 160xx`), is built with BigWigs packager, has a topic such as `wow-addon`, or is listed in [`seeds.txt`](seeds.txt).
2. **Checked.** Its latest GitHub release has a Forever build: a `release.json` entry with the `forever` or `camelot` flavor, or a zip named for Forever.
3. **Listed.** With name, author, summary, version, download link, logo from the TOC `IconTexture`, description and screenshots from the README, category, changelog and the last five Forever releases.

## Addons in several parts

Some repositories release each module on its own tag (`Core-v0.12.0`, `Wheel-v0.10.0`, ...). The index lists the core and every module as separate entries; a module entry has `"module": "Wheel"`. When an addon's TOC needs another addon (`## Dependencies`), the entry lists it in `requires`, so Runeloader installs it too. A release zip without any `.toc` file is not listed.

## Gamepad addons

Addons made for playing with a gamepad or on a handheld (Steam Deck, Android handhelds) get `"tags": ["gamepad"]`, and Runeloader shows them under **Gamepad**. An addon gets the tag when [`gamepad.txt`](gamepad.txt) lists it, or when its topics (`gamepad`, `controller`, `steam-deck`, `handheld`, ...), its TOC title, notes or `X-Category`, its name or its summary say so. A README that only mentions a gamepad is logged for review. `-owner/repo` in `gamepad.txt` keeps the tag off an addon the rule gets wrong.

To retag the published index after you change `gamepad.txt`, without a full run:

```sh
python -m indexer.build_index --out site --tags-only
```

## Discontinued addons

An addon whose author stopped it stays listed with `"discontinued"`, so players who installed it learn that it is over: `archived` when the repository is archived, `notice` when the repo description, the TOC notes or the start of the README say so about the addon itself ("This addon is no longer maintained", "Deprecated", "has moved to ..."), `listed` when [`discontinued.txt`](discontinued.txt) names it (for a notice that is only on another site). `discontinued_note` keeps the author's sentence, and `successor` names the index entry to use instead, from a GitHub link in the notice or the second column of `discontinued.txt`. `-owner/repo` there keeps the mark off an addon the rule reads wrong. `--tags-only` also applies `discontinued.txt`.

The index does not mark an addon because of an old release alone: WoW Forever is young, and a finished addon may not need updates.

## Review of risky addons

Each run also checks the release zip of every listed addon for things that can break its install or load: no addon folder the app can find, no Forever TOC or `## Interface: 160xx`, unreplaced packager tokens such as `@project-version@`, TOC files missing from the zip, a required addon that is neither in the zip nor in the index, two repos that install the same folder. A zip guessed from asset names (no `release.json`) is left out when its TOCs are not for Forever or when it ships the folder of another repo's verified build. A required addon the index lacks is searched for by name and checked from the next run on. The findings are published as `review.json` next to the index. New ones (a new addon, a new release or a new kind of problem) open a GitHub issue labelled `index-review`. A finding that is fine goes into [`review-ignore.txt`](review-ignore.txt) as `owner/repo code`.

To check the published index again without a token (for example after you edit `review-ignore.txt`):

```sh
python -m indexer.build_index --out site --review-only --report review-report.md
```

## Addon authors

Your addon shows up on its own if you publish GitHub releases with [BigWigs packager](https://github.com/BigWigsMods/packager) and ship a `YourAddon_Forever.toc`. Otherwise add `owner/repo` to `seeds.txt` in a pull request. For a gamepad addon, add it to `gamepad.txt` instead.

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

### Full run on your own machine

A personal token and no time limit find more addons than the daily job. Start from the published index, run, and publish the result; the next daily job continues from it.

```sh
git clone https://github.com/Gulliby-development/runeloader-index && cd runeloader-index
git worktree add site origin/gh-pages
GITHUB_TOKEN=$(gh auth token) python -m indexer.build_index --out site --search-minutes 240
cd site && git add index.json state.json icons && git commit -m "Full index run" && git push origin HEAD:gh-pages
```

Python 3.10+, standard library only. The token needs no scopes. See [AGENTS.md](AGENTS.md) for how the index works.
