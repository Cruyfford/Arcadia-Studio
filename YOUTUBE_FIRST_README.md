# ARCADIA — YouTube first, without Steam

The Windows Studio Videos page now checks YouTube *first*, through the
official YouTube Data API v3 search endpoint. Add your YouTube API key in the
new setting. Verified game-title trailers can be opened in the browser using
the new "Open on YouTube" menu item. Channel names may help ranking but
cannot prove video content or rights; no guarantee of perfect matches.

A valid YouTube result is only a watch link, NOT an MP4 download.
The YouTube API does not provide an MP4 file. YouTube's API developer terms
prohibit downloading and caching audiovisual content without approval.
YouTube results are cached as metadata for only 7 days, and at most 50 game
searches are attempted per batch to reduce quota use.

For PS3-playable MP4, the Studio continues to use ScreenScraper as the second
source and RAWG third, or a local video selected by the user with appropriate
rights. MP4 files created from these sources may be uploaded and linked to
ARCADIA using existing features, subject to permissions/licensing.

To create an API key: enable YouTube Data API v3 in Google Cloud Console and
create a credential with the proper API restrictions.
API docs: https://developers.google.com/youtube/data/reference/rest/v3/search/list
Policies: https://developers.google.com/youtube/terms/developer-policies
