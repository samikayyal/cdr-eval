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
2. In **Song Discovery**, choose one MP3, WAV, or M4A file up to 100 MB and select **Prepare recording**. The prepared song opens in **Playlist Management** under **Ready recordings**.
3. From the song's three-dot menu, select **Edit details** to correct its title or artist, or **Preview** to listen privately. During preview, the live connection stays open and live audio is muted on this device.
4. Select **Add to end** from the ready recording's three-dot menu. Once the song joins the playlist, its menu shows **Preview** and **Edit details**.

In **Song Discovery**, search by title or artist to see separate music library and YouTube results. YouTube shows up to 30 matches, with 10 per page. Select **View in Playlist Management** to jump to an existing library track. For a YouTube result, check the title, channel, source ID, and duration, then select **Preview** to hear that recording privately. Select **Import to library** to prepare it for the station, then add it from **Playlist Management**. Repeated imports open the existing library track. If YouTube retrieval fails, you can still upload a file.

Install [Deno](https://docs.deno.com/runtime/getting_started/installation/) on the station's PATH for YouTube retrieval.

The **العربية** / **English** switch remembers your choice in the browser.

## Checks

```powershell
uv run pytest
uv run pyright
```

The worker reads MP3 frames from SQLite and sends the same paced audio to each connected listener. Demo audio is stereo 44.1 kHz MPEG-1 Layer III at 128 kbps, with the bit reservoir disabled and no ID3 or Xing headers. Independent frames let a browser start decoding after joining mid-track.

## Hosted station

Open [Al Qubtan Restaurants Radio](https://quptan-radio.samikayyal.com) to listen over HTTPS. See the [deployment runbook](docs/deployment.md) for service, disk, release and verification procedures.
