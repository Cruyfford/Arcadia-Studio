# ARCADIA — strictly verified game videos (no Steam)

Sources: ScreenScraper (same requested console + exact title), then RAWG (exact title only). Steam has been removed from the video-source module.

- On the Windows Studio "Videos" page choose "fetch missing videos"; verified results now create both silent .avp previews and MP4 H.264/AAC with sound for PS3 playback.
- No trailer is selected just because it is the first search result, begins with the same text, or differs by a sequel number / edition.
- PS3's RAWG fallback only accepts an exact normalized catalogue title; a separate cache folder stops previously mismatched trailers from being reused.
- For the PS3 to play the new MP4 from the store, upload the generated trailers from the Studio to your configured hosting and use "link store." Without a RAWG API key and without uploaded trailers, PS3 cannot fetch a video.
- YouTube is not a direct playable MP4 source via its Data API. For content you own or have permission to redistribute, download/obtain the video through approved means and use "Add trailer with audio".
- Requests can fail if exact name metadata is absent; the application deliberately says no matching video instead of showing another game's video.

Unit tests: python -m unittest tests.test_video_matching -v
