import unittest
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

        result = renamer.get_tmdb_info('fake_api_key', 'Correct Movie (2023).mkv', 'en', debug=False)

        self.assertIsNotNone(result)
        self.assertEqual(result['title'], 'Correct Movie')
        # Verify it was called with the primary title and year
        self.assertEqual(mock_get.call_args_list[0].kwargs['params']['query'], 'Correct Movie')
        self.assertEqual(mock_get.call_args_list[0].kwargs['params']['primary_release_year'], '2023')

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

        result = renamer.get_tmdb_info('fake_api_key', 'Titulo Primario (Fallback Movie) (2023).mkv', 'en', debug=False)

        self.assertIsNotNone(result)
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

        result = renamer.get_tmdb_info('fake_api_key', 'Movie With Wrong Year (2000).mkv', 'en', debug=False)

        self.assertIsNotNone(result)
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

        result = renamer.get_tmdb_info('fake_api_key', 'NonExistent Movie (Also Fake) (2023).mkv', 'en', debug=False)
        self.assertIsNone(result)
        self.assertEqual(mock_get.call_count, 4)


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
