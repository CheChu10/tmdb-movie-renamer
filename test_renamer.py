import errno
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, Dict, cast

import requests
from unittest.mock import patch, MagicMock
from pathlib import Path

import renamer  # Import the script we want to test


LEGACY_DESTINATION_TEMPLATE = (
    "{COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/"
    "{COLLECTION_NAME}/{TITLE} ({YEAR}) {IMDB}/"
    "{TITLE} ({YEAR}) {IMDB} - "
    "[{VF}{SOURCE|ifexists: (%value%)}{HDR|ifexists:, %value%}{VC|ifexists:, %value%}{AC|ifexists:, %value%}]"
)

class TestFilenameSanitization(unittest.TestCase):

    def test_sanitize_filename_cases(self):
        cases = [
            {'name': '15:17 Tren a París', 'expected': '15.17 Tren a París'},
            {'name': '15:17', 'expected': '15.17'},
            {'name': 'A/B:C', 'expected': 'A -B -C'},
        ]

        for c in cases:
            with self.subTest(name=c['name']):
                self.assertEqual(renamer.sanitize_filename(c['name']), c['expected'])


class TestMediaAnalysis(unittest.TestCase):

    def test_detect_hdr_label_prefers_dolby_vision(self):
        track = SimpleNamespace(
            hdr_format='Dolby Vision',
            hdr_format_string=None,
            hdr_format_commercial=None,
            hdr_format_compatibility=None,
            transfer_characteristics=None,
            transfer_characteristics_original=None,
            bit_depth='10',
            colour_primaries='BT.2020',
            color_primaries=None,
        )
        self.assertEqual(renamer._detect_hdr_label(track), 'Dolby Vision')

    def test_detect_hdr_label_not_only_bit_depth(self):
        track = SimpleNamespace(
            hdr_format=None,
            hdr_format_string=None,
            hdr_format_commercial=None,
            hdr_format_compatibility=None,
            transfer_characteristics=None,
            transfer_characteristics_original=None,
            bit_depth='10',
            colour_primaries='BT.709',
            color_primaries=None,
        )
        self.assertEqual(renamer._detect_hdr_label(track), '')


class TestMovieNameExtraction(unittest.TestCase):

    def test_extraction_cases(self):
        cases = [
            {
                'filename': "La cueva de Barron (Barron's Cove) (2024).mkv",
                'name': 'La cueva de Barron',
                'year': '2024',
                'fallback': "Barron's Cove",
            },
            {
                'filename': 'Dune (2021).mkv',
                'name': 'Dune',
                'year': '2021',
                'fallback': None,
            },
            {
                'filename': 'Alien (1979).mkv',
                'name': 'Alien',
                'year': '1979',
                'fallback': None,
            },
            {
                'filename': 'Some Movie (3000).mkv',
                'name': 'Some Movie',
                'year': None,
                'fallback': None,
            },
            {
                'filename': 'Pulp Fiction.mkv',
                'name': 'Pulp Fiction',
                'year': None,
                'fallback': None,
            },
            {
                'filename': 'Inception [1080p BluRay] (2010).mkv',
                'name': 'Inception',
                'year': '2010',
                'fallback': None,
            },
            {
                'filename': 'Movie Title (Fallback Title) (Another Tag) (2022).mkv',
                'name': 'Movie Title',
                'year': '2022',
                'fallback': 'Fallback Title',
            },
            # Torrent-style / backups / noisy names
            {
                'filename': 'Movie.Title.2021.1080p.WEB-DL.DD5.1.x264-GROUP.mkv',
                'name': 'Movie Title 2021 1080p WEB-DL DD5 1 x264-GROUP',
                'year': None,
                'fallback': None,
            },
            {
                'filename': 'Movie Title (2021) (1080p WEB-DL x264) (GROUP).mkv',
                'name': 'Movie Title',
                'year': '2021',
                'fallback': None,
            },
            {
                'filename': 'Movie Title (Director\'s Cut) (2021) (BluRay).mkv',
                'name': 'Movie Title',
                'year': '2021',
                'fallback': "Director's Cut",
            },
            {
                'filename': 'Movie Title (2021) [BACKUP].mkv',
                'name': 'Movie Title',
                'year': '2021',
                'fallback': None,
            },
            {
                'filename': 'Movie Title (Remastered) (1972) [1080p].mkv',
                'name': 'Movie Title',
                'year': '1972',
                'fallback': 'Remastered',
            },
        ]

        for c in cases:
            with self.subTest(filename=c['filename']):
                name, year, fallback = renamer.get_movie_name_and_year(c['filename'])
                self.assertEqual(name, c['name'])
                self.assertEqual(year, c['year'])
                self.assertEqual(fallback, c['fallback'])


class TestTmdbInfoFetcher(unittest.TestCase):

    @patch('renamer.requests.get')
    def test_search_finds_on_first_try(self, mock_get):
        """
        Simulates the API finding a match with the primary title and year.
        """
        # Mock the search response
        mock_search_response = MagicMock()
        mock_search_response.json.return_value = {'results': [{'id': 123}]}
        mock_search_response.raise_for_status.return_value = None

        # Mock the movie details response
        mock_details_response = MagicMock()
        mock_details_response.json.return_value = {'id': 123, 'title': 'Correct Movie'}
        mock_details_response.raise_for_status.return_value = None

        mock_get.side_effect = [mock_search_response, mock_details_response]

        result = renamer.get_tmdb_info('fake_api_key', 'Correct Movie (2023).mkv', 'en', None, debug=False)

        self.assertIsNotNone(result)
        result = cast(Dict[str, Any], result)
        self.assertEqual(result['title'], 'Correct Movie')
        # Verify it was called with the primary title and year
        self.assertEqual(mock_get.call_args_list[0].kwargs['params']['query'], 'Correct Movie')
        self.assertEqual(mock_get.call_args_list[0].kwargs['params']['primary_release_year'], '2023')

    @patch('renamer.requests.get')
    def test_find_by_imdb_id_skips_search(self, mock_get):
        mock_find = MagicMock()
        mock_find.json.return_value = {'movie_results': [{'id': 111}]}
        mock_find.raise_for_status.return_value = None

        mock_details = MagicMock()
        mock_details.json.return_value = {'id': 111, 'title': 'From IMDb', 'external_ids': {'imdb_id': 'tt1234567'}}
        mock_details.raise_for_status.return_value = None

        mock_get.side_effect = [mock_find, mock_details]

        out = renamer.get_tmdb_info('fake_api_key', 'Some Movie (2023) [tt1234567].mkv', 'en', None, debug=False)
        self.assertIsNotNone(out)

        # First call must be /find
        self.assertIn('/find/tt1234567', mock_get.call_args_list[0].args[0])
        self.assertEqual(mock_get.call_args_list[0].kwargs['params']['external_source'], 'imdb_id')

        # Second call must be /movie/111
        self.assertIn('/movie/111', mock_get.call_args_list[1].args[0])

    @patch('renamer.requests.get')
    def test_search_uses_fallback_title(self, mock_get):
        """
        Simulates the API failing on the primary title but finding on the fallback.
        """
        # Mock responses
        mock_search_fail = MagicMock()
        mock_search_fail.json.return_value = {'results': []} # No results for primary title
        mock_search_fail.raise_for_status.return_value = None

        mock_search_success = MagicMock()
        mock_search_success.json.return_value = {'results': [{'id': 456}]} # Found on fallback
        mock_search_success.raise_for_status.return_value = None

        mock_details_response = MagicMock()
        mock_details_response.json.return_value = {'id': 456, 'title': 'Fallback Movie'}
        mock_details_response.raise_for_status.return_value = None

        # The API will be called: 1. Primary w/year (fail), 2. Primary w/o year (fail), 3. Fallback w/year (success), 4. Details
        mock_get.side_effect = [mock_search_fail, mock_search_fail, mock_search_success, mock_details_response]

        result = renamer.get_tmdb_info('fake_api_key', 'Titulo Primario (Fallback Movie) (2023).mkv', 'en', None, debug=False)

        self.assertIsNotNone(result)
        result = cast(Dict[str, Any], result)
        self.assertEqual(result['title'], 'Fallback Movie')
        # Check that the successful call used the fallback query
        self.assertEqual(mock_get.call_args_list[2].kwargs['params']['query'], 'Fallback Movie')

    @patch('renamer.requests.get')
    def test_search_uses_no_year_fallback(self, mock_get):
        """
        Simulates failing with year, but succeeding without it.
        """
        mock_fail_with_year = MagicMock(json=MagicMock(return_value={'results': []}))
        mock_success_without_year = MagicMock(json=MagicMock(return_value={'results': [{'id': 789}]}))
        mock_details = MagicMock(json=MagicMock(return_value={'id': 789, 'title': 'Movie With Wrong Year'}))
        
        mock_get.side_effect = [mock_fail_with_year, mock_success_without_year, mock_details]

        result = renamer.get_tmdb_info('fake_api_key', 'Movie With Wrong Year (2000).mkv', 'en', None, debug=False)

        self.assertIsNotNone(result)
        result = cast(Dict[str, Any], result)
        self.assertEqual(result['title'], 'Movie With Wrong Year')
        # First call has the year
        self.assertIn('primary_release_year', mock_get.call_args_list[0].kwargs['params'])
        # Second call does not
        self.assertNotIn('primary_release_year', mock_get.call_args_list[1].kwargs['params'])

    @patch('renamer.requests.get')
    def test_search_fails_completely(self, mock_get):
        """
        Simulates the API finding no results for any title combination.
        """
        mock_search_fail = MagicMock()
        mock_search_fail.json.return_value = {'results': []}
        mock_search_fail.raise_for_status.return_value = None

        # It will try title w/ year, title w/o year, fallback w/ year, fallback w/o year
        mock_get.side_effect = [mock_search_fail, mock_search_fail, mock_search_fail, mock_search_fail]

        result = renamer.get_tmdb_info('fake_api_key', 'NonExistent Movie (Also Fake) (2023).mkv', 'en', None, debug=False)
        self.assertIsNone(result)
        self.assertEqual(mock_get.call_count, 4)


class TestRetryAndRateLimit(unittest.TestCase):

    def setUp(self):
        renamer._TMDB_RATE_LIMIT_UNTIL = 0.0

    @patch('renamer.requests.get')
    def test_make_tmdb_request_does_not_retry_on_401(self, mock_get):
        resp = MagicMock(status_code=401, headers={})

        def raise_401():
            raise requests.HTTPError("401", response=MagicMock(status_code=401, headers={}))

        resp.raise_for_status.side_effect = raise_401
        mock_get.return_value = resp

        with self.assertRaises(requests.HTTPError):
            renamer._make_tmdb_request('https://example.invalid', {}, {})

        self.assertEqual(mock_get.call_count, 1)

    @patch('renamer.requests.get')
    def test_make_tmdb_request_retries_on_500(self, mock_get):
        now = [0.0]

        def fake_time():
            return now[0]

        def fake_sleep(secs):
            now[0] += secs

        resp1 = MagicMock(status_code=500, headers={})
        resp2 = MagicMock(status_code=500, headers={})
        resp3 = MagicMock(status_code=200, headers={})

        def raise_500():
            raise requests.HTTPError("500", response=MagicMock(status_code=500, headers={}))

        resp1.raise_for_status.side_effect = raise_500
        resp2.raise_for_status.side_effect = raise_500
        resp3.raise_for_status.return_value = None
        resp3.json.return_value = {'ok': True}

        mock_get.side_effect = [resp1, resp2, resp3]

        with patch('renamer.time.time', side_effect=fake_time), patch('renamer.time.sleep', side_effect=fake_sleep):
            out = renamer._make_tmdb_request('https://example.invalid', {}, {})

        self.assertEqual(out, {'ok': True})
        self.assertEqual(mock_get.call_count, 3)
        # backoff should have slept 1s then 2s
        self.assertEqual(now[0], 3.0)

        # timeout must be applied
        for call in mock_get.call_args_list:
            self.assertEqual(call.kwargs.get('timeout'), renamer.REQUEST_TIMEOUT)

    @patch('renamer.requests.get')
    def test_make_tmdb_request_honors_retry_after_on_429(self, mock_get):
        renamer._TMDB_RATE_LIMIT_UNTIL = 0.0

        now = [0.0]

        def fake_time():
            return now[0]

        def fake_sleep(secs):
            now[0] += secs

        resp1 = MagicMock(status_code=429, headers={'Retry-After': '3'})

        def raise_429():
            raise requests.HTTPError("429", response=MagicMock(status_code=429, headers={'Retry-After': '3'}))

        resp1.raise_for_status.side_effect = raise_429

        resp2 = MagicMock(status_code=200, headers={})
        resp2.raise_for_status.return_value = None
        resp2.json.return_value = {'ok': True}

        mock_get.side_effect = [resp1, resp2]

        with patch('renamer.time.time', side_effect=fake_time), patch('renamer.time.sleep', side_effect=fake_sleep):
            out = renamer._make_tmdb_request('https://example.invalid', {}, {})

        self.assertEqual(out, {'ok': True})
        self.assertEqual(mock_get.call_count, 2)
        # should have waited at least Retry-After seconds
        self.assertGreaterEqual(now[0], 3.0)


class TestFileActionsAtomic(unittest.TestCase):

    def test_copy_is_atomic_no_partial_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td) / 'src'
            dest_dir = Path(td) / 'dest'
            src_dir.mkdir()
            dest_dir.mkdir()

            src = src_dir / 'file.bin'
            dest = dest_dir / 'file.bin'

            src.write_bytes(b'a' * (1024 * 128))

            original_copyfileobj = shutil.copyfileobj

            def failing_copyfileobj(fsrc, fdst, length=0):
                # Write some bytes then fail (simulates interruption)
                fdst.write(fsrc.read(4096))
                fdst.flush()
                raise OSError('simulated copy failure')

            with patch('renamer.shutil.copyfileobj', side_effect=failing_copyfileobj):
                with self.assertRaises(OSError):
                    renamer._atomic_copy(src, dest)

            # Must not leave final dest
            self.assertFalse(dest.exists())

            # Must not leave temp files
            leftovers = list(dest_dir.glob(f"{renamer.TEMP_PREFIX}*"))
            self.assertEqual(leftovers, [])

    def test_move_cross_device_fallback_is_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td) / 'src'
            dest_dir = Path(td) / 'dest'
            src_dir.mkdir()
            dest_dir.mkdir()

            src = src_dir / 'file.bin'
            dest = dest_dir / 'file.bin'
            src.write_bytes(b'hello')

            # Force first os.replace(src, dest) to behave like cross-device move,
            # but allow os.replace(tmp, dest) inside _atomic_copy to work.
            real_replace = os.replace

            def replace_side_effect(a, b):
                if Path(a) == src and Path(b) == dest:
                    raise OSError(errno.EXDEV, 'Cross-device link')
                return real_replace(a, b)

            with patch('renamer.os.replace', side_effect=replace_side_effect):
                renamer._atomic_move(src, dest)

            self.assertFalse(src.exists())
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), b'hello')

            leftovers = list(dest_dir.glob(f"{renamer.TEMP_PREFIX}*"))
            self.assertEqual(leftovers, [])


class TestCollectionNameNormalization(unittest.TestCase):

    def test_strip_collection_designator_basic(self):
        self.assertEqual(renamer.strip_collection_designator('Harry Potter Collection'), 'Harry Potter')

    def test_strip_collection_designator_spanish_article(self):
        self.assertEqual(renamer.strip_collection_designator('Mononoke - la colección'), 'Mononoke')

    def test_strip_collection_designator_parenthetical(self):
        self.assertEqual(renamer.strip_collection_designator('Foo (Collection)'), 'Foo')

    def test_strip_collection_designator_fullwidth_cjk(self):
        self.assertEqual(renamer.strip_collection_designator('熊猫计划（系列）'), '熊猫计划')


class TestCollectionLocalizationDebug(unittest.TestCase):

    def setUp(self):
        renamer._COLLECTION_NAME_CACHE.clear()

    @patch('renamer._make_tmdb_request')
    def test_collection_name_kept_when_region_specific_translation_missing(self, mock_request):
        tmdb_data = {
            'belongs_to_collection': {
                'id': 1421776,
                'name': '熊猫计划（系列）',
            }
        }

        mock_request.return_value = {
            'translations': [
                {
                    'iso_639_1': 'es',
                    'iso_3166_1': 'MX',
                    'data': {'name': 'Operación Panda: Colección'},
                }
            ]
        }

        renamer.apply_preferred_collection_name(tmdb_data, {}, 'es', 'ES', debug=False)
        self.assertEqual(tmdb_data['belongs_to_collection']['name'], '熊猫计划（系列）')

    @patch('renamer._make_tmdb_request')
    def test_collection_debug_shows_language_breakdown(self, mock_request):
        tmdb_data = {
            'belongs_to_collection': {
                'id': 1421776,
                'name': 'Panda Plan Collection',
            }
        }

        mock_request.return_value = {
            'translations': [
                {
                    'iso_639_1': 'es',
                    'iso_3166_1': 'ES',
                    'data': {'name': 'Panda Plan'},
                },
                {
                    'iso_639_1': 'es',
                    'iso_3166_1': 'MX',
                    'data': {'name': 'Operación Panda: Misión rescate'},
                },
            ]
        }

        with patch('renamer.console_logger.info') as mock_info:
            renamer.apply_preferred_collection_name(tmdb_data, {}, 'es', 'ES', debug=True)

        self.assertEqual(tmdb_data['belongs_to_collection']['name'], 'Panda Plan')

        messages = "\n".join(
            str(c.args[0]) for c in mock_info.call_args_list if c.args
        )
        self.assertIn('TMDB collection translations found for es (requested region=ES)', messages)
        self.assertIn("TMDB collection es-ES name candidate: 'Panda Plan'.", messages)


class TestPathOverlapClassification(unittest.TestCase):

    def test_classify_overlap_none(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a'
            b = Path(td) / 'b'
            a.mkdir()
            b.mkdir()
            self.assertEqual(renamer.classify_path_overlap(a, b), 'none')

    def test_classify_overlap_same(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a'
            a.mkdir()
            self.assertEqual(renamer.classify_path_overlap(a, a), 'same')

    def test_classify_overlap_src_within_dest(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'dest'
            src = dest / 'sub'
            dest.mkdir()
            src.mkdir()
            self.assertEqual(renamer.classify_path_overlap(src, dest), 'src_within_dest')

    def test_classify_overlap_dest_within_src(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'src'
            dest = src / 'out'
            src.mkdir()
            dest.mkdir()
            self.assertEqual(renamer.classify_path_overlap(src, dest), 'dest_within_src')


class TestSourceExpansion(unittest.TestCase):

    def test_expand_src_inputs_glob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '120').mkdir()
            (root / '121').mkdir()
            (root / '130').mkdir()

            out = renamer._expand_src_inputs([str(root / '12*')])
            self.assertEqual(set(out), {root / '120', root / '121'})

    def test_expand_src_inputs_plain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'src').mkdir()
            out = renamer._expand_src_inputs([str(root / 'src')])
            self.assertEqual(out, [root / 'src'])


class TestSourceParsing(unittest.TestCase):

    def test_parse_source_variants(self):
        cases = [
            {
                'source': 'WEBRip',
                'names': [
                    'Movie (2020) [1080p (WEBRip)].mkv',
                    'Movie (2020) [1080p (WEB-Rip)].mkv',
                    'Movie (2020) [1080p (WebRip)].mkv',
                    'Movie (2020) [1080p (WEB RIP)].mkv',
                ],
            },
            {
                'source': 'WEB-DL',
                'names': [
                    'Movie (2020) [1080p (WEBDL)].mkv',
                    'Movie (2020) [1080p (WEB-DL)].mkv',
                    'Movie (2020) [1080p (webdl)].mkv',
                ],
            },
        ]

        for c in cases:
            for name in c['names']:
                with self.subTest(name=name, source=c['source']):
                    self.assertEqual(renamer.parse_source_from_filename(name), c['source'])


class TestPathBuilding(unittest.TestCase):

    def test_path_building_simple(self):
        """
        Tests the destination path construction for a standard movie.
        """
        tmdb_data = {
            'title': 'Inception',
            'release_date': '2010-07-16',
            'external_ids': {'imdb_id': 'tt1375666'}
        }
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}
        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            'BluRay',
            Path('/movies'),
            'en',
            '.mkv',
            destination_template=LEGACY_DESTINATION_TEMPLATE,
        )
        expected = Path('/movies/I/Inception (2010) [tt1375666]/Inception (2010) [tt1375666] - [1080p (BluRay), x264, EAC3].mkv')
        self.assertEqual(dest_path, expected)

    def test_path_building_with_collection(self):
        """
        Tests that a collection subfolder is correctly added to the path.
        """
        tmdb_data = {
            'title': 'Harry Potter and the Sorcerer\'s Stone',
            'release_date': '2001-11-16',
            'external_ids': {'imdb_id': 'tt0241527'},
            'belongs_to_collection': {'name': 'Harry Potter Collection'}
        }
        media_info = {'vf': '2160p', 'vc': 'x265', 'ac': 'TrueHD', 'hdr': 'HDR'}
        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            'UHD BDRemux',
            Path('/movies'),
            'en',
            '.mkv',
            destination_template=LEGACY_DESTINATION_TEMPLATE,
        )
        # This test now reflects the intended behavior of having a hyphen separator.
        expected = Path('/movies/H/Harry Potter - Collection/Harry Potter and the Sorcerer\'s Stone (2001) [tt0241527]/Harry Potter and the Sorcerer\'s Stone (2001) [tt0241527] - [2160p (UHD BDRemux), HDR, x265, TrueHD].mkv')
        self.assertEqual(dest_path, expected)


class TestDestinationTemplateParameterization(unittest.TestCase):

    def _base_tmdb_data(self) -> Dict[str, Any]:
        return {
            'id': 27205,
            'title': 'Inception',
            'original_title': 'Inception',
            'release_date': '2010-07-16',
            'external_ids': {'imdb_id': 'tt1375666'},
        }

    def test_custom_template_with_lang_and_region(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}
        template = "{LANG}/{REGION}/{YEAR}/{TITLE}/{TITLE} - {VF}"

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            'BluRay',
            Path('/movies'),
            'en',
            '.mkv',
            destination_template=template,
            region='US',
        )

        expected = Path('/movies/en/US/2010/Inception/Inception - 1080p.mkv')
        self.assertEqual(dest_path, expected)

    def test_collection_name_tag_is_empty_when_not_in_collection(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}
        template = "{COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/{COLLECTION_NAME}/{TITLE} ({YEAR}) {IMDB}/{TITLE} ({YEAR}) {IMDB}"

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template=template,
        )

        expected = Path('/movies/I/Inception (2010) [tt1375666]/Inception (2010) [tt1375666].mkv')
        self.assertEqual(dest_path, expected)

    def test_first_letter_can_be_computed_with_fallback_expression(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/{TITLE}",
        )

        expected = Path('/movies/I/Inception.mkv')
        self.assertEqual(dest_path, expected)

    def test_unknown_template_tag_raises(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                None,
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="{TITLE}/{UNKNOWN_TAG}/{TITLE}",
            )

    def test_name_alias_is_rejected(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                None,
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="{NAME}",
            )

    def test_ext_field_is_rejected(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                None,
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="{TITLE}{EXT}",
            )

    def test_lowercase_field_and_dot_shorthand_work(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{title[0].upper}/{title.upper}",
        )

        expected = Path('/movies/I/INCEPTION.mkv')
        self.assertEqual(dest_path, expected)

    def test_to_upper_alias_is_rejected(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                None,
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="{title.toUpper}",
            )

    def test_unknown_template_filter_raises(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                None,
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="{TITLE|explode}",
            )

    def test_technical_fields_are_fully_composable_without_aggregate(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template=(
                "{TITLE}/{TITLE} - "
                "[{VF}, {VC}, {AC}]"
            ),
        )

        expected = Path('/movies/Inception/Inception - [1080p, x264, EAC3].mkv')
        self.assertEqual(dest_path, expected)

    def test_ifcontains_rule_filter(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/{TITLE}{TITLE|ifcontains:ception: [MATCH]}",
        )

        expected = Path('/movies/Inception/Inception [MATCH].mkv')
        self.assertEqual(dest_path, expected)

    def test_rule_text_supports_explicit_field_variables(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            'BluRay',
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/{SOURCE|ifexists:${TITLE} - %value%:NO}",
        )

        expected = Path('/movies/Inception/Inception - BluRay.mkv')
        self.assertEqual(dest_path, expected)

    def test_rule_text_rejects_legacy_dollar_field_token(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                'BluRay',
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="{TITLE}/{SOURCE|ifexists:$TITLE - %value%}",
            )

    def test_ifge_rule_filter_for_fps(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None, 'fps': 60.0}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/{TITLE} - {FPS|ifge:60:%value%FPS}",
        )

        expected = Path('/movies/Inception/Inception - 60FPS.mkv')
        self.assertEqual(dest_path, expected)

    def test_ifexists_rule_filter(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': 'HDR10'}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/{TITLE}{HDR|ifexists: - %value%}",
        )

        expected = Path('/movies/Inception/Inception - HDR10.mkv')
        self.assertEqual(dest_path, expected)

    def test_ifexists_branch_matrix(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        cases = [
            {
                'label': 'empty then, source present',
                'template': "{TITLE}/{TITLE}{SOURCE|ifexists::NOEXISTE}",
                'source': 'BluRay',
                'expected': Path('/movies/Inception/Inception.mkv'),
            },
            {
                'label': 'empty then, source missing',
                'template': "{TITLE}/{TITLE}{SOURCE|ifexists::NOEXISTE}",
                'source': None,
                'expected': Path('/movies/Inception/InceptionNOEXISTE.mkv'),
            },
            {
                'label': 'then/else, source present',
                'template': "{TITLE}/{TITLE}{SOURCE|ifexists:SIEXISTE:NOEXISTE}",
                'source': 'BluRay',
                'expected': Path('/movies/Inception/InceptionSIEXISTE.mkv'),
            },
            {
                'label': 'then/else, source missing',
                'template': "{TITLE}/{TITLE}{SOURCE|ifexists:SIEXISTE:NOEXISTE}",
                'source': None,
                'expected': Path('/movies/Inception/InceptionNOEXISTE.mkv'),
            },
        ]

        for case in cases:
            with self.subTest(case=case['label']):
                result = renamer.build_destination_path(
                    tmdb_data,
                    media_info,
                    case['source'],
                    Path('/movies'),
                    'en',
                    '.mkv',
                    destination_template=cast(str, case['template']),
                )
                self.assertEqual(result, cast(Path, case['expected']))

    def test_fallback_accepts_literal_text(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{COLLECTION_NAME|fallback:No Collection}/{TITLE}",
        )

        expected = Path('/movies/No Collection/Inception.mkv')
        self.assertEqual(dest_path, expected)

    def test_fallback_uses_explicit_variable_marker(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        literal_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{COLLECTION_NAME|fallback:TITLE}/{TITLE}",
        )

        variable_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{COLLECTION_NAME|fallback:${TITLE}}/{TITLE}",
        )

        legacy_braced_literal_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{COLLECTION_NAME|fallback:{TITLE}}/{TITLE}",
        )

        self.assertEqual(literal_path, Path('/movies/TITLE/Inception.mkv'))
        self.assertEqual(variable_path, Path('/movies/Inception/Inception.mkv'))
        self.assertEqual(legacy_braced_literal_path, Path('/movies/{TITLE}/Inception.mkv'))

    def test_template_dot_segments_are_rejected(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        with self.assertRaises(ValueError):
            renamer.build_destination_path(
                tmdb_data,
                media_info,
                None,
                Path('/movies'),
                'en',
                '.mkv',
                destination_template="../{TITLE}",
            )

    def test_literals_between_tags_are_rendered(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/holi - {YEAR} - literal",
        )

        expected = Path('/movies/Inception/holi - 2010 - literal.mkv')
        self.assertEqual(dest_path, expected)

    def test_local_filename_field_is_exposed_for_rules(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/{LOCAL_FILENAME|ifcontains:WEB-DL:[LOCAL]}",
            local_filename='Inception.2010.WEB-DL.x264.mkv',
        )

        expected = Path('/movies/Inception/[LOCAL].mkv')
        self.assertEqual(dest_path, expected)

    def test_stem_filter_derives_local_filename_basename(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template="{TITLE}/{LOCAL_FILENAME|stem}",
            local_filename='Inception.2010.WEB-DL.x264.mkv',
        )

        expected = Path('/movies/Inception/Inception.2010.WEB-DL.x264.mkv')
        self.assertEqual(dest_path, expected)


class TestTemplatePresets(unittest.TestCase):

    def _base_tmdb_data(self) -> Dict[str, Any]:
        return {
            'id': 27205,
            'title': 'Inception',
            'original_title': 'Inception',
            'release_date': '2010-07-16',
            'external_ids': {'imdb_id': 'tt1375666'},
        }

    def test_resolve_known_preset(self):
        resolved = renamer.resolve_destination_template('preset:plex')
        self.assertEqual(resolved, renamer.TEMPLATE_PRESETS['plex'])

    def test_resolve_known_preset_short_name(self):
        resolved = renamer.resolve_destination_template('plex')
        self.assertEqual(resolved, renamer.TEMPLATE_PRESETS['plex'])

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            renamer.resolve_destination_template('preset:not_exists')

    def test_build_path_with_movie_year_presets(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        cases = [
            ('preset:plex', Path('/movies/Inception (2010)/Inception (2010).mkv')),
            ('preset:emby', Path('/movies/Inception (2010)/Inception (2010).mkv')),
        ]

        for preset, expected in cases:
            with self.subTest(preset=preset):
                dest_path = renamer.build_destination_path(
                    tmdb_data,
                    media_info,
                    None,
                    Path('/movies'),
                    'en',
                    '.mkv',
                    destination_template=preset,
                )
                self.assertEqual(dest_path, expected)

    def test_build_path_with_jellyfin_preset_uses_doc_id_shape(self):
        tmdb_data = self._base_tmdb_data()
        media_info = {'vf': '1080p', 'vc': 'x264', 'ac': 'EAC3', 'hdr': None}

        dest_path = renamer.build_destination_path(
            tmdb_data,
            media_info,
            None,
            Path('/movies'),
            'en',
            '.mkv',
            destination_template='preset:jellyfin',
        )

        expected = Path('/movies/Inception (2010) [imdbid-tt1375666]/Inception (2010) [imdbid-tt1375666].mkv')
        self.assertEqual(dest_path, expected)

class TestConfigurationLoading(unittest.TestCase):

    def test_setup_configuration_accepts_rule_variables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / 'src'
            dest = root / 'dest'
            src.mkdir()
            dest.mkdir()

            config_path = root / 'config.ini'
            config_path.write_text(
                (
                    "[TMDB]\n"
                    "api_key = dummy-token\n\n"
                    "[TEMPLATES]\n"
                    "destination_template = {COLLECTION_NAME|fallback:${TITLE}}/{SOURCE|ifexists: (%value%)}\n"
                ),
                encoding='utf-8',
            )

            fake_script_path = root / 'renamer.py'
            fake_script_path.write_text('', encoding='utf-8')

            argv = [
                'renamer.py',
                '--src',
                str(src),
                '--dest',
                str(dest),
                '--action',
                'test',
                '--lang',
                'es',
            ]

            with patch.object(renamer, '__file__', str(fake_script_path)):
                with patch.object(sys, 'argv', argv):
                    cfg = renamer.setup_configuration()

            self.assertIsNotNone(cfg)
            cfg = cast(Dict[str, Any], cfg)
            self.assertEqual(cfg['destination_template'], '{COLLECTION_NAME|fallback:${TITLE}}/{SOURCE|ifexists: (%value%)}')

    def test_setup_configuration_rejects_value_as_dollar_variable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / 'src'
            dest = root / 'dest'
            src.mkdir()
            dest.mkdir()

            config_path = root / 'config.ini'
            config_path.write_text(
                (
                    "[TMDB]\n"
                    "api_key = dummy-token\n\n"
                    "[TEMPLATES]\n"
                    "destination_template = {TITLE}/{SOURCE|ifexists: (${VALUE})}\n"
                ),
                encoding='utf-8',
            )

            fake_script_path = root / 'renamer.py'
            fake_script_path.write_text('', encoding='utf-8')

            argv = [
                'renamer.py',
                '--src',
                str(src),
                '--dest',
                str(dest),
                '--action',
                'test',
                '--lang',
                'es',
            ]

            with patch.object(renamer, '__file__', str(fake_script_path)):
                with patch.object(sys, 'argv', argv):
                    cfg = renamer.setup_configuration()

            self.assertIsNone(cfg)



if __name__ == '__main__':
    unittest.main()
