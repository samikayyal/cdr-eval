# Al Qubtan Restaurants Radio

## Run the station

```powershell
uv run uvicorn radio.web:create_app --factory --workers 1 --timeout-graceful-shutdown 5
```

Open http://127.0.0.1:8000. The application creates `data/station.sqlite3` on first run and seeds the station playlist with two short original demos, **Harbor Lights** and **Evening Tide**. Set `RADIO_DATABASE` to use a different SQLite path. Keep one worker because it owns the shared broadcast.

## Listen on two devices

1. To listen from the same network, add `--host 0.0.0.0` to the launch command and open port 8000 on the station computer's LAN address from each device.
2. Verify both pages show **Ready to play** and stay silent. Press **Play** on one device, wait a few seconds, and press **Play** on the second.
3. Listen for the second device joining **Harbor Lights** in progress, then for both devices to move to **Evening Tide** and back to **Harbor Lights**. **On air** and **Up next** show the shared station schedule. Each browser buffers audio locally, so its audible transition can lag behind the schedule.
4. Press **Stop** on one device and check that the other keeps playing. Press **Stop** on the final device and check that both pages return to **Ready to play**.

## Prepare a recording and add it to the playlist

Install FFmpeg and ensure both `ffmpeg` and `ffprobe` are on the station's PATH.

1. Open **Manager sign-in** and enter `admin` for both fields.
2. In **Song Discovery**, open **Batch upload**, choose MP3, WAV, or M4A files up to 100 MB each, and select **Prepare recordings**. Prepared songs appear in **Playlist Management** under **Downloaded songs**.
3. From the song's three-dot menu, select **Edit details** to correct its title or artist, or **Preview** to listen privately. During preview, the live connection stays open and live audio is muted on this device.
4. Select **Add to playlist**, then **Add to end** or **Choose position**. The position picker shows the on-air entry and gaps before, between, and after the entries. Select a gap and confirm with **Add here**. If another manager changes that gap, choose again from the refreshed order.

**Playlist Management** opens on **Station playlist**. Use **Downloaded songs** to see saved recordings, including songs already scheduled. You can add the same recording again from either tab. Drag a playlist entry by its three-line handle. The surrounding entries slide apart to show where it will land. Release to save the new order. You can also activate the handle to choose a gap and select **Move here**. If the entry is on air, it finishes playing and **Up next** switches to its successor in the new order. Moving it to the end makes the first entry play next.

To permanently remove a recording, select **Delete song** from its three-dot menu in **Downloaded songs**, then select **Delete permanently** in the confirmation dialog. Remove its playlist entries first.

In **Song Discovery**, search by title or artist to open the **YouTube** results tab. Switch to **Music library** to see local matches, or submit an empty search to browse the library. Use **Preview** or **Add to playlist** directly on a music library result, or open its three-dot menu to find it in Playlist Management. YouTube shows up to 30 matches, with 10 per page. For a YouTube result, check the title, channel, and duration when available, then select **Preview** to hear the audio privately as it downloads. Select **Import** on each song you want to save. The **YouTube import queue** prepares songs one at a time while you browse. Use **Add to playlist** when a song is ready or **Retry import** if it fails. Imported songs also appear under **Downloaded songs**.

Install [Deno](https://docs.deno.com/runtime/getting_started/installation/) on the station's PATH for YouTube retrieval.

The **العربية** / **English** switch remembers your choice in the browser.

## Checks

```powershell
uv run pytest
uv run pyright
```

The worker reads MP3 frames from SQLite and sends the same paced audio to each connected listener. Demo audio is stereo 44.1 kHz MPEG-1 Layer III at 128 kbps, with the bit reservoir disabled and no ID3 or Xing headers. Independent frames let a browser start decoding after joining mid-track.

## Hosted station

Open [Al Qubtan Restaurants Radio](https://quptan-radio.samikayyal.com) to listen over HTTPS. The [assessment report](docs/assessment-report.md) covers the station’s features, architecture, and test results. See the [deployment runbook](docs/deployment.md) for service, disk, release and verification procedures.
