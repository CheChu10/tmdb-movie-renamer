#!/usr/bin/env python3
"""
Movie Renamer Script

This script automates the renaming and organization of movie files by fetching
metadata from TheMovieDB (TMDB) and analyzing local media file information.
It is designed to be a powerful and flexible replacement for tools like FileBot.
"""

import argparse
import configparser
import json
import logging
import re
import requests
import shutil
import sys

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from colorama import Fore, Style, init
from pymediainfo import MediaInfo

from RTN import parse as rtn_parse

# --- Constants ---
# Using type hints for better clarity on the dictionary structure
COLOR_MAP: Dict[str, str] = {
    'TEST': Fore.YELLOW,
    'MOVE': Fore.GREEN,
    'COPY': Fore.CYAN,
    'SKIP': Fore.MAGENTA,
    'ERROR': Fore.RED,
}

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
file_handler = logging.FileHandler(log_file_path)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
file_logger.addHandler(file_handler)


# --- Helper Functions ---

def sanitize_filename(name: str) -> str:
    """Replaces characters illegal in Windows filenames with ' -'."""
    illegal_chars = r'<>:"/\\|?*'
    sanitized_name = name
    for char in illegal_chars:
        sanitized_name = sanitized_name.replace(char, ' -')
    return sanitized_name.strip()


def get_movie_name_and_year(filename: str, debug: bool = False) -> Tuple[str, Optional[str], Optional[str]]:
    """Extracts a searchable movie title, year and a single fallback title from a filename."""
    stem = Path(filename).stem
    parsed = rtn_parse(stem) 

    name = re.sub(r'[._]', ' ', stem)

    fallback = parsed.parsed_title or None
    year_match = re.search(r'\((19[89]\d|20[0-4]\d)\)', name)
    year = year_match.group(1) if year_match else None

    if year_match:
        name = name[:year_match.start()].strip()

    name = re.sub(r'\[.*?\]|\(.*?\)', '', name).strip()

    if debug:
        console_logger.info(
            Fore.CYAN + f"[DEBUG] Extracted Name: '{name}' | Year: '{year}' | Fallback: '{fallback}'"
        )
    return name, year, fallback


def get_language_code(lang_input: str) -> str:
    """Converts a user-friendly language string to an ISO 639-1 code."""
    lang_map = {
        'es': 'es', 'spa': 'es', 'spanish': 'es', 'español': 'es',
        'en': 'en', 'eng': 'en', 'english': 'en',
        'fr': 'fr', 'fre': 'fr', 'french': 'fr', 'francés': 'fr',
        'de': 'de', 'ger': 'de', 'german': 'de', 'deutsch': 'de',
        'it': 'it', 'ita': 'it', 'italian': 'it', 'italiano': 'it',
        'pt': 'pt', 'por': 'pt', 'portuguese': 'pt', 'portugués': 'pt',
        'ja': 'ja', 'jpn': 'ja', 'japanese': 'ja', '日本語': 'ja',
        'zh': 'zh', 'chi': 'zh', 'chinese': 'zh', '中文': 'zh',
        'ko': 'ko', 'kor': 'ko', 'korean': 'ko', '한국어': 'ko',
        'ru': 'ru', 'rus': 'ru', 'russian': 'ru', 'русский': 'ru',
        'ar': 'ar', 'ara': 'ar', 'arabic': 'ar', 'العربية': 'ar',
        'hi': 'hi', 'hin': 'hi', 'hindi': 'hi', 'हिन्दी': 'hi',
        'nl': 'nl', 'dut': 'nl', 'nld': 'nl', 'dutch': 'nl', 'nederlands': 'nl',
    }
    return lang_map.get(lang_input.lower(), 'es')


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


def get_tmdb_info(api_key: str, filename: str, lang_code: str, debug: bool) -> Optional[Dict[str, Any]]:
    """Fetches movie data from TheMovieDB API using a Bearer Token."""
    title, year, fallback = get_movie_name_and_year(filename, debug=debug)
    if not title:
        file_logger.warning(f"Could not extract a valid title from '{filename}'")
        return None

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

        resp = requests.get(search_url, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json().get('results', [])

        if debug:
            console_logger.info(Fore.CYAN + "[DEBUG] API Search Response (up to 5 results):")
            console_logger.info(Fore.CYAN + json.dumps(results[:5], indent=2, ensure_ascii=False))

        return results

    try:
        results: List[Dict[str, Any]] = []
        for candidate in [title] + ([fallback] if fallback else []):
            year_options = (True, False) if year else (False,)

            for use_year in year_options:
                results = _search(candidate, use_year)
                if results:
                    title = candidate 
                    break
            if results:
                break

        if not results:
            file_logger.warning(f"No results found on TMDB for '{title}' ({year or 'N/A'})")
            return None

        # Llamada única a detalles de película
        movie_id = results[0]['id']
        movie_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        movie_params = {'append_to_response': 'external_ids,collection', 'language': lang_code}

        if debug:
            console_logger.info(Fore.CYAN + "[DEBUG] Calling TMDB Movie Details API...")
            console_logger.info(Fore.CYAN + f"        URL: {movie_url}")
            console_logger.info(Fore.CYAN + f"        Params: {movie_params}")

        resp = requests.get(movie_url, params=movie_params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    except requests.RequestException as e:
        file_logger.error(f"Network error contacting TMDB: {e}")
        console_logger.info(Fore.CYAN + f"[DEBUG] Network error contacting TMDB: {e}")
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
    sources = ['UHDRip', 'BDRemux', 'WEB-DL', 'WEBDL', 'BluRay', 'BDRip', 'MicroHD']
    for source in sources:
        if source.lower() in filename_lower:
            return 'WEB-DL' if source == 'WEBDL' else source
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
        suffix_words = ['Collection', 'Colección', 'Sammlung', 'Collezione']
        collection_name_base = collection_name_from_tmdb
        for word in suffix_words:
            if collection_name_base.lower().endswith(word.lower()):
                base_length = len(collection_name_base) - len(word)
                collection_name_base = collection_name_base[:base_length].strip().rstrip('-').strip()
                break
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

    # Guard clause: if we are testing, do nothing further.
    if action == 'test':
        return

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if action == 'move':
            shutil.move(src_path, dest_path)
        elif action == 'copy':
            shutil.copy2(src_path, dest_path)  # copy2 preserves metadata
    except Exception as e:
        error_prefix = f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL}"
        console_logger.error(f"{error_prefix} Could not {action} file: {e}")
        file_logger.error(f"[ERROR] Could not {action} file '{src_path}': {e}")


def process_file(filepath: Path, config: Dict[str, Any]) -> None:
    """Main processing logic for a single movie file."""
    console_logger.info(f"--- Processing file: {filepath.name} ---")
    file_logger.info(f"--- Processing file: {filepath.name} ---")

    # Guard clauses for early exit
    tmdb_data = get_tmdb_info(config['api_key'], filepath.name, config['lang_code'], config['debug'])
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
    parser.add_argument('--src', required=True, type=Path, help="Source directory containing movies to process.")
    parser.add_argument('--dest', required=True, type=Path, help="Destination directory for organized movies.")
    parser.add_argument('--lang', default='es', help="Language for metadata (e.g., es, spa, en, eng). Default: es.")
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

    if not args.src.is_dir() or not args.dest.is_dir():
        console_logger.error(f"{COLOR_MAP['ERROR']}[ERROR]{Style.RESET_ALL} Source or destination is not a valid directory.")
        return None

    return {
        'api_key': api_key,
        'input_dir': args.src,
        'output_dir': args.dest,
        'lang_code': get_language_code(args.lang),
        'action': 'test' if args.dry_run else args.action,
        'debug': args.debug,
    }


def main() -> None:
    """Main entry point for the script."""
    config = setup_configuration()
    # Guard clause: if setup fails, exit.
    if not config:
        sys.exit(1)

    action = config['action']
    file_logger.info(f"Starting recursive scan in: {config['input_dir']} with action: {action.upper()}")
    if action == 'test':
        console_logger.info(COLOR_MAP['TEST'] + "Simulation mode active. No changes will be made.")

    for filepath in config['input_dir'].rglob('*'):
        if filepath.is_file() and filepath.suffix.lower() in ['.mkv', '.mp4', '.avi']:
            process_file(filepath, config)
            console_logger.info("-" * 20)

    console_logger.info("Process completed.")
    file_logger.info("Process completed.")


if __name__ == "__main__":
    main()
