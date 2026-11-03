# Working on the Runeloader index

These instructions apply to every coding agent (Claude Code, GitHub Copilot, Codex and others) and to human contributors.

## Project

The Runeloader index is a daily list of World of Warcraft addons on GitHub whose latest release has a WoW Forever build. The [Runeloader](https://github.com/Gulliby-development/runeloader) Android app downloads it to search for addons and to check for updates. It is plain Python 3 with the standard library only, run once a day by GitHub Actions.

- Output: `index.json` and `state.json` on the `gh-pages` branch. The app reads `https://raw.githubusercontent.com/Gulliby-development/runeloader-index/gh-pages/index.json`.
- GitHub is the only source. The indexer does not call CurseForge, WoWInterface or Wago.

## How the index works

- `indexer/build_index.py` runs the job in three steps:
  1. **Discover.** Code search for Forever TOCs (`_Forever.toc`, `_Camelot.toc`, `## Interface: 160xx`), code search for BigWigs packager workflows, repository search by topics, and `seeds.txt`. Each code search is split by file size, because one search returns at most 1000 results. TOCs under test, example and template folders are skipped.
  2. **Check.** Repo metadata and the last releases come from GraphQL, 30 repos per query. `indexer/forever.py` picks the Forever build of each release.
  3. **Describe.** Title, notes, author, folder and other-site ids from the Forever TOC; description and screenshots from the README; category; changelog from the release notes (`indexer/details.py`).
- `state.json` keeps the pick from each `release.json` by URL, so an unchanged release is not downloaded again.
- The job refuses to publish when the index shrinks below 70% of the previous one.

## index.json

Top level: `schema` (1), `generated`, `count`, `addons`. Each addon has `repo`, `name`, `author`, `summary`, `stars`, `topics`, `tag`, `version`, `file`, `url`, `size`, `downloads`, `released`, `prerelease`, `verified` and `found_by`. Optional: `folders`, `ids` (`curseforge`, `wowinterface`, `wago`, copied from the TOC), `description`, `screenshots`, `category` (`quests|map|bags|combat|ui|tools`), `changelog`, `releases` (up to 5, newest first) and, later, `icon`.

The app tolerates missing keys. Add a key freely, but never rename or remove one without a new `schema` number and the matching change in the app.

## Task workflow

- Track planned work as GitHub Issues, not in Markdown files or TODO comments. App-side work lives in [runeloader](https://github.com/Gulliby-development/runeloader/issues); indexer work lives here. Record a new idea, a follow-up or a deferred item as an issue.
  - Labels: `idea` (not decided yet), `deferred` (agreed for later), `needs-owner` (only the owner can do or decide it), `verification` (needs a real run or an account).
  - Put `Priority: P0|P1|P2` on the first line of the issue body. P0 blocks current work, P1 is next, P2 is later or an idea.
- Every Feature and Task issue has a `## Maintainability` section. It names the rule a later change must follow, or says `None`.
- Start every task on its own branch from an up-to-date `main`: `feature/<name>` for features, documentation, CI and refactors; `bugfix/<name>` for fixes. `<name>` is a few lowercase words joined by hyphens.
- Commit working states on the task branch. Run `python -m unittest` before each commit.
- A pull request that finishes an issue says `Closes #N`, or `Closes Gulliby-development/runeloader#N` for an app issue. Use `Refs` for partial work.
- `main` takes changes only through pull requests with green CI. Do not push to `main` directly. Once CI is green, the author may merge their own pull request.

## Working conventions

- Use the Python standard library only, Python 3.10+. A new dependency needs an issue and the owner's decision first.
- The Forever build is picked with the same rules as the app. A change to `indexer/forever.py` needs the same change in the app's `Sources.GitHub` or `Toc`, with a test on both sides.
- The index is a hint for search and update checks. The app always resolves the download from GitHub itself before installing.
- Treat every README, TOC and release note as untrusted text: parse it, never run it, cap its length.
- Send the token only to `api.github.com`. Release downloads redirect to signed URLs that must not get an `Authorization` header.
- Stay inside the rate limits: batch GraphQL, keep the pauses between searches, and cache what does not change.
- One bad repository must never stop the run: log it and go on.
- Never commit tokens. The workflow reads the optional `INDEX_TOKEN` secret, else the Actions token.
- Keep comments short and about intent that the code does not show.

## Language

- Write code, identifiers, comments, commit messages, issues, pull requests and these instructions in English.
- `README.md` is in English and stays short: what the index is, how an addon gets in, how to run it.

## Navigation

- `indexer/build_index.py`: discovery, GraphQL batches, the main loop and publishing.
- `indexer/forever.py`: Forever build rules, TOC parsing, other-site ids.
- `indexer/details.py`: README and release notes to plain text, screenshots, category.
- `seeds.txt`: repositories always checked.
- `tests/`: unit tests, including a full `check()` against a fake GitHub.
- `.github/workflows/index.yml`: the daily job. `.github/workflows/test.yml`: tests on pull requests.

## Verification

CI runs the tests on every pull request. Run them before you open one:

```sh
python -m unittest -v
```

For a change to discovery or checking, also run the job with a token and look at the result:

```sh
GITHUB_TOKEN=<token> MAX_CHECKS=200 python -m indexer.build_index --out /tmp/site
python -c "import json; d=json.load(open('/tmp/site/index.json')); print(d['count']); print(d['addons'][0])"
```

Say in the pull request how many addons the run found before and after the change. Report any check you could not run.
