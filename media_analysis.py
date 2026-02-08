import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from pymediainfo import MediaInfo


def to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None

    frac = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if frac:
        den = int(frac.group(2))
        if den:
            return int(frac.group(1)) / den

    num = re.search(r'\d+(?:[\.,]\d+)?', text)
    if not num:
        return None

    try:
        return float(num.group(0).replace(',', '.'))
    except Exception:
        return None


def format_float_compact(value: Optional[float], max_decimals: int = 3) -> str:
    if value is None:
        return ''
    text = f"{value:.{max_decimals}f}".rstrip('0').rstrip('.')
    return text


def detect_hdr_label(video_track: Any) -> str:
    hdr_candidates = [
        getattr(video_track, 'hdr_format', None),
        getattr(video_track, 'hdr_format_string', None),
        getattr(video_track, 'hdr_format_commercial', None),
        getattr(video_track, 'hdr_format_compatibility', None),
        getattr(video_track, 'transfer_characteristics', None),
        getattr(video_track, 'transfer_characteristics_original', None),
    ]
    hdr_blob = ' '.join(str(v).strip() for v in hdr_candidates if v).lower()

    if 'dolby vision' in hdr_blob or 'dvhe' in hdr_blob:
        return 'Dolby Vision'
    if 'hdr10+' in hdr_blob or 'st 2094' in hdr_blob:
        return 'HDR10+'
    if 'hdr10' in hdr_blob or 'smpte st 2084' in hdr_blob or 'pq' in hdr_blob:
        return 'HDR10'
    if 'hlg' in hdr_blob or 'arib std-b67' in hdr_blob:
        return 'HLG'

    bit_depth = to_int(getattr(video_track, 'bit_depth', None))
    color_primaries = (
        getattr(video_track, 'colour_primaries', None)
        or getattr(video_track, 'color_primaries', None)
        or ''
    )
    color_primaries_l = str(color_primaries).lower()
    if bit_depth and bit_depth >= 10 and ('bt.2020' in color_primaries_l or '2020' in color_primaries_l):
        return 'HDR'

    return ''


def get_resolution_class(
    width: Optional[int],
    height: Optional[int],
    *,
    resolution_tolerance: float,
    resolution_class_tiers: Sequence[Tuple[int, int, str]],
) -> str:
    if not width or not height:
        return 'N/A'

    w, h = int(width), int(height)
    for w_th, h_th, label in resolution_class_tiers:
        if w >= int(w_th * resolution_tolerance) or h >= int(h_th * resolution_tolerance):
            return label

    return f"{h}p"


def extract_media_info(
    filepath: Path,
    resolution_classifier: Callable[[Optional[int], Optional[int]], str],
) -> Dict[str, Any]:
    media_info = MediaInfo.parse(filepath)

    if not media_info.video_tracks:
        raise ValueError('No video track found')
    if not media_info.audio_tracks:
        raise ValueError('No audio track found')
    if not media_info.general_tracks:
        raise ValueError('No general track found')

    video_track = media_info.video_tracks[0]
    audio_track = media_info.audio_tracks[0]
    general_track = media_info.general_tracks[0]

    writing_library = video_track.writing_library
    if writing_library and 'x264' in writing_library.lower():
        vc = 'x264'
    elif writing_library and 'x265' in writing_library.lower():
        vc = 'x265'
    else:
        vc = (video_track.format or 'N/A').upper()

    return {
        'vf': resolution_classifier(video_track.width, video_track.height),
        'vc': vc,
        'ac': (audio_track.format or 'N/A').replace('-', ''),
        'hdr': detect_hdr_label(video_track),
        'fps': to_float(getattr(video_track, 'frame_rate', None) or getattr(general_track, 'frame_rate', None)),
        'bit_depth': to_int(getattr(video_track, 'bit_depth', None)),
        'width': video_track.width,
        'height': video_track.height,
        'bitrate': general_track.overall_bit_rate,
    }


def deduce_source_from_media_info(
    media_info: Dict[str, Any],
    *,
    default_source: str,
    uhd_height_threshold: int,
    uhd_bdremux_bitrate_mbps: int,
    bdremux_bitrate_mbps: int,
    bdrip_bitrate_mbps: int,
) -> Optional[str]:
    height = media_info.get('height')
    bitrate = media_info.get('bitrate')
    if not height or not bitrate:
        return None

    bitrate_mbps = bitrate / 1_000_000
    source = default_source
    if height >= uhd_height_threshold and bitrate_mbps > uhd_bdremux_bitrate_mbps:
        source = 'UHD BDRemux'
    elif height >= uhd_height_threshold:
        source = 'UHDRip'
    elif height >= 1080 and bitrate_mbps > bdremux_bitrate_mbps:
        source = 'BDRemux'
    elif height >= 1080 and bitrate_mbps > bdrip_bitrate_mbps:
        source = 'BDRip'

    return source
