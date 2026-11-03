# Working on the Runeloader index

- The Forever build is picked with the same rules as the app's `Sources.GitHub` and `Toc` (github.com/Gulliby-development/runeloader). A change to `indexer/forever.py` needs the same change in the app, with a test on both sides.
- Standard library only, Python 3.10+. Run `python -m unittest` before every commit.
- The index is a hint for search and update checks. The app always resolves the download from GitHub itself before installing.
- Never commit tokens. The workflow reads `INDEX_TOKEN` (optional) or the Actions token.
- Branches: `feature/<name>` or `bugfix/<name>`; changes reach `main` through pull requests. Write code, commits and issues in English.
