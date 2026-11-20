import unittest
from arcadia_studio.core.previews import ScreenScraper, Rawg, exact_game_name


class TrailerTitleMatching(unittest.TestCase):
    def test_title_equality(self):
        self.assertTrue(exact_game_name("God of War III", "GOD OF WAR  III™"))
        self.assertTrue(exact_game_name("Pokémon", "Pokemon"))
        self.assertTrue(exact_game_name("1942", "1942"))
        for other in ("1943", "1942 The Next Mission", "God of War IV", "God of War III Remastered"):
            self.assertFalse(exact_game_name("1942" if other.startswith('194') else "God of War III", other))

    def test_screenscraper_skips_first_wrong_game(self):
        src = ScreenScraper("key", "pass")
        src._get = lambda *a, **kw: {"response": {"jeux": [
            {"id": 1, "noms": [{"text": "1943"}], "medias": [{"type": "video", "url": "wrong.mp4"}]},
            {"id": 2, "noms": [{"text": "1942"}], "medias": [{"type": "video", "url": "correct.mp4"}]}
        ]}}
        self.assertEqual(src.find_video("1942", "PS3"), ("correct.mp4", "1942"))

    def test_screenscraper_no_exact_match(self):
        src = ScreenScraper("key", "pass")
        src._get = lambda *a, **kw: {"response": {"jeux": [
            {"id": 1, "noms": [{"text": "1943"}], "medias": [{"type": "video", "url": "wrong.mp4"}]}
        ]}}
        self.assertEqual(src.find_video("1942", "PS3"), (None, None))

    def test_rawg_never_queries_movies_for_wrong_game(self):
        src = Rawg("test-key")
        calls = []
        def mock(path, **kwargs):
            calls.append(path)
            if path == 'games':
                return {"results": [
                    {"id": 1, "name": "The Last of Us Part II"},
                    {"id": 2, "name": "The Last of Us"}
                ]}
            return {"results": [{"data": {"480": "correct.mp4"}}]}
        src._get = mock
        self.assertEqual(src.find_video("The Last of Us", "PS3"), ("correct.mp4", "The Last of Us"))
        self.assertEqual(calls, ["games", "games/2/movies"])

    def test_rawg_no_match(self):
        src = Rawg("test-key")
        def mock(path, **kwargs):
            if path != 'games': raise AssertionError("Never query a wrong game")
            return {"results": [{"id": 1, "name": "The Last of Us Part II"}]}
        src._get = mock
        self.assertEqual(src.find_video("The Last of Us", "PS3"), (None, None))


if __name__ == "__main__":
    unittest.main()
