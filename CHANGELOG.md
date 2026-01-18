## Changelog

This file is written in English to keep diffs simple.

### 1.1

- More accurate matches when the filename includes an IMDb id (`ttXXXXXXX`) by using TMDB Find API (`/find/{imdb_id}`) instead of ambiguous title search.
- Keeps the IMDb id from the filename if TMDB does not return one (helps with some unreleased / incomplete entries).
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
