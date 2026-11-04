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
- `state.json` keeps every candidate found so far and the pick from each `release.json` by URL. A day with throttled searches still checks every candidate found before, and an unchanged release is not downloaded again.
- Searches get a time budget (`SEARCH_MINUTES`, 45 by default). A failed or throttled search skips that query; it never stops the run.
- Each run adds to the published index and never starts over. Every addon already listed is checked first and is outside the `MAX_CHECKS` limit, which applies to new candidates only. A listed addon whose GraphQL batch failed, or whose check raised an error, keeps its entry from the last run. It leaves the index only when GitHub answers and its latest releases have no Forever build, or the repository is gone.
- Tags (`tags` in `index.json`) mark addons across categories; a category stays one value. A new tag gets its rule in `indexer/details.py` (clear signals only: topics, TOC, name, summary), a hand-made list file like `gamepad.txt`, a test in `tests/test_details.py`, and the matching key in the app.
- The job refuses to publish when the index shrinks below 70% of the previous one.
- **Review.** `indexer/review.py` checks every release zip for install problems (cached with the zip in `state.json`), writes `review.json` to `gh-pages`, and the job opens an `index-review` issue with the findings that are new for that release. A new check gets a code in `review.CHECKS` and a test in `tests/test_review.py`, and stays cheap: it uses the zip's file list and its TOCs only. `review.doubtful` leaves out guessed zips the zip itself does not confirm; `find_dependencies` adds repos named like a missing required folder as candidates (reason `dependency`). Work an `index-review` issue by fixing the indexer or the app, or by adding `owner/repo code` to `review-ignore.txt` when the finding is fine, then close it.
- A long full run can be made on a local machine and published to `gh-pages` by hand (see `README.md`); the daily job then continues from it.

## index.json

Top level: `schema` (1), `generated`, `count`, `addons`. Each addon has `repo`, `name`, `author`, `summary`, `stars`, `topics`, `tag`, `version`, `file`, `url`, `size`, `downloads`, `released`, `prerelease`, `verified` and `found_by`. Optional: `tags` (a list; today only `gamepad`), `folders`, `ids` (`curseforge`, `wowinterface`, `wago`, copied from the TOC), `description`, `screenshots`, `category` (`quests|map|bags|combat|ui|tools`), `changelog`, `releases` (up to 5, newest first) and `icon` (a path such as `icons/<owner>__<repo>.png`, relative to `index.json`, made from the TOC `IconTexture`), `module` (the module name, on an entry for one module of a module repo) and `requires` (entry keys this addon needs, such as `owner/repo` or `owner/repo#Module`, linked from the TOC `Dependencies`/`RequiredDeps` by folder).

An entry is keyed by `repo`, plus `#<module>` when it has `module`. A release zip with no `.toc` file at all is not listed, since nothing in it can be installed.

`review.json` (not read by the app): `generated`, `checks` (code -> meaning), `addons` (entry key -> `version`, `url`, `problems` as `[code, detail]`, `since`).

The app tolerates missing keys. Add a key freely, but never rename or remove one without a new `schema` number and the matching change in the app.

## Task workflow

- Track planned work as GitHub Issues, not in Markdown files or TODO comments. App-side work lives in [runeloader](https://github.com/Gulliby-development/runeloader/issues); indexer work lives here. Record a new idea, a follow-up or a deferred item as an issue.
  - Labels: `idea` (not decided yet), `deferred` (agreed for later), `needs-owner` (only the owner can do or decide it), `verification` (needs a real run or an account).
  - Put `Priority: P0|P1|P2` on the first line of the issue body. P0 blocks current work, P1 is next, P2 is later or an idea.
- Every Feature and Task issue has a `## Maintainability` section. It names the rule a later change must follow, or says `None`.
- Start every task on its own branch from an up-to-date `main`: `feature/<name>` for features, documentation, CI and refactors; `bugfix/<name>` for fixes. `<name>` is a few lowercase words joined by hyphens.
- Commit working states on the task branch. Run `python -m unittest` before each commit.
- A pull request that finishes an issue says `Closes #N`, or `Closes Gulliby-development/runeloader#N` for an app issue. Use `Refs` for partial work.
- `main` takes changes only through pull requests with green CI. Once CI is green, the author may land their own pull request by fast-forwarding `main` to the tested head, so no merge commit records an account email.

## Working conventions

- Use the Python standard library only, Python 3.10+. A new dependency needs an issue and the owner's decision first.
- A repo that releases each module on its own tag (`Core-v1`, `Wheel-v2`, ...) gets one entry per release group (`forever.module_groups`): the core (plain `v1.2`, `Core-v…` or `<Repo>-v…`) as an ordinary entry, and each module as an entry with `module` set, the newest published release of that group. Tag prefixes in `forever.NOT_MODULES` (release, build, beta, ...) are not modules. The app follows the same rules in `Modules.java`; a change to them changes both, with a test on each side.
- The Forever build is picked with the same rules as the app. A change to `indexer/forever.py` needs the same change in the app's `Sources.GitHub` or `Toc`, with a test on both sides.
- The index is a hint for search and update checks. The app always resolves the download from GitHub itself before installing.
- Treat every README, TOC and release note as untrusted text: parse it, never run it, cap its length.
- Send the token only to `api.github.com`. Release downloads redirect to signed URLs that must not get an `Authorization` header.
- Stay inside the rate limits: batch GraphQL, keep the pauses between searches, split a code search by size only when it has more than 1000 results, and cache what does not change. The Actions token gets long secondary-limit pauses on code search; `INDEX_TOKEN` avoids most of them.
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
- `indexer/remotezip.py`: reads the file list and single files of a release zip with HTTP range requests.
- `indexer/images.py`: BLP2 and TGA decoding, scaling, PNG output (standard library only).
- `indexer/icons.py`: addon folders and the logo from the release zip.
- `indexer/review.py`: install checks on each release zip, `review.json` and the review issue text.
- `review-ignore.txt`: review findings that were looked at and are fine.
- `seeds.txt`: repositories always checked.
- `gamepad.txt`: gamepad and handheld addons. Each one is always checked and tagged `gamepad`; `-owner/repo` never is.
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
