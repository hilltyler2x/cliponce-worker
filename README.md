# ClipOnce cloud worker

Runs ClipOnce's autopilot, live auto-clip and posting on GitHub Actions, so no PC has to stay on.

| Job | When | What it does |
| --- | --- | --- |
| Daily autopilot | 9 AM New York | Finds the GTA 6 Twitch moments live audiences reacted to most, cuts them from the streamer's own VOD at 1080x1920 with captions and credit, hosts them as release files and queues them. (YouTube blocks GitHub's servers, so YouTube clipping stays on the PC with `autopilot.py`.) |
| Post queued clips | Every 2 hours, 10 AM to 10 PM | Posts the next clip to YouTube Shorts, Instagram and Threads, within each platform's daily cap. Over-cap clips wait for tomorrow. |
| Live auto-clip | Every 15 minutes | If a streamer in `config.json`, or one of the top live GTA 6 Twitch streams, is live, records the stream and reads chat. A chat spike (laugh, hype or sad emotes, "clip it") becomes a captioned clip. |

## Settings
Edit `config.json`: searches, clips per day, daily caps, and `streamers`, for example
`{"url": "https://www.twitch.tv/somestreamer", "post": true}`.

## Secrets (Settings, Secrets and variables, Actions)
Platforms without secrets are skipped.
- `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`: free app keys from dev.twitch.tv/console/apps (finds GTA 6 clips and live streams).
- `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`: YouTube upload sign-in.
- `IG_USER_ID`, `IG_TOKEN`, optional `IG_GRAPH_HOST` (`graph.instagram.com` for Instagram Login tokens).
- `THREADS_USER_ID`, `THREADS_TOKEN`.
- `YT_COOKIES`: optional, a cookies.txt export, only if YouTube blocks downloads from GitHub.

Originals only: clip channels, compilations, reactions, reuploads and videos that credit another creator are skipped, so we never re-clip someone else's posted clip. Clipping the same moment from the original video or live is fine.

Safety: titles mentioning leaks, piracy or cracks are skipped, Rockstar's own channel is skipped, and every clip credits its creator on screen and in the caption.
