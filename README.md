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

## Checks

```powershell
uv run pytest
uv run pyright
```

The worker reads MP3 frames from SQLite and sends the same paced audio to each connected listener. Demo audio is stereo 44.1 kHz MPEG-1 Layer III at 128 kbps, with the bit reservoir disabled and no ID3 or Xing headers. Independent frames let a browser start decoding after joining mid-track.
