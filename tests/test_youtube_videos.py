import unittest
from arcadia_studio.core.youtube_videos import YouTube, matches_game

class YouTubeTests(unittest.TestCase):
    def test_matches_correct_game(self):
        self.assertTrue(matches_game("God of War III", "God of War III Official PS3 Trailer", "PS3"))
        self.assertTrue(matches_game("The Last of Us", "[PS3] The Last of Us Gameplay Trailer", "PS3"))
    def test_rejects_wrong_game_or_edition(self):
        self.assertFalse(matches_game("God of War III", "God of War IV Official Trailer PS3"))
        self.assertFalse(matches_game("The Last of Us", "The Last of Us Part II Official Trailer PS3"))
        self.assertFalse(matches_game("God of War III", "God of War III Remastered PS4 Trailer"))
        self.assertFalse(matches_game("God of War III", "God of War III Reaction Trailer"))
        self.assertFalse(matches_game("God of War III", "God of War III Review"))
    def test_prefers_known_channel(self):
        yt=YouTube("fake-test-key")
        yt._search=lambda **params: {'items':[
            {'id':{'videoId':'abcdefG1234'}, 'snippet':{'title':'God of War III Trailer PS3','channelTitle':'Unverified'}},
            {'id':{'videoId':'ZYXwVUT9876'}, 'snippet':{'title':'God of War III Official Trailer PS3','channelTitle':'PlayStation'}}]}
        found=yt.find_video('God of War III','PS3')
        self.assertEqual(found['id'], 'ZYXwVUT9876')
    def test_no_wrong_game(self):
        yt=YouTube("fake-test-key")
        yt._search=lambda **params: {'items':[
            {'id':{'videoId':'abcdefG1234'}, 'snippet':{'title':'God of War IV Official Trailer PS3','channelTitle':'PlayStation'}}]}
        self.assertIsNone(yt.find_video('God of War III','PS3'))
