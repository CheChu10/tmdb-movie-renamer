## Changelog

This file is written in English to keep diffs simple.

### 1.2.2

- Keep strict collection localization by language+region: if the exact region translation is missing (e.g. `es-ES`), keep TMDB default collection name and do not fall back to another region.
- Improve `--debug` console output for collections: show translation breakdown by language/region, exact region candidate, and the final collection-name decision.
- Add `--debug` trace for collection folder assembly (raw TMDB name, normalized/base name, suffix, final folder, index letter).
- Expand collection suffix normalization for extra designators, including CJK forms like `（系列）`, `シリーズ`, and `시리즈`.
- Add tests for CJK collection suffix stripping and strict region behavior/debug diagnostics in collection localization.

### 1.2.1

- Fix filename sanitization for time-like titles (e.g. `15:17` -> `15.17`).
- Remove unused `rank-torrent-name` dependency and update docs accordingly.
- Expand filename parsing tests with noisy/torrent-style cases and consolidate redundant test cases.
- Internal cleanup: centralize parsing constants (source aliases, resolution tiers, copy buffer).

### 1.2

- `--src` now supports multiple inputs and glob patterns (shell-style, e.g. `/movies/1/12*`) in addition to plain directories.
- Year parsing from filenames is now more robust: detects any `(YYYY)` and validates it against a plausible range (1888..current_year+1).

### 1.1

- More accurate matches when the filename includes an IMDb id (`ttXXXXXXX`) by using TMDB Find API (`/find/{imdb_id}`) instead of ambiguous title search.
- Uses an IMDb id from the filename only for matching (TMDB Find API). If TMDB does not return an IMDb id, the output will not include one.
- Better title selection in the requested language/country using TMDB `translations` and `alternative_titles`.
- `--lang` supports language + country codes like `es-ES`, `pt-PT`, `pt-BR`. If you only pass a language (e.g. `es`), the script tries to choose a default country using Babel.
- Collection folder names can be translated using TMDB Collection Translations API.
- Collection translation results are cached to reduce repeated API calls.
- Collection naming is more consistent: removes suffixes already present in TMDB names (like "Collection" / "la colección" / "(Collection)") and applies a standard suffix.
- Source parsing improved: supports `WEBRip` (and common variants) and normalizes common source tags.
- Safer file operations:
  - Atomic copy/move using hidden temporary files (`.renamer-tmp-*`) and rename-at-end.
  - Automatic cleanup of leftover temp files on exit.
- Script-level lock to prevent running multiple instances at the same time (`.renamer.lock`).
- Overlapping `--src` / `--dest` is supported; risky cases scan a snapshot of files to avoid infinite loops.
- Logging improvements:
  - Rotated `renamer.log` for main actions.
  - Rotated `renamer.detail.log` for diagnostics (API details, title/collection decisions).

### 1.0

- Initial working version.
- Rename movies using TMDB metadata and MediaInfo technical data.
- Jellyfin-friendly folder and filename structure.
