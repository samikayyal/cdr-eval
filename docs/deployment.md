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

On Ubuntu, install Caddy and curl with `apt`, create the service account, and install `uv` from Astral's official installer:

```sh
sudo apt-get update
sudo apt-get install -y caddy curl
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

## Activate a GitHub release

Use an exact commit SHA already pushed to GitHub. From an authenticated checkout, verify the commit and archive the committed files:

```sh
sha=$(git rev-parse HEAD)
gh api "repos/samikayyal/cdr-eval/commits/$sha" --jq .sha
git archive --format=tar.gz --output="quptan-radio-$sha.tar.gz" "$sha"
gcloud compute scp "quptan-radio-$sha.tar.gz" quptan-radio:~/ \
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

Inspect service logs with `sudo journalctl -u quptan-radio -u caddy -b`.
