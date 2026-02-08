from typing import Dict, List


# Presets aim to follow official movie naming docs.
# Personal/custom layouts should be provided as full template expressions.
TEMPLATE_PRESETS: Dict[str, str] = {
    'jellyfin': (
        "{TITLE} ({YEAR}){IMDB_ID|ifexists: [imdbid-%value%]}/"
        "{TITLE} ({YEAR}){IMDB_ID|ifexists: [imdbid-%value%]}"
    ),
    'plex': (
        "{TITLE} ({YEAR})/"
        "{TITLE} ({YEAR})"
    ),
    'emby': (
        "{TITLE} ({YEAR})/"
        "{TITLE} ({YEAR})"
    ),
    'minimal': (
        "{TITLE}/"
        "{TITLE}"
    ),
}


TEMPLATE_PRESET_DESCRIPTIONS: Dict[str, str] = {
    'jellyfin': 'Official-style Jellyfin movie folder + file naming with optional [imdbid-tt...].',
    'plex': 'Official Plex MovieName (Year)/MovieName (Year) pattern.',
    'emby': 'Official Emby MovieName (Year)/MovieName (Year) pattern.',
    'minimal': 'Minimal title-only structure.',
}


def available_template_preset_names() -> List[str]:
    return sorted(TEMPLATE_PRESETS)


def resolve_destination_template(raw_template: str) -> str:
    candidate = (raw_template or '').strip()
    if not candidate:
        raise ValueError('destination_template cannot be empty.')

    lower = candidate.lower()
    preset_name = ''

    if lower.startswith('preset:'):
        preset_name = candidate.split(':', 1)[1].strip().lower()
    elif lower in TEMPLATE_PRESETS:
        preset_name = lower

    if preset_name:
        if preset_name not in TEMPLATE_PRESETS:
            names = ', '.join(available_template_preset_names())
            raise ValueError(f"Unknown template preset '{preset_name}'. Available presets: {names}")
        return TEMPLATE_PRESETS[preset_name]

    return candidate
