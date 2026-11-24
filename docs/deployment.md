# Deploy Al Qubtan Restaurants Radio

The public station is at [quptan-radio.samikayyal.com](https://quptan-radio.samikayyal.com). Cloudflare serves the DNS record. Caddy on the VM obtains and renews the public TLS certificate and forwards requests to the single FastAPI worker on `127.0.0.1:8000`. The worker serves the Listen page while the broadcast is idle. Opening `/live.mp3` starts or joins audio production.

## GCP resources

| Resource | Configuration |
| --- | --- |
| Project | `quptan-radio-samikayyal` |
| VM | `quptan-radio`, `e2-micro`, Ubuntu 24.04 LTS, `europe-west1-b` |
| Public IP | `quptan-radio-ip`, reserved in `europe-west1` |
| Data disk | `quptan-radio-data`, 20 GB standard Persistent Disk, auto-delete disabled |
| Network | `quptan-radio`, public TCP 80 and 443, SSH restricted to IAP |

In the Cloudflare zone for `samikayyal.com`, set the `quptan-radio` A record to the reserved IPv4 address with **DNS only**. Public TCP 80 handles HTTP redirects and ACME validation. TCP 443 serves the player and stream.

Connect using the Google Cloud CLI:

```sh
gcloud compute ssh quptan-radio --project=quptan-radio-samikayyal \
  --zone=europe-west1-b --tunnel-through-iap
```

## Prepare the VM

On Ubuntu, install Caddy, curl, and FFmpeg with `apt`, create the service account, and install `uv` from Astral's official installer. The station uses `ffmpeg` and `ffprobe` to prepare uploaded recordings.

```sh
sudo apt-get update
sudo apt-get install -y caddy curl ffmpeg
sudo useradd --system --user-group --home-dir /var/lib/quptan-radio \
  --shell /usr/sbin/nologin quptan-radio
sudo install -d /opt/quptan-radio/bin /opt/quptan-radio/releases /var/lib/quptan-radio
curl -fsSLo /tmp/install-quptan-uv.sh https://astral.sh/uv/install.sh
sudo env UV_UNMANAGED_INSTALL=/opt/quptan-radio/bin sh /tmp/install-quptan-uv.sh
```

Check `/dev/disk/by-id/google-quptan-radio-data` with `sudo lsblk -f`. For a new, blank data disk, initialize it once with `sudo mkfs.ext4 /dev/disk/by-id/google-quptan-radio-data`. For an existing disk, reuse its filesystem. Obtain its UUID with `sudo blkid` and add this entry to `/etc/fstab` with the actual UUID:

```fstab
UUID=<data-disk-uuid> /var/lib/quptan-radio ext4 defaults,nofail 0 2
```

Run `sudo mount /var/lib/quptan-radio` and `sudo chown quptan-radio:quptan-radio /var/lib/quptan-radio`. The service requires the mount before opening `station.sqlite3`. Caddy stores its certificates on the VM boot disk.

On the 20 GB data disk, preparation keeps a 256 MiB free-space reserve and requires another 500 MB of headroom before starting. This covers a maximum-size source and prepared MP3, SQLite WAL and checkpoint writes, and a concurrent YouTube preview download.

## Activate a GitHub release

On an existing VM, install FFmpeg with `sudo apt-get install -y ffmpeg` before activating a release that enables recording uploads.

For YouTube discovery, install a [Deno release](https://github.com/denoland/deno/releases) in `/opt/quptan-radio/bin/deno`. Verify the release archive against its published SHA-256 digest before installing the executable. The systemd service includes that directory on its path. The locked yt-dlp dependency includes the YouTube JavaScript challenge scripts.

Use an exact commit SHA already pushed to GitHub. From an authenticated checkout, verify the commit and archive the committed files:

```sh
set -e
sha=$(git rev-parse HEAD)
test "$(gh api "repos/samikayyal/cdr-eval/commits/$sha" --jq .sha)" = "$sha"
git archive --format=tar.gz --output="quptan-radio-$sha.tar.gz" "$sha"
gcloud compute scp "quptan-radio-$sha.tar.gz" quptan-radio:. \
  --project=quptan-radio-samikayyal --zone=europe-west1-b --tunnel-through-iap
```

On the VM, set `sha` to that verified commit and run:

```sh
set -e
release="/opt/quptan-radio/releases/$sha"
sudo install -d "$release"
sudo tar -xzf "$HOME/quptan-radio-$sha.tar.gz" -C "$release"
sudo /opt/quptan-radio/bin/uv sync --project "$release" --locked --no-dev \
  --python /usr/bin/python3
sudo caddy validate --config "$release/deploy/Caddyfile" --adapter caddyfile
sudo ln -sfnT "$release" /opt/quptan-radio/current
sudo install -m 0644 "$release/deploy/quptan-radio.service" /etc/systemd/system/
sudo install -m 0644 "$release/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now quptan-radio caddy
sudo systemctl restart quptan-radio
sudo systemctl reload caddy
```

Releases share the SQLite file on the mounted data disk. To roll back, use the previous release directory and reinstall its service and Caddy config with the activation commands above.

## Verify the deployment

1. From another machine, open the public HTTPS address and check that the browser accepts the certificate, shows **Ready to play**, and stays silent. `curl -fsS https://quptan-radio.samikayyal.com/api/broadcast` should return `{"on_air":null,"up_next":null}` with no listeners.
2. On the VM, confirm `systemctl is-enabled quptan-radio caddy` and `findmnt /var/lib/quptan-radio`. During a deployment check, run `sudo systemctl kill --signal=SIGKILL quptan-radio` and confirm that systemd starts a new worker and the HTTPS page returns.
3. Run `sudo systemctl reboot`, reconnect over IAP, then check `systemctl is-active quptan-radio caddy` and `findmnt /var/lib/quptan-radio` again.
4. After the reboot, open the station from two devices. Press **Play** on the first, then join from the second and check that both hear the shared broadcast through a track transition. Stop each device and check that the station returns to **Ready to play** after the last listener leaves.
5. Sign in and search for `فيروز` in Song Discovery. Check that the library and YouTube results appear in separate sections and that YouTube results show the title, channel, and duration when available. Preview a recording available for the test and check that the player can start before the source download finishes. Import the same selection and verify that it uses the staged source to prepare the library MP3. Check that a second import opens the same library track. Add it to the station playlist in the test database and verify both listeners reach it. Test an unavailable recording and confirm that search results and file upload remain usable.
6. With two listeners connected to a test station, select a batch with a new recording, an identical copy, a corrupt recording, and a file over 100 MB. Prepare the batch and check each file's result. Open a ready track in Playlist Management while the batch is still running. Check that the duplicate opens the same track and both listeners keep playing.

Inspect service logs with `sudo journalctl -u quptan-radio -u caddy -b`.

## Back up the station

The operator runs backups on demand using the existing IAP and Google Cloud CLI access. Keep the snapshots in `gs://quptan-radio-samikayyal-backups` in `europe-west1`. The bucket uses uniform bucket-level access and enforced public access prevention. Use a principal with permission to create and read objects. The VM does not need Cloud Storage credentials.

On the VM, check that the station data disk is mounted and has space for another database-sized file. The station can keep serving listeners and accepting manager edits during the backup. SQLite's online backup API copies one committed snapshot, including the audio BLOBs, metadata, playlist entries and saved restart position. Take a new backup after edits you want to protect.

```sh
set -e
mountpoint -q /var/lib/quptan-radio
stamp=$(date -u +%Y%m%dT%H%M%SZ)
staging=$(sudo mktemp -d /var/lib/quptan-radio/.backup.XXXXXXXX)
snapshot="$staging/station-$stamp.sqlite3"
sudo /opt/quptan-radio/bin/uv run --no-project --offline --python /usr/bin/python3 \
  /opt/quptan-radio/current/radio/backup.py /var/lib/quptan-radio/station.sqlite3 "$snapshot"
sudo chown "$(id -u):$(id -g)" "$snapshot" "$staging"
sha256sum "$snapshot"
```

On the authenticated operator workstation, use a private directory with room for the snapshot. Set `snapshot` to the VM path printed by `sha256sum`. Compare the downloaded file's SHA-256 to the VM output, then upload using a new object name and the generation precondition.

```sh
set -e
umask 077
name=$(basename "$snapshot")
gcloud compute scp "quptan-radio:$snapshot" "./$name" \
  --project=quptan-radio-samikayyal --zone=europe-west1-b --tunnel-through-iap
sha256sum "$name"
# After checking the hashes match:
object="gs://quptan-radio-samikayyal-backups/$name"
gcloud storage cp --if-generation-match=0 "$name" "$object"
gcloud storage objects describe "$object"
```

After confirming the upload, run `rm -- "$snapshot"` and `sudo rmdir -- "$staging"` on the VM, then remove the workstation copy. Each successful upload is a dated recovery point. Normal starts and reboots open the existing database on the data disk, which keeps edits made since the last backup.

## Restore into a test instance

Use the code revision that created the backup or a compatible newer release. In the project checkout on a trusted workstation or test server, use a fresh private directory and bind the test server to loopback. Download the exact object you intend to restore. Use its generation in the URI when pinning a recovery point.

```sh
set -e
umask 077
restore_dir=$(mktemp -d)
gcloud storage cp "$object" "$restore_dir/download.sqlite3"
uv run --locked -m radio.backup "$restore_dir/download.sqlite3" "$restore_dir/station.sqlite3"
RADIO_DATABASE="$restore_dir/station.sqlite3" uv run --locked uvicorn \
  radio.web:create_app --factory --host 127.0.0.1 --port 8011 --workers 1
```

The copy checks SQLite integrity and foreign-key references and requires a new destination. Open `http://127.0.0.1:8011/`. Check **Starts with** against the expected recording. Sign in and check the library metadata, previews, and the full playlist order. Press **Play** and confirm that **On air** and the audio match the saved position. Press **Stop**, stop the test service, and remove the temporary restore files.

Run the reboot check separately on the hosted VM. Before rebooting, wait until the broadcast is idle and record **Starts with**, the boot ID from `/proc/sys/kernel/random/boot_id`, and the database inode from `stat -c '%i' /var/lib/quptan-radio/station.sqlite3`. After reboot, confirm the boot ID changed, the data disk is mounted with the same database inode, and both services are active. Compare **Starts with**, then verify that the first Play starts that recording and that the station returns to idle after Stop.

### Recovery drill observations

- **Cloud backup.** On 2026-11-24, an online backup captured both recordings after playback reached playlist slot 2. The VM snapshot and the download of object generation `1795544971789272` had the same SHA-256. An anonymous download from the backup bucket returned HTTP 403.
- **Separate restore.** A loopback instance restored from that Cloud Storage object had the same library metadata, audio BLOB hashes, and playlist order (**Harbor Lights**, then **Evening Tide**). Chrome showed **Starts with Evening Tide**, played **Evening Tide** with the audio element unmuted, and returned to idle after Stop. The first 16,384 stream bytes matched the stored recording and decoded to non-silent audio.
- **Hosted reboot.** The VM boot ID changed, the persistent data disk remounted with the same database inode, and systemd brought up the station and Caddy. The library, playlist order, and audio hashes still matched. The first live connection streamed **Evening Tide** from its beginning, then the station returned to idle at that position.
