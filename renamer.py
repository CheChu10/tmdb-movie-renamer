#!/usr/bin/env python3
"""
Movie Renamer Script

This script automates the renaming and organization of movie files by fetching
metadata from TheMovieDB (TMDB) and analyzing local media file information.
It is designed to be a powerful and flexible replacement for tools like FileBot.
"""

import argparse
import atexit
import configparser
import errno
import glob
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import unicodedata
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import requests

try:
    import fcntl  # Unix
except ImportError:  # pragma: no cover
    fcntl = None

try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover
    msvcrt = None

# Avoid strict attribute checks in type checkers
if msvcrt is not None:  # pragma: no cover
    MSVCRT_LOCK_NB = getattr(msvcrt, 'LK_NBLCK', None)
    MSVCRT_UNLOCK = getattr(msvcrt, 'LK_UNLCK', None)
else:
    MSVCRT_LOCK_NB = None
    MSVCRT_UNLOCK = None

from colorama import Fore, Style, init
from pymediainfo import MediaInfo


# --- Constants ---
# Using type hints for better clarity on the dictionary structure
COLOR_MAP: Dict[str, str] = {
    'TEST': Fore.YELLOW,
    'MOVE': Fore.GREEN,
    'COPY': Fore.CYAN,
    'SKIP': Fore.MAGENTA,
    'ERROR': Fore.RED,
}

# Retry configuration
# max_retries counts RETRIES (not attempts). total attempts = 1 + max_retries.
MAX_RETRIES = 3
REQUEST_TIMEOUT = 5  # seconds

# Rate limit handling
DEFAULT_RETRY_AFTER_SECONDS = 1

_TMDB_RATE_LIMIT_UNTIL = 0.0

# Log rotation
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

DETAIL_LOG_MAX_BYTES = 10 * 1024 * 1024
DETAIL_LOG_BACKUP_COUNT = 3

# Lock file path
LOCK_FILE = Path(__file__).parent / '.renamer.lock'

# Temp file prefix (for atomic copies)
TEMP_PREFIX = '.renamer-tmp-'

IMDB_ID_RE = re.compile(r'\btt\d{7,8}\b', flags=re.IGNORECASE)

# Year detection from filenames
_MIN_PLAUSIBLE_YEAR = 1888
_MAX_PLAUSIBLE_YEAR_SKEW = 1  # allow next-year releases
_PARENS_YEAR_CAPTURE_RE = re.compile(r'\(\s*(\d{4})\s*\)')
_PARENS_YEAR_FULL_RE = re.compile(r'^\(\s*(\d{4})\s*\)$')

# Collection suffix normalization
# TMDB collection names often include a localized "collection" suffix already
# (sometimes with an article, like "la colección"). We strip it and re-append
# the correct suffix for the chosen language to keep folder names consistent.
_COLLECTION_DESIGNATOR_PATTERNS = [
    r'collection',
    r'colecci[oó]n',
    r'sammlung',
    r'collezione',
    r'cole[cç][aã]o',
]
_COLLECTION_ARTICLE_PATTERN = r"(?:the|a|an|la|el|los|las|le|les|il|lo|i|gli|die|der|das|o|os|as)"
_COLLECTION_DESIGNATOR_RE_PART = r'(?:' + '|'.join(_COLLECTION_DESIGNATOR_PATTERNS) + r')'
_COLLECTION_SUFFIX_STRIP_RE = re.compile(
    r"(?:\s*[-–—:]+\s*|\s+)(?:" + _COLLECTION_ARTICLE_PATTERN + r"\s+)?" + _COLLECTION_DESIGNATOR_RE_PART + r"\s*$",
    flags=re.IGNORECASE,
)
_COLLECTION_SUFFIX_PARENS_STRIP_RE = re.compile(
    r"\s*[\(\[]\s*(?:" + _COLLECTION_ARTICLE_PATTERN + r"\s+)?" + _COLLECTION_DESIGNATOR_RE_PART + r"\s*[\)\]]\s*$",
    flags=re.IGNORECASE,
)

_LOCK_HANDLE: Optional[Any] = None
_TEMP_FILES: Set[Path] = set()

_COLLECTION_NAME_CACHE: Dict[Tuple[int, str, Optional[str]], Optional[str]] = {}


def _parse_retry_after_seconds(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        seconds = int(float(value.strip()))
        return max(0, seconds)
    except Exception:
        return None


def _wait_for_tmdb_rate_limit() -> None:
    global _TMDB_RATE_LIMIT_UNTIL
    now = time.time()
    if _TMDB_RATE_LIMIT_UNTIL > now:
        time.sleep(_TMDB_RATE_LIMIT_UNTIL - now)


def retry_with_backoff(max_retries: int = MAX_RETRIES, initial_delay: float = 1.0) -> Callable:
    """Decorator implementing selective retries with exponential backoff.

    Retries on:
    - timeouts / connection errors
    - HTTP 429 (rate limit): honors Retry-After if present (or DEFAULT_RETRY_AFTER_SECONDS)
    - HTTP 5xx

    Does NOT retry on other 4xx.
    If max_retries == 0: a single attempt is made.
    """

    if max_retries < 0:
        raise ValueError('max_retries must be >= 0')

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            global _TMDB_RATE_LIMIT_UNTIL
            attempts = max_retries + 1
            for attempt_idx in range(attempts):
                try:
                    return func(*args, **kwargs)
                except requests.RequestException as e:
                    is_last = attempt_idx >= attempts - 1

                    retry_after = None
                    should_retry = False

                    if isinstance(e, (requests.Timeout, requests.ConnectionError)):
                        should_retry = True
                    elif isinstance(e, requests.HTTPError) and getattr(e, 'response', None) is not None:
                        status = e.response.status_code
                        if status == 429:
                            should_retry = True
                            retry_after = (
                                _parse_retry_after_seconds(e.response.headers.get('Retry-After'))
                                or DEFAULT_RETRY_AFTER_SECONDS
                            )
                        elif 500 <= status < 600:
                            should_retry = True

                    if not should_retry or is_last:
                        raise

                    exp_delay = initial_delay * (2 ** attempt_idx)
                    delay = exp_delay
                    if retry_after is not None:
                        delay = max(exp_delay, retry_after)
                        _TMDB_RATE_LIMIT_UNTIL = max(_TMDB_RATE_LIMIT_UNTIL, time.time() + retry_after)

                    file_logger.warning(
                        f"TMDB request failed (attempt {attempt_idx + 1}/{attempts}), retrying in {delay}s: {type(e).__name__}"
                    )
                    time.sleep(delay)

            raise RuntimeError('unreachable')

        return wrapper

    return decorator

# --- Logging Setup ---
init(autoreset=True)
console_logger = logging.getLogger('console')
console_logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(message)s'))
console_logger.addHandler(console_handler)

file_logger = logging.getLogger('file')
file_logger.setLevel(logging.INFO)
log_file_path = Path(__file__).parent / 'renamer.log'
if not file_logger.handlers:
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    file_logger.addHandler(file_handler)

# Detailed rotating log (internal decisions / API payload hints)
# Kept separate from renamer.log (which is more "final actions").
detail_logger = logging.getLogger('detail')
detail_logger.setLevel(logging.DEBUG)
detail_log_path = Path(__file__).parent / 'renamer.detail.log'
if not detail_logger.handlers:
    detail_handler = RotatingFileHandler(
        detail_log_path,
        maxBytes=DETAIL_LOG_MAX_BYTES,
        backupCount=DETAIL_LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    detail_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    detail_logger.addHandler(detail_handler)


# --- Helper Functions ---

def strip_collection_designator(name: str) -> str:
    """Remove trailing "Collection"/"Colección"/... from a TMDB collection name."""
    s = (name or '').strip()
    if not s:
        return s

    # Parenthetical version: "Foo (Collection)"
    out = _COLLECTION_SUFFIX_PARENS_STRIP_RE.sub('', s).strip()
    # Hyphen/space version: "Foo - la colección" / "Foo Collection"
    out2 = _COLLECTION_SUFFIX_STRIP_RE.sub('', out).strip().rstrip('-').strip()

    # Avoid returning empty strings.
    return out2 or s


def sanitize_filename(name: str) -> str:
    """Replaces characters illegal in Windows filenames with ' -' and normalizes Unicode."""
    illegal_chars = r'<>:"/\\|?*'
    sanitized_name = name

    normalized = unicodedata.normalize('NFC', sanitized_name)
    sanitized_name = normalized

    for char in illegal_chars:
        sanitized_name = sanitized_name.replace(char, ' -')

    sanitized_name = sanitized_name.strip()

    if not sanitized_name:
        return "Unknown"

    return sanitized_name


def extract_imdb_id_from_filename(filename: str) -> Optional[str]:
    m = IMDB_ID_RE.search(filename)
    if not m:
        return None
    return m.group(0).lower()


def _expand_src_inputs(src_inputs: List[str]) -> List[Path]:
    """Expand --src values.

    Supports either:
    - one or more directories
    - glob patterns (e.g. /movies/1/12*)
    - individual files

    This is intentionally glob (shell-style) rather than regex.
    """

    out: List[Path] = []
    seen: Set[Path] = set()

    for raw in src_inputs:
        s = os.path.expandvars(os.path.expanduser((raw or '').strip()))
        if not s:
            continue

        # If it contains glob chars, expand it ourselves (useful when user quotes
        # the argument and the shell doesn't expand it).
        if any(c in s for c in ['*', '?', '[']):
            matches = [Path(p) for p in glob.glob(s, recursive=True)]
            for p in matches:
                if p not in seen:
                    out.append(p)
                    seen.add(p)
            continue

        p = Path(s)
        if p not in seen:
            out.append(p)
            seen.add(p)

    return out


def get_movie_name_and_year(filename: str, debug: bool = False) -> Tuple[str, Optional[str], Optional[str]]:
    """Extracts a searchable movie title, year and a single fallback title from a filename."""
    stem = Path(filename).stem

    name = re.sub(r'[._]', ' ', stem)

    # Prefer the last plausible year in parentheses: many release names include
    # multiple (...) groups.
    year = None
    year_match = None
    max_year = time.localtime().tm_year + _MAX_PLAUSIBLE_YEAR_SKEW
    for m in _PARENS_YEAR_CAPTURE_RE.finditer(name):
        try:
            y = int(m.group(1))
        except Exception:
            continue
        if _MIN_PLAUSIBLE_YEAR <= y <= max_year:
            year = str(y)
            year_match = m

    if year_match is not None:
        name = name[:year_match.start()].strip()

    fallback = None

    # Identify non-year parentheses (e.g. translated title) for fallback searches.
    all_parens = list(re.finditer(r'\([^)]*\)', name))
    year_parens = []
    for m in all_parens:
        m2 = _PARENS_YEAR_FULL_RE.match(m.group())
        if not m2:
            continue
        try:
            y = int(m2.group(1))
        except Exception:
            continue
        if _MIN_PLAUSIBLE_YEAR <= y <= max_year:
            year_parens.append(m)

    non_year_parens = [m for m in all_parens if m not in year_parens]

    if non_year_parens:
        first_non_year_paren = non_year_parens[0]
        fallback_text = first_non_year_paren.group()[1:-1].strip()

        if (
            fallback_text
            and not fallback_text.isdigit()
            and not any(tag in fallback_text.lower() for tag in ['bluray', 'web-dl', 'bdrip', 'microhd', 'uhdrip', 'bdremux', 'webdl'])
        ):
            fallback = fallback_text

    name = re.sub(r'\[.*?\]|\(.*?\)', '', name).strip()

    if not name:
        file_logger.warning(f"Could not extract a valid title from '{filename}'")
        return "", year, fallback

    if debug:
        console_logger.info(
            Fore.CYAN + f"[DEBUG] Extracted Name: '{name}' | Year: '{year}' | Fallback: '{fallback}'"
        )
    return name, year, fallback


def normalize_lang_input(lang_input: str) -> Tuple[str, Optional[str]]:
    """Normalize --lang to (language, region).

    Accepted inputs:
    - language only: `es`, `it`, `bg`
    - language + region: `es-ES`, `es_MX`, `pt-BR`
    - a few common aliases for language-only inputs (spa/español/eng/...)

    Returns:
    - lang_code: ISO 639-1 (lowercase)
    - region: ISO 3166-1 alpha-2 (uppercase) or None
    """
    raw = (lang_input or '').strip()
    if not raw:
        return 'es', None

    # Handle lang-region forms first.
    m = re.match(r'^([A-Za-z]{2,3})[-_]?([A-Za-z]{2})$', raw)
    if m:
        lang_part = m.group(1).lower()
        region_part = m.group(2).upper()
        lang_code = _alias_to_lang_code(lang_part)
        return lang_code, region_part

    lang_code = _alias_to_lang_code(raw.lower())
    return lang_code, None


def _alias_to_lang_code(lang_part: str) -> str:
    """Maps friendly aliases to ISO 639-1 language codes."""
    lang_map = {
        'es': 'es', 'spa': 'es', 'spanish': 'es', 'español': 'es',
        'en': 'en', 'eng': 'en', 'english': 'en',
        'fr': 'fr', 'fre': 'fr', 'french': 'fr', 'francés': 'fr',
        'de': 'de', 'ger': 'de', 'german': 'de', 'deutsch': 'de',
        'it': 'it', 'ita': 'it', 'italian': 'it', 'italiano': 'it',
        'pt': 'pt', 'por': 'pt', 'portuguese': 'pt', 'portugués': 'pt',
        'ja': 'ja', 'jpn': 'ja', 'japanese': 'ja',
        'zh': 'zh', 'chi': 'zh', 'chinese': 'zh',
        'ko': 'ko', 'kor': 'ko', 'korean': 'ko',
        'ru': 'ru', 'rus': 'ru', 'russian': 'ru',
        'ar': 'ar', 'ara': 'ar', 'arabic': 'ar',
        'hi': 'hi', 'hin': 'hi', 'hindi': 'hi',
        'nl': 'nl', 'dut': 'nl', 'nld': 'nl', 'dutch': 'nl',
    }
    return lang_map.get(lang_part, lang_part if len(lang_part) == 2 else 'es')


def get_collection_suffix(lang_code: str) -> str:
    """Returns the translated ' - Collection' suffix for a given language."""
    suffix_map = {
        'es': ' - Colección',
        'en': ' - Collection',
        'fr': ' - Collection',
        'de': ' - Sammlung',
        'it': ' - Collezione',
    }
    return suffix_map.get(lang_code, ' - Collection')


def get_default_region(lang_code: str) -> Optional[str]:
    """Return a likely default region for a language.

    TMDB separates language (ISO 639-1) and country/region (ISO 3166-1) in some
    endpoints like `alternative_titles` and `translations`.

    If the user does not provide an explicit region (e.g. `--lang es-ES`), we
    infer a sensible default using Babel's likely-subtags data.

    Note: Babel is an optional dependency at runtime; if it's missing we can't
    infer a default region.
    """
    try:
        from babel.core import get_global  # type: ignore

        likely = get_global('likely_subtags')
        tag = likely.get(lang_code)
        if not tag:
            detail_logger.debug(f"No likely_subtags entry for lang_code={lang_code}")
            return None

        # Examples: es_Latn_ES, bg_Cyrl_BG
        parts = tag.split('_')
        if len(parts) >= 3:
            region = parts[2]
            if isinstance(region, str) and len(region) == 2:
                return region.upper()
        return None
    except Exception as e:
        detail_logger.debug(f"Could not infer default region for {lang_code}: {type(e).__name__}")
        return None


def _pick_title_from_translations(
    tmdb_data: Dict[str, Any],
    lang_code: str,
    region: Optional[str],
    strict_region: bool,
) -> Optional[str]:
    translations = tmdb_data.get('translations', {}).get('translations', [])
    if not isinstance(translations, list) or not translations:
        return None

    # If we require region-specific behavior but we don't have a region,
    # do not guess across countries.
    if strict_region and not region:
        return None

    # Prefer exact language+region match (e.g. es-ES).
    if region:
        for tr in translations:
            if tr.get('iso_639_1') == lang_code and tr.get('iso_3166_1') == region:
                title = (tr.get('data') or {}).get('title')
                if title:
                    return title

        if strict_region:
            return None

    # Non-strict mode: any translation for that language.
    for tr in translations:
        if tr.get('iso_639_1') == lang_code:
            title = (tr.get('data') or {}).get('title')
            if title:
                return title

    return None


def _pick_title_from_alternative_titles(
    tmdb_data: Dict[str, Any],
    region: Optional[str],
    strict_region: bool,
) -> Optional[str]:
    alt_titles = tmdb_data.get('alternative_titles', {}).get('titles', [])
    if not isinstance(alt_titles, list) or not alt_titles:
        return None

    # If we require region-specific behavior but we don't have a region,
    # do not guess across countries.
    if strict_region and not region:
        return None

    if region:
        for alt in alt_titles:
            if alt.get('iso_3166_1') == region:
                title = (alt.get('title') or '').strip()
                if title:
                    return title

        return None

    # Non-strict mode only.
    for alt in alt_titles:
        title = (alt.get('title') or '').strip()
        if title:
            return title

    return None


def _pick_collection_name_from_translations(
    translations: Any,
    lang_code: str,
    region: Optional[str],
    strict_region: bool,
) -> Optional[str]:
    if not isinstance(translations, list) or not translations:
        return None

    if strict_region and not region:
        return None

    if region:
        for tr in translations:
            if tr.get('iso_639_1') != lang_code or tr.get('iso_3166_1') != region:
                continue
            data = tr.get('data') or {}
            name = (data.get('name') or data.get('title') or tr.get('name') or '').strip()
            if name:
                return name

        if strict_region:
            return None

    for tr in translations:
        if tr.get('iso_639_1') != lang_code:
            continue
        data = tr.get('data') or {}
        name = (data.get('name') or data.get('title') or tr.get('name') or '').strip()
        if name:
            return name

    return None


def apply_preferred_collection_name(
    tmdb_data: Dict[str, Any],
    headers: Dict[str, str],
    lang_code: str,
    region: Optional[str],
    debug: bool = False,
) -> None:
    collection = tmdb_data.get('belongs_to_collection')
    if not isinstance(collection, dict):
        return

    raw_id = collection.get('id')
    if raw_id is None:
        return

    try:
        collection_id = int(raw_id)
    except Exception:
        return

    explicit_region = region is not None
    if region is None:
        region = get_default_region(lang_code)

    strict_region = True

    cache_key = (collection_id, lang_code, region)
    if cache_key in _COLLECTION_NAME_CACHE:
        chosen = _COLLECTION_NAME_CACHE[cache_key]
        if chosen:
            collection['name'] = chosen
        return

    url = f"https://api.themoviedb.org/3/collection/{collection_id}/translations"
    params: Dict[str, Any] = {}

    if debug:
        region_source = 'explicit' if explicit_region else ('inferred' if region else 'none')
        console_logger.info(
            Fore.CYAN
            + f"[DEBUG] Collection language context: lang={lang_code}, region={region or 'N/A'} (source={region_source})."
        )
        console_logger.info(Fore.CYAN + "[DEBUG] Calling TMDB Collection Translations API...")
        console_logger.info(Fore.CYAN + f"        URL: {url}")

    try:
        resp = _make_tmdb_request(url, params, headers)
    except requests.RequestException as e:
        detail_logger.debug(f"Collection translations request failed: {type(e).__name__}")
        _COLLECTION_NAME_CACHE[cache_key] = None
        return

    translations = resp.get('translations')
    if not isinstance(translations, list):
        translations = (resp.get('translations', {}) or {}).get('translations')

    chosen = _pick_collection_name_from_translations(translations, lang_code, region, strict_region=strict_region)
    chosen = (chosen or '').strip() or None

    _COLLECTION_NAME_CACHE[cache_key] = chosen

    if chosen:
        old_name = (collection.get('name') or '').strip()
        collection['name'] = chosen
        if debug:
            console_logger.info(
                Fore.CYAN
                + f"[DEBUG] Using '{chosen}' as collection name (was: '{old_name or 'N/A'}')."
            )

    # Log a summary to the detailed log for diagnosability.
    if isinstance(translations, list):
        detail_logger.debug(
            f"TMDB collection translations summary: collection_id={collection_id}, lang={lang_code}, region={region or 'N/A'}, count_total={len(translations)}"
        )
        for tr in translations:
            try:
                iso639 = tr.get('iso_639_1')
                iso3166 = tr.get('iso_3166_1')
                data = tr.get('data') or {}
                name = (data.get('name') or data.get('title') or tr.get('name') or '').strip()
                if name:
                    detail_logger.debug(f"TMDB collection translation entry: {iso639}-{iso3166} name={name}")
            except Exception:
                continue


def apply_preferred_title(
    tmdb_data: Dict[str, Any],
    lang_code: str,
    filename_title: str,
    filename_alt_title: Optional[str],
    region: Optional[str] = None,
    debug: bool = False,
) -> None:
    """Mutate tmdb_data['title'] to a best-effort localized/display title.

    TMDB can return an untranslated `title` even when requesting `language=es`
    (e.g. overview is translated but the title is not, or the localized name is
    stored as a country-specific alternative title).

    Strategy (non-English languages only):
    - If TMDB already returned a localized title (title != original_title), keep it.
    - Else try `translations`.
    - Else try `alternative_titles` for a default country (es->ES, en->US, ...).
    - Else fall back to the filename's "other" title (the one that differs from TMDB).
    """
    if lang_code == 'en':
        return

    tmdb_title = (tmdb_data.get('title') or '').strip()
    original_title = (tmdb_data.get('original_title') or '').strip()

    # If TMDB already gave us a localized title, we keep it as the baseline.
    # But we may still override it if we have an explicit/inferred region and
    # TMDB provides a region-specific title (e.g. pt-PT vs pt-BR).

    explicit_region = region is not None
    inferred_region = None

    if region is None:
        inferred_region = get_default_region(lang_code)
        region = inferred_region

    # We always operate in a "region aware" mode for title selection.
    # If we don't have a region (and can't infer it), we avoid falling back to
    # other countries (e.g. es-MX) because it can be wrong.
    strict_region = True

    if debug:
        region_source = 'explicit' if explicit_region else ('inferred' if region else 'none')
        console_logger.info(Fore.CYAN + f"[DEBUG] Language context: lang={lang_code}, region={region or 'N/A'} (source={region_source}).")

    chosen = _pick_title_from_translations(tmdb_data, lang_code, region, strict_region=strict_region)
    chosen_source = 'translations'

    if not chosen:
        chosen = _pick_title_from_alternative_titles(tmdb_data, region, strict_region=strict_region)
        chosen_source = 'alternative_titles'

    # Log alternative titles too (useful to explain why a title was chosen).
    alt_payload = tmdb_data.get('alternative_titles', {})
    if isinstance(alt_payload, dict):
        alt_titles = alt_payload.get('titles', [])
    else:
        alt_titles = []

    if isinstance(alt_titles, list):
        detail_logger.debug(
            f"TMDB alternative_titles summary: lang={lang_code}, region={region or 'N/A'}, count_total={len(alt_titles)}"
        )
        for alt in alt_titles:
            try:
                iso3166 = alt.get('iso_3166_1')
                title = (alt.get('title') or '').strip()
                if title:
                    detail_logger.debug(f"TMDB alternative title entry: {iso3166} title={title}")
            except Exception:
                continue

        if debug:
            if region:
                region_titles = []
                for alt in alt_titles:
                    if alt.get('iso_3166_1') != region:
                        continue
                    t = (alt.get('title') or '').strip()
                    if t:
                        region_titles.append(t)

                if region_titles:
                    console_logger.info(
                        Fore.CYAN + f"[DEBUG] TMDB alternative_titles for region={region}: " + "; ".join(region_titles[:10])
                    )
                    if len(region_titles) > 10:
                        console_logger.info(
                            Fore.CYAN + f"[DEBUG] TMDB alternative_titles for region={region} truncated: total={len(region_titles)}"
                        )
                else:
                    console_logger.info(
                        Fore.CYAN + f"[DEBUG] TMDB alternative_titles: no entries for iso_3166_1='{region}' (total={len(alt_titles)})."
                    )
            else:
                console_logger.info(
                    Fore.CYAN + f"[DEBUG] TMDB alternative_titles present but no region selected (total={len(alt_titles)})."
                )

    # IMPORTANT: if TMDB doesn't provide a title for the requested region,
    # do NOT invent one from the filename. In that case we keep the original
    # title, matching the previous behavior (and avoiding wrong titles).

    chosen = (chosen or '').strip()
    if chosen and chosen != tmdb_title:
        tmdb_data['title'] = chosen
        if debug:
            console_logger.info(Fore.CYAN + f"[DEBUG] Using '{chosen}' as display title (source: {chosen_source}).")
    elif debug:
        console_logger.info(Fore.CYAN + "[DEBUG] No localized title applied; keeping TMDB title as-is.")

    # Persist a bit more detail to the detailed log.
    translations_payload = tmdb_data.get('translations', {})
    if isinstance(translations_payload, dict):
        translations = translations_payload.get('translations', [])
    else:
        translations = []

    if isinstance(translations, list):
        detail_logger.debug(
            f"TMDB translations summary: lang={lang_code}, region={region or 'N/A'}, count_total={len(translations)}"
        )

        for tr in translations:
            try:
                iso639 = tr.get('iso_639_1')
                iso3166 = tr.get('iso_3166_1')
                title = ((tr.get('data') or {}).get('title') or '').strip()
                if title:
                    detail_logger.debug(f"TMDB translation entry: {iso639}-{iso3166} title={title}")
            except Exception:
                continue

    if debug:
        if isinstance(translations, list):
            entries: List[str] = []
            exact_region_title = None

            for tr in translations:
                if tr.get('iso_639_1') != lang_code:
                    continue

                title = ((tr.get('data') or {}).get('title') or '').strip()
                if not title:
                    continue

                iso3166 = tr.get('iso_3166_1')
                if region and iso3166 == region and exact_region_title is None:
                    exact_region_title = title

                entries.append(f"{iso3166}:{title}" if iso3166 else title)

            if entries:
                suffix = f" for {lang_code}" + (f" (requested region={region})" if region else "")
                console_logger.info(Fore.CYAN + f"[DEBUG] TMDB translations found{suffix}: " + "; ".join(entries[:10]))
                if len(entries) > 10:
                    console_logger.info(Fore.CYAN + f"[DEBUG] TMDB translations truncated: total={len(entries)}")

                if region:
                    if exact_region_title:
                        console_logger.info(Fore.CYAN + f"[DEBUG] TMDB {lang_code}-{region} title candidate: '{exact_region_title}'.")
                    else:
                        console_logger.info(Fore.CYAN + f"[DEBUG] TMDB {lang_code}-{region} title candidate: (none).")
            else:
                console_logger.info(Fore.CYAN + f"[DEBUG] TMDB translations: no entries for iso_639_1='{lang_code}'.")
        else:
            console_logger.info(Fore.CYAN + "[DEBUG] TMDB translations payload missing or invalid.")


@retry_with_backoff(max_retries=MAX_RETRIES, initial_delay=1.0)
def _make_tmdb_request(url: str, params: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """Make a single TMDB API request with timeout and retry logic."""
    _wait_for_tmdb_rate_limit()
    resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_tmdb_info(api_key: str, filename: str, lang_code: str, region: Optional[str], debug: bool) -> Optional[Dict[str, Any]]:
    """Fetches movie data from TheMovieDB API using a Bearer Token."""
    filename_title, year, filename_alt_title = get_movie_name_and_year(filename, debug=debug)
    if not filename_title:
        file_logger.warning(f"Could not extract a valid title from '{filename}'")
        return None

    filename_imdb_id = extract_imdb_id_from_filename(filename)
    if debug:
        console_logger.info(Fore.CYAN + f"[DEBUG] Extracted IMDB ID: '{filename_imdb_id}'")

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    search_url = "https://api.themoviedb.org/3/search/movie"

    def _search(candidate: str, use_year: bool) -> List[Dict[str, Any]]:
        """Helper: busca un único título en TMDB, opcionalmente filtrando por año."""
        params = {'query': candidate, 'language': lang_code}
        if use_year and year:
            params['primary_release_year'] = year

        if debug:
            mode = "with year" if use_year and year else "without year"
            console_logger.info(Fore.CYAN + f"[DEBUG] Calling TMDB Search API for '{candidate}' ({mode})...")
            console_logger.info(Fore.CYAN + f"        URL: {search_url}")
            console_logger.info(Fore.CYAN + f"        Params: {params}")
            console_logger.info(Fore.CYAN + f"        Headers: {{'Authorization': 'Bearer ***REDACTED***'}}")

        resp_data = _make_tmdb_request(search_url, params, headers)
        results = resp_data.get('results', [])

        if debug:
            console_logger.info(Fore.CYAN + "[DEBUG] API Search Response (up to 5 results):")
            console_logger.info(Fore.CYAN + json.dumps(results[:5], indent=2, ensure_ascii=False))

        return results

    try:
        results: List[Dict[str, Any]] = []
        searched_candidate = filename_title

        movie_id: Optional[int] = None

        # If the filename already contains an IMDb ID, prefer TMDB's /find endpoint
        # to avoid ambiguous search results.
        if filename_imdb_id:
            find_url = f"https://api.themoviedb.org/3/find/{filename_imdb_id}"
            find_params = {
                'external_source': 'imdb_id',
                'language': lang_code,
            }

            if debug:
                console_logger.info(Fore.CYAN + f"[DEBUG] Calling TMDB Find API for '{filename_imdb_id}'...")
                console_logger.info(Fore.CYAN + f"        URL: {find_url}")
                console_logger.info(Fore.CYAN + f"        Params: {find_params}")

            try:
                find_data = _make_tmdb_request(find_url, find_params, headers)
                movie_results = find_data.get('movie_results', [])

                detail_logger.debug(
                    f"TMDB find summary: imdb_id={filename_imdb_id}, movie_results={len(movie_results)}"
                )

                if debug:
                    console_logger.info(Fore.CYAN + "[DEBUG] TMDB Find Response (movie_results up to 5):")
                    console_logger.info(
                        Fore.CYAN + json.dumps(movie_results[:5], indent=2, ensure_ascii=False)
                    )

                if isinstance(movie_results, list) and movie_results:
                    movie_id = movie_results[0].get('id')
                    searched_candidate = f"imdb:{filename_imdb_id}"
            except requests.RequestException as e:
                detail_logger.debug(f"TMDB find request failed: {type(e).__name__}")

        if movie_id is None:
            for candidate in [filename_title] + ([filename_alt_title] if filename_alt_title else []):
                year_options = (True, False) if year else (False,)

                for use_year in year_options:
                    results = _search(candidate, use_year)
                    if results:
                        searched_candidate = candidate
                        break
                if results:
                    break

                # No fixed sleep here: rate limiting is handled via HTTP 429 Retry-After.

            if not results:
                file_logger.warning(f"No results found on TMDB for '{filename_title}' ({year or 'N/A'})")
                return None

            movie_id = results[0]['id']

        movie_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        movie_params = {
            'append_to_response': 'external_ids,collection,translations,alternative_titles',
            'language': lang_code,
        }

        if debug:
            console_logger.info(Fore.CYAN + "[DEBUG] Calling TMDB Movie Details API...")
            console_logger.info(Fore.CYAN + f"        URL: {movie_url}")
            console_logger.info(Fore.CYAN + f"        Params: {movie_params}")

        resp_data = _make_tmdb_request(movie_url, movie_params, headers)

        # IMPORTANT: the final output should always reflect TMDB data.
        # We may use an IMDb id from the filename to find the correct TMDB entry,
        # but we do not copy that IMDb id into the final metadata if TMDB doesn't
        # provide one.
        if debug and filename_imdb_id:
            tmdb_imdb = (resp_data.get('external_ids') or {}).get('imdb_id')
            if not tmdb_imdb:
                console_logger.info(
                    Fore.CYAN
                    + f"[DEBUG] TMDB did not provide an IMDb id for this title (filename had {filename_imdb_id})."
                )

        apply_preferred_title(resp_data, lang_code, filename_title, filename_alt_title, region=region, debug=debug)
        apply_preferred_collection_name(resp_data, headers, lang_code, region, debug=debug)

        if debug and searched_candidate != filename_title:
            console_logger.info(Fore.CYAN + f"[DEBUG] Matched on search candidate: '{searched_candidate}'.")

        return resp_data

    except requests.RequestException as e:
        error_msg = str(e)
        error_msg = re.sub(r'Authorization[^&"\']*', 'Authorization=***REDACTED***', error_msg)
        file_logger.error(f"Network error contacting TMDB: {error_msg}")
        if debug:
            console_logger.info(Fore.CYAN + f"[DEBUG] Network error contacting TMDB: {error_msg}")
        return None


def get_resolution_class(width: Optional[int], height: Optional[int]) -> str:
    """Classifies video resolution using both width and height with tolerance.
    Fixes cases like 1792x1080 (should be 1080p) and 3840x1600 (should be 2160p).
    """
    if not width or not height:
        return "N/A"

    w, h = int(width), int(height)

    # Tolerancia (~5%) para encodes no estándar, anamórficos o con recortes.
    # Con esto: h≈1080 => 1080p aunque w<1920 (p.ej., 1792x1080).
    tiers = [
        (3840, 2160, "2160p"),
        (2560, 1440, "1440p"),
        (1920, 1080, "1080p"),
        (1280,  720,  "720p"),
    ]

    for w_th, h_th, label in tiers:
        if w >= int(w_th * 0.95) or h >= int(h_th * 0.95):
            return label

    return f"{h}p"


def get_media_info(filepath: Path, debug: bool = False) -> Optional[Dict[str, Any]]:
    """Extracts technical media information from a file."""
    try:
        media_info = MediaInfo.parse(filepath)

        if not media_info.video_tracks:
            file_logger.warning(f"No video track found in '{filepath}'")
            return None

        if not media_info.audio_tracks:
            file_logger.warning(f"No audio track found in '{filepath}'")
            return None

        if not media_info.general_tracks:
            file_logger.warning(f"No general track found in '{filepath}'")
            return None

        video_track = media_info.video_tracks[0]
        audio_track = media_info.audio_tracks[0]
        general_track = media_info.general_tracks[0]

        if debug:
            console_logger.info(Fore.CYAN + f"[DEBUG] Raw video track info:")
            console_logger.info(Fore.CYAN + f"        - Format: {video_track.format}")
            console_logger.info(Fore.CYAN + f"        - Width: {video_track.width}")
            console_logger.info(Fore.CYAN + f"        - Height: {video_track.height}")
            console_logger.info(Fore.CYAN + f"        - Writing library: {getattr(video_track, 'writing_library', 'N/A')}")

        writing_library = video_track.writing_library
        if writing_library and 'x264' in writing_library.lower():
            vc = 'x264'
        elif writing_library and 'x265' in writing_library.lower():
            vc = 'x265'
        else:
            vc = (video_track.format or "N/A").upper()

        return {
            'vf': get_resolution_class(video_track.width, video_track.height),
            'vc': vc,
            'ac': (audio_track.format or "N/A").replace('-', ''),
            'hdr': "HDR" if video_track.bit_depth and int(video_track.bit_depth) >= 10 else None,
            'width': video_track.width,
            'height': video_track.height,
            'bitrate': general_track.overall_bit_rate,
        }
    except Exception as e:
        file_logger.warning(f"Could not read media info from '{filepath}': {e}")
        return None


def parse_source_from_filename(filename: str) -> Optional[str]:
    """Extracts the source (e.g., WEB-DL, BluRay) from the original filename."""
    filename_lower = filename.lower()
    # Normalize common source variants found in release names.
    # Order matters: longer/more specific patterns first.
    source_aliases = [
        ('uhd bdremux', 'UHD BDRemux'),
        ('bdremux', 'BDRemux'),
        ('bdrip', 'BDRip'),
        ('bluray', 'BluRay'),
        ('blu-ray', 'BluRay'),
        ('microhd', 'MicroHD'),
        ('webrip', 'WEBRip'),
        ('web-rip', 'WEBRip'),
        ('web rip', 'WEBRip'),
        ('webdl', 'WEB-DL'),
        ('web-dl', 'WEB-DL'),
    ]

    for needle, normalized in source_aliases:
        if needle in filename_lower:
            return normalized
    return None


def deduce_source_from_mediainfo(media_info: Dict[str, Any], debug: bool = False) -> Optional[str]:
    """Attempts to deduce the source based on resolution and bitrate."""
    height = media_info.get('height')
    bitrate = media_info.get('bitrate')
    if not height or not bitrate:
        return None

    bitrate_mbps = bitrate / 1_000_000
    if debug:
        console_logger.info(Fore.CYAN + f"[DEBUG] Deducing source: Height={height}p, Bitrate={bitrate_mbps:.2f} Mbps")

    source = "WEB-DL"  # Default fallback
    if height >= 2100 and bitrate_mbps > 40:
        source = 'UHD BDRemux'
    elif height >= 2100:
        source = 'UHDRip'
    elif height >= 1080 and bitrate_mbps > 25:
        source = 'BDRemux'
    elif height >= 1080 and bitrate_mbps > 10:
        source = 'BDRip'

    if debug:
        console_logger.info(Fore.CYAN + f"[DEBUG] Deduced source: {source}")
    return source


def build_destination_path(tmdb_data: Dict[str, Any], media_info: Dict[str, Any], source: Optional[str],
                           output_dir: Path, lang_code: str, original_suffix: str) -> Path:
    """Constructs the full destination path for a movie file."""
    sanitized_title = sanitize_filename(tmdb_data['title'])
    year = tmdb_data.get('release_date', 'N/A')[:4]
    imdb_id = tmdb_data.get('external_ids', {}).get('imdb_id', '')

    imdb_id_str = f"[{imdb_id}]" if imdb_id else ""
    movie_title_year = f"{sanitized_title} ({year})"
    first_letter = sanitized_title[0].upper()
    movie_folder_parent = output_dir / first_letter

    collection_info = tmdb_data.get('belongs_to_collection')
    if collection_info:
        collection_name_from_tmdb = sanitize_filename(collection_info['name'])
        collection_name_base = strip_collection_designator(collection_name_from_tmdb)
        correct_suffix = get_collection_suffix(lang_code)
        collection_name = f"{collection_name_base}{correct_suffix}"
        first_letter = collection_name_base[0].upper()
        movie_folder_parent = output_dir / first_letter / collection_name

    movie_folder = movie_folder_parent / f"{movie_title_year} {imdb_id_str}".strip()

    source_str = f" ({source})" if source else ""
    tags: List[str] = [f"{media_info.get('vf', 'N/A')}{source_str}"]
    if media_info.get('hdr'):
        tags.append(media_info['hdr'])
    if media_info.get('vc'):
        tags.append(media_info['vc'])
    if media_info.get('ac'):
        tags.append(media_info['ac'])

    tags_str = ", ".join(filter(None, tags))
    new_filename = f"{movie_title_year} {imdb_id_str} - [{tags_str}]{original_suffix}".strip()

    return movie_folder / new_filename


def classify_path_overlap(src: Path, dest: Path) -> str:
    """Classify overlap relationship between two directories.

    Returns one of:
    - 'none': no overlap
    - 'same': src == dest
    - 'src_within_dest': src is inside dest (dest is ancestor of src)
    - 'dest_within_src': dest is inside src (src is ancestor of dest)

    This is used to decide whether we need loop-avoidance measures.
    """
    try:
        src_resolved = src.resolve()
        dest_resolved = dest.resolve()

        if src_resolved == dest_resolved:
            return 'same'

        # dest is ancestor of src
        if dest_resolved in src_resolved.parents:
            return 'src_within_dest'

        # src is ancestor of dest
        if src_resolved in dest_resolved.parents:
            return 'dest_within_src'

        return 'none'
    except Exception:
        return 'none'


# Note: keep only classify_path_overlap; boolean wrapper removed.


def _register_temp_file(path: Path) -> None:
    _TEMP_FILES.add(path)


def _cleanup_temp_files() -> None:
    for p in list(_TEMP_FILES):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
        finally:
            _TEMP_FILES.discard(p)


def _install_cleanup_handlers() -> None:
    atexit.register(_cleanup_temp_files)

    def _handler(signum: int, frame: Any) -> None:  # pragma: no cover
        _cleanup_temp_files()
        raise SystemExit(128 + signum)

    for sig in (getattr(signal, 'SIGINT', None), getattr(signal, 'SIGTERM', None)):
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass


def _atomic_copy(src_path: Path, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_name = f"{TEMP_PREFIX}{dest_path.name}.{os.getpid()}"
    tmp_path = dest_path.with_name(tmp_name)

    _register_temp_file(tmp_path)
    try:
        with open(src_path, 'rb') as fsrc:
            with open(tmp_path, 'wb') as fdst:
                shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
                fdst.flush()
                os.fsync(fdst.fileno())

        shutil.copystat(src_path, tmp_path, follow_symlinks=True)
        os.replace(tmp_path, dest_path)
    finally:
        # If replace succeeded, tmp_path no longer exists.
        # If we failed before replace, delete the tmp.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        _TEMP_FILES.discard(tmp_path)


def _atomic_move(src_path: Path, dest_path: Path) -> None:
    # Best-effort: try rename first (atomic on same filesystem), fallback to copy+delete.
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(src_path, dest_path)
        return
    except OSError as e:
        # EXDEV: cross-device link (different filesystem)
        if e.errno != errno.EXDEV:
            raise

    _atomic_copy(src_path, dest_path)
    src_path.unlink()


def perform_file_action(action: str, src_path: Path, dest_path: Path) -> None:
    """Performs the specified file action (move, copy, test, skip) and logs it."""
    action_upper = action.upper()
    action_prefix = f"[{action_upper}]"
    action_color = COLOR_MAP.get(action_upper, Fore.WHITE)
    colored_prefix = f"{action_color}{action_prefix}{Style.RESET_ALL}"

    from_str = f"FROM: {src_path}"
    to_str = f"TO:   {dest_path}"

    console_logger.info(f"{colored_prefix} {from_str}")
    console_logger.info(f"{colored_prefix} {to_str}")
    file_logger.info(f"{action_prefix} {from_str}")
    file_logger.info(f"{action_prefix} {to_str}")

    # Guard clause: if we are testing or skipping, do nothing further.
    if action in {'test', 'skip'}:
        return

    try:
        if action == 'move':
            _atomic_move(src_path, dest_path)
        elif action == 'copy':
            _atomic_copy(src_path, dest_path)
    except Exception as e:
        error_prefix = f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL}"
        console_logger.error(f"{error_prefix} Could not {action} file: {e}")
        file_logger.error(f"[ERROR] Could not {action} file '{src_path}': {e}")


def process_file(filepath: Path, config: Dict[str, Any]) -> None:
    """Main processing logic for a single movie file."""
    console_logger.info(f"--- Processing file: {filepath.name} ---")
    file_logger.info(f"--- Processing file: {filepath.name} ---")

    # Guard clauses for early exit
    tmdb_data = get_tmdb_info(config['api_key'], filepath.name, config['lang_code'], config.get('region'), config['debug'])
    if not tmdb_data:
        return

    media_info = get_media_info(filepath, debug=config['debug'])
    if not media_info:
        return

    source = parse_source_from_filename(filepath.name) or deduce_source_from_mediainfo(media_info, config['debug'])

    destination_path = build_destination_path(
        tmdb_data, media_info, source, config['output_dir'], config['lang_code'], filepath.suffix
    )

    action = config['action']
    if destination_path.exists():
        action = 'skip'

    perform_file_action(action, filepath, destination_path)


def setup_configuration() -> Optional[Dict[str, Any]]:
    """Parses command-line arguments and loads configuration."""
    parser = argparse.ArgumentParser(
        description="Renames and organizes movie files, similar to FileBot.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '--src',
        required=True,
        nargs='+',
        type=str,
        help=(
            "Source directory/file or glob pattern(s) containing movies to process. "
            "Examples: /path/movies or /path/movies/12*"
        ),
    )
    parser.add_argument('--dest', required=True, type=Path, help="Destination directory for organized movies.")
    parser.add_argument(
        '--lang',
        default='es',
        help=(
            "Language for metadata. Examples: es, en, it, bg; or locale-specific: es-ES, es-MX, pt-BR. "
            "Default: es."
        ),
    )
    parser.add_argument('--action', default='test', choices=['test', 'move', 'copy'], help="Action to perform. Default: test.")
    parser.add_argument('--dry-run', action='store_true', help="Safety flag. Overrides any action to 'test'.")
    parser.add_argument('--debug', action='store_true', help="Enables detailed internal debugging output.")
    args = parser.parse_args()

    config_parser = configparser.ConfigParser()
    config_path = Path(__file__).parent / 'config.ini'
    if not config_path.exists():
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Configuration file '{config_path}' not found.")
        return None
    config_parser.read(config_path)

    api_key = config_parser.get('TMDB', 'api_key', fallback=None)
    if not api_key or api_key == 'TU_API_KEY_AQUI':
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Please set your TMDB API key in 'config.ini'.")
        return None

    expanded_src = _expand_src_inputs(args.src)
    if not expanded_src:
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Source did not match any paths.")
        return None

    if not args.dest.is_dir():
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Destination is not a valid directory.")
        return None

    # Keep only valid sources (directories or individual files).
    input_paths: List[Path] = [p for p in expanded_src if p.exists() and (p.is_dir() or p.is_file())]
    if not input_paths:
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Source or destination is not a valid directory.")
        return None

    scan_snapshot = False
    overlap_kinds: Set[str] = set()

    for p in input_paths:
        if not p.is_dir():
            continue
        kind = classify_path_overlap(p, args.dest)
        overlap_kinds.add(kind)
        if kind in {'same', 'dest_within_src'}:
            scan_snapshot = True

    # We used to hard-fail on any overlap. In practice, some overlap is safe
    # (e.g. scanning a movie folder inside the library root) and "same" paths
    # are useful for dry-runs.
    if scan_snapshot:
        # Potentially risky when moving/copying because the destination is inside
        # the scan tree; avoid infinite loops by taking a snapshot of files.
        console_logger.info(
            f"{COLOR_MAP['TEST']}[WARN]{Style.RESET_ALL} Source and destination directories overlap ({', '.join(sorted(overlap_kinds))}). "
            "Using snapshot scan mode to avoid infinite loops."
        )
    elif 'src_within_dest' in overlap_kinds:
        # Safe: we scan a subset of the destination tree.
        if args.debug:
            console_logger.info(
                f"{COLOR_MAP['TEST']}[DEBUG]{Style.RESET_ALL} Source is within destination; scan is restricted to src subtree."
            )

    lang_code, region = normalize_lang_input(args.lang)

    return {
        'api_key': api_key,
        'input_paths': input_paths,
        'output_dir': args.dest,
        'lang_code': lang_code,
        'region': region,
        'action': 'test' if args.dry_run else args.action,
        'debug': args.debug,
        'scan_snapshot': scan_snapshot,
        'overlap_kinds': overlap_kinds,
    }


def acquire_lock() -> bool:
    """Best-effort lock.

    - Linux/Unix: uses fcntl.flock if available.
    - Windows: uses msvcrt.locking if available.
    - Otherwise: no lock (returns True).

    Keeps the underlying handle open in _LOCK_HANDLE.
    """

    global _LOCK_HANDLE

    # If we cannot lock (module missing), run without locking.
    if fcntl is None and msvcrt is None:
        return True

    handle = None
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        handle = open(LOCK_FILE, 'a+')

        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None and MSVCRT_LOCK_NB is not None:
            handle.seek(0)
            getattr(msvcrt, 'locking')(handle.fileno(), MSVCRT_LOCK_NB, 1)
        else:
            # Locking not available on this platform / interpreter.
            handle.close()
            return True

        _LOCK_HANDLE = handle
        return True

    except Exception:
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass
        return False


def release_lock() -> None:
    """Release lock and remove lock file (best-effort)."""

    global _LOCK_HANDLE

    try:
        if _LOCK_HANDLE is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_UN)
                elif msvcrt is not None and MSVCRT_UNLOCK is not None:
                    _LOCK_HANDLE.seek(0)
                    getattr(msvcrt, 'locking')(_LOCK_HANDLE.fileno(), MSVCRT_UNLOCK, 1)
            finally:
                _LOCK_HANDLE.close()
    except Exception:
        pass
    finally:
        _LOCK_HANDLE = None

    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass


def main() -> None:
    """Main entry point for the script."""
    _install_cleanup_handlers()

    if not acquire_lock():
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Another instance of the script is already running.")
        file_logger.error("Another instance of the script is already running.")
        sys.exit(1)

    try:
        config = setup_configuration()
        if not config:
            sys.exit(1)

        action = config['action']
        src_display = ", ".join(str(p) for p in config.get('input_paths', []))
        file_logger.info(f"Starting recursive scan in: {src_display} with action: {action.upper()}")
        if action == 'test':
            console_logger.info(COLOR_MAP['TEST'] + "Simulation mode active. No changes will be made.")

        def _iter_input_files() -> List[Path]:
            out: List[Path] = []
            seen: Set[Path] = set()
            for root in config.get('input_paths', []):
                if root.is_file():
                    if root.suffix.lower() in ['.mkv', '.mp4', '.avi'] and root not in seen:
                        out.append(root)
                        seen.add(root)
                    continue

                for p in root.rglob('*'):
                    if p.is_file() and p.suffix.lower() in ['.mkv', '.mp4', '.avi'] and p not in seen:
                        out.append(p)
                        seen.add(p)
            return out

        if config.get('scan_snapshot'):
            # Snapshot scan avoids infinite loops when dest is inside src (or same).
            files = _iter_input_files()
            for filepath in files:
                process_file(filepath, config)
                console_logger.info("-" * 20)
        else:
            for filepath in _iter_input_files():
                process_file(filepath, config)
                console_logger.info("-" * 20)

        console_logger.info("Process completed.")
        file_logger.info("Process completed.")
    finally:
        release_lock()
        _cleanup_temp_files()


if __name__ == "__main__":
    main()
