PS3 trailer playback fixes: v3
- YouTube remains metadata search only, as the Data API has no download/MP4 endpoint.
- RAWG response parser now tolerates normal whitespace in JSON and checks complete top-level game objects, preserving game name + ID from the same result.
- A movie is accepted only if its game name matches exactly and its URL uses HTTPS.
- Every fetched video is verified by the built-in MP4/H.264 parser, instead of the old ftyp-only header check. Unsupported videos are not cached.
- Failure message says explicitly when YouTube has only a link or when no usable MP4 exists.
- For full videos with audio inside PS3, Arcadia Studio must upload properly transcoded, licensed MP4 to the configured VIDEOSTORE URL. YouTube playback remains unsupported in this native player.
- This is a source-code/build validation change, not confirmation on physical PS3.
