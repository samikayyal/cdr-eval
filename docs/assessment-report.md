# Al Qubtan Restaurants Radio

I built a web radio station so Al Qubtan’s branches can listen to the same music and a manager can change the playlist for all of them from one place.

[Live station](https://quptan-radio.samikayyal.com/) · [GitHub repository](https://github.com/samikayyal/cdr-eval)

Demo account — Username `admin` · Password `admin`

## Features

- **Live listening.** A branch joins the song already playing. The player shows what’s on air and what’s next, and reconnects if the connection drops.
- **YouTube search.** Managers can search in English or Arabic, preview a result privately, and import the exact recording they heard.
- **Music library.** Managers can upload several MP3, WAV, or M4A files at once, edit track details, and look up saved recordings by title or artist.
- **One playlist for all branches.** Managers can add songs at a specific position, reorder the list, and undo a removal. Changes to upcoming songs take effect while the current song plays.
- **English and Arabic.** The listener and manager screens switch between both languages and remember the selection.

## Technical architecture

The live station runs on one Google Compute Engine `e2-micro` in Belgium (`europe-west1-b`). It has two shared vCPUs, 1 GiB of RAM, a 10 GiB balanced boot disk, and a 20 GiB standard data disk. It runs Ubuntu 24.04 LTS. Cloudflare provides DNS, Caddy handles HTTPS, and Uvicorn runs a single FastAPI worker. The browser interface uses HTML, CSS, and JavaScript.

The worker reads each prepared MP3 once at playback speed and sends the same stream to every listener. Each browser buffers its own audio, so one branch can hear a transition slightly later than another. The last disconnect stops the stream, and the next listener starts at the saved playlist position.

SQLite on a persistent disk stores the prepared audio, track details, and playlist. systemd restarts the app after a crash. The database can be restored from a backup in private Google Cloud Storage. I tested the restore on a separate instance and checked the playlist order and saved playback position. [Deployment and recovery](deployment.md)

For the same setup in South Carolina (`us-east1`), 730 hours at list rates comes to about **$11.56 per month** ($6.11 for the VM, $1.80 for both disks, and $3.65 for the public IPv4 address). With available Google Cloud Free Tier allowances, the VM and standard data disk are covered and the base cost drops to about **$4.65 per month**. Both estimates exclude internet data transfer and backup storage. [VM pricing](https://cloud.google.com/products/compute/pricing/general-purpose) · [Disk pricing](https://cloud.google.com/compute/disks-image-pricing) · [IP pricing](https://cloud.google.com/vpc/network-pricing) · [Free Tier](https://docs.cloud.google.com/free/docs/free-cloud-features#compute)

In a one-hour test on the hosted station, I kept an automated Chrome player running while other listeners joined and left and I changed the next song. The main player paused to buffer four times. The longest pause was about two seconds, and playback resumed on the same connection every time. [Continuity test](hosted-verification.md#continuity)

In the hosted load test, all ten streams kept decoding while the VM prepared an 86.4 MB WAV. CPU peaked at 71%, RAM use peaked at 757 MiB, and outbound traffic averaged 1.45 Mbps. At 128 kbps per listener, 30 listeners would need about 3.84 Mbps of audio traffic and 100 would need 12.8 Mbps before network overhead. [Capacity measurements](hosted-verification.md#capacity)

## AI-assisted development

I used OpenAI Codex to build, debug, and review the station. For the interface, I used ChatGPT image generation to explore visual ideas and make UI mockups. In Codex, I used a mix of skills I wrote myself and open-source skills from Matt Pocock to work through tickets and review changes.

Before settling on how a feature should work, I used the `grill-me` skill to have Codex ask me questions about the design and talk through implementation choices. I used GPT-6.2 Astra to write all the specs and tickets, GPT-6.2 Sol to implement them, and GPT-6.2 Astra again to review the changes.

I had Codex use its computer-use skill to test the station in Google Chrome and the in-app browser. I checked the listener and manager screens in English and Arabic at desktop and phone sizes. I also tested slow and failed YouTube previews to check the loading message and retry. For the shared broadcast, I checked that a playlist edit showed up in two listening sessions and was still there after a fresh sign-in.

## Open-source software used

- **yt-dlp and Deno** power YouTube search and audio retrieval. Deno runs the JavaScript needed by yt-dlp for YouTube.
- **FFmpeg and ffprobe** check recordings, normalize loudness, and encode the MP3s used for broadcast.
- **FastAPI, Uvicorn, Caddy, and SQLite** serve the app, handle HTTPS, and store the station’s data. The app runs on Python, with uv managing dependencies and the environment.

## Challenges and how I solved them

- **Changing the playlist while branches are listening.** I made edits to upcoming songs take effect without restarting the stream. In a hosted test, two browser sessions showed the new **Up next** and played through the change. The edit was still there after a fresh sign-in. [Hosted playlist test](https://github.com/samikayyal/cdr-eval/pull/28)
- **Slow YouTube previews.** The first version waited for FFmpeg to finish preparing the track. I moved that work to import and let the browser play the preview as yt-dlp downloads it. The preview now opens right away, shows when it’s loading, and offers a retry if it fails. I checked this with real audio in English and Arabic at desktop and phone widths. [Preview tests](https://github.com/samikayyal/cdr-eval/pull/28)
- **A branch losing Wi-Fi.** I made the player retry on its own. In the network test, one device went quiet when its connection dropped and resumed when Wi-Fi came back. The other stream kept playing. [Wi-Fi test](hosted-verification.md#continuity)

## Future improvements

- **Show branch playback status.** Give each branch player a name and show whether it’s playing and when it last checked in. The manager could see which branch lost the stream and whether other branches were affected.
- **Schedule playlists by time of day.** Let managers set different playlists for lunch and dinner. The station would switch to the scheduled playlist at the next song change.
