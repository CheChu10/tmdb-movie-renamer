import errno
import os
import shutil
import sys
import tempfile
import unittest
from typing import Any, Dict, cast

import requests
from unittest.mock import patch, MagicMock
from pathlib import Path

import renamer  # Import the script we want to test

class TestMovieNameExtraction(unittest.TestCase):

    def test_extract_with_fallback(self):
        """
        Tests extraction with a primary Spanish title and an English fallback title.
        """
        filename = "La cueva de Barron (Barron's Cove) (2024).mkv"
        name, year, fallback = renamer.get_movie_name_and_year(filename)
        self.assertEqual(name, "La cueva de Barron")
        self.assertEqual(year, "2024")
        self.assertEqual(fallback, "Barron's Cove")

    def test_extract_simple_title(self):
        """
        Tests extraction for a standard filename with no fallback.
        """
        filename = "Dune (2021).mkv"
        name, year, fallback = renamer.get_movie_name_and_year(filename)
        self.assertEqual(name, "Dune")
        self.assertEqual(year, "2021")
        self.assertIsNone(fallback)

    def test_extract_no_year(self):
        """
        Tests extraction when the filename does not contain a year.
        """
        filename = "Pulp Fiction.mkv"
        name, year, fallback = renamer.get_movie_name_and_year(filename)
        self.assertEqual(name, "Pulp Fiction")
        self.assertIsNone(year)
        self.assertIsNone(fallback)

    def test_extract_with_release_tags(self):
        """
        Tests that release tags in brackets are correctly stripped.
        """
        filename = "Inception [1080p BluRay] (2010).mkv"
        name, year, fallback = renamer.get_movie_name_and_year(filename)
        self.assertEqual(name, "Inception")
        self.assertEqual(year, "2010")
        self.assertIsNone(fallback)

    def test_extract_with_multiple_parentheses(self):
        """
        Tests that the first non-year parenthesis is chosen as fallback.
        """
        filename = "Movie Title (Fallback Title) (Another Tag) (2022).mkv"
        name, year, fallback = renamer.get_movie_name_and_year(filename)
        self.assertEqual(name, "Movie Title")
        self.assertEqual(year, "2022")
        self.assertEqual(fallback, "Fallback Title")


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


class TestSourceParsing(unittest.TestCase):

    def test_parse_source_webrip_variants(self):
        for name in [
            'Movie (2020) [1080p (WEBRip)].mkv',
            'Movie (2020) [1080p (WEB-Rip)].mkv',
            'Movie (2020) [1080p (WebRip)].mkv',
            'Movie (2020) [1080p (WEB RIP)].mkv',
        ]:
            self.assertEqual(renamer.parse_source_from_filename(name), 'WEBRip')

    def test_parse_source_webdl_variants(self):
        for name in [
            'Movie (2020) [1080p (WEBDL)].mkv',
            'Movie (2020) [1080p (WEB-DL)].mkv',
            'Movie (2020) [1080p (webdl)].mkv',
        ]:
            self.assertEqual(renamer.parse_source_from_filename(name), 'WEB-DL')


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
            tmdb_data, media_info, 'BluRay', Path('/movies'), 'en', '.mkv'
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
            tmdb_data, media_info, 'UHD BDRemux', Path('/movies'), 'en', '.mkv'
        )
        # This test now reflects the intended behavior of having a hyphen separator.
        expected = Path('/movies/H/Harry Potter - Collection/Harry Potter and the Sorcerer\'s Stone (2001) [tt0241527]/Harry Potter and the Sorcerer\'s Stone (2001) [tt0241527] - [2160p (UHD BDRemux), HDR, x265, TrueHD].mkv')
        self.assertEqual(dest_path, expected)



if __name__ == '__main__':
    unittest.main()
