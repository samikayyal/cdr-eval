# Run one shared broadcast on GCP while listeners are connected

One station process on GCP produces a continuous MP3 stream over HTTPS while at least one listener is connected to the live audio stream. The first listener starts playback. Additional listeners join the same broadcast in progress. Opening the public page, editing the playlist, and privately previewing a track leave an idle broadcast idle.

When the station detects that the final listener has disconnected, it stops producing audio. The restart position is the numeric index in the station playlist. On the next connection, the station reads the current playlist and starts the track at the saved position from the beginning. Edits during idle time can change which track occupies that position. Recovery after a process restart uses the same position-based rule.

The station checks the saved position against the playlist at the moment playback starts. If the position falls past the end, playback starts with the first entry. Shortening and then expanding the playlist while idle preserves the saved position until that check.

While idle, Listen shows Ready to play and a Starts with label giving the title and artist that the first listener would hear. The label uses the same position and wraparound rule as playback and refreshes after playlist edits. Viewing it leaves the saved position and idle state intact.

For the assessment, one small Compute Engine VM stays online to serve the public link and the manager interface. Its single station process gives simultaneous listeners one broadcast and supports long shifts without Cloud Run's per-request time limit. We accept idle VM uptime charges. Audio production still stops when the final listener disconnects.

On the VM, systemd starts the application at boot and restarts it after a crash. The service initially serves the player and management interface with the broadcast idle. The first live connection, including a reconnect from an existing player, starts playback.

The public station URL is `https://quptan-radio.samikayyal.com`. Manage the subdomain's DNS in the applicant's existing Cloudflare zone for `samikayyal.com`. During deployment, verify HTTPS, live playback, and manager sign-in on this hostname.

We will build the station in Python with FastAPI and one application worker. The interface uses HTML, CSS, and JavaScript. FFmpeg processes one upload at a time to validate it, normalize loudness, and prepare its MP3. The broadcast reads the prepared audio once at playback speed and sends it to all connected listeners.

SQLite stores one prepared MP3 per library track as a BLOB. Preview and broadcast read that same MP3. The station fingerprints the original upload for exact-file duplicate detection and discards the temporary source file after processing. Preparing a track again from its original recording requires a fresh upload. SQLite also stores library metadata, playlist entries and their order, and the saved restart position. The database resides on persistent disk, with a recoverable database backup outside the VM.

Song Discovery lets managers search the music library by artist or title, preview a track, upload a missing recording, and add a ready track to the playlist. Playlist Management shows the running order and lets managers rearrange or remove individual entries. Adding a recording to the library and scheduling it to play are separate actions.

Song Discovery uses yt-dlp with YouTube as its first online source. Search returns candidate video IDs, titles, channels, and durations when available. Online results appear separately from local matches. Preview plays the selected recording through private audio-only controls in Song Discovery. The server can temporarily stage audio that the source permits it to retrieve, keyed to the selected video ID. Adding that result to the library reuses the staged audio when available. Unused preview files are cleaned up. Only an explicit import saves a library track; the usual duplicate, size, disk-space, and preparation checks apply. If retrieval fails, the search results and file upload remain available.

Song Discovery reads the embedded title and artist when a file is uploaded and suggests the filename as the title if the title tag is missing. The manager can correct those values. Search results, playlist entries, and the public On air display all use the confirmed library title and artist.

Uploading an identical file opens its existing library track. Different files may share a title and artist. Each library track can appear in the playlist multiple times.

Song Discovery accepts MP3, WAV, and M4A uploads up to 100 MB per file and checks that the station retains enough free disk space for operation. Choose the reserve when sizing the disk. A manager may select multiple files. Each file has its own processing status and result, so a duplicate, an unreadable file, or insufficient space does not undo successful uploads. Ready tracks can be previewed or added while the remaining files are processed.

When the manager confirms Add, the server reads the current playlist and checks whether the requested placement still agrees with what the manager saw. The check and insertion occur in one transaction. On a conflict, the page shows the refreshed playlist, explains that the order changed, and lets the manager choose again. Add to end appends to the current end.

Each playlist entry has a Move to… action that uses the same gap picker as Choose position on phones and laptops. Moving the playing entry lets it finish and immediately updates Up next to its successor in the revised rotation. For example, moving the playing B to the end of A → B → C → D makes A next by wraparound.

Removing a playlist entry takes one tap and offers Undo. A removed on-air entry finishes playing. The last playable entry is protected. To delete a recording from the library, the manager removes all its playlist entries and explicitly confirms deletion. Song Discovery shows the number of entries using the track and links to Playlist Management. If the recording is still finishing on air, its audio file is retained until playback ends.

The Listen tab links to Music credits, which lists the title and artist for each demo song.

A private preview temporarily mutes live audio on the manager's device while preserving any existing live connection. Ending the preview restores the prior mute setting at the current live position. Starting a preview on its own leaves an idle broadcast idle.

The public station page has a Listen tab with a primary Play button and an admin sign-in. Listening is open to anyone with the link. Signing in reveals Playlist Management and Song Discovery beside Listen as tabs within the same page. Switching tabs preserves the listener's playback state. The public assessment deployment uses the shared demo username `admin` and password `admin`. Anyone who enters that pair can manage the live assessment station.

The public station name is Al Qubtan Restaurants Radio. While broadcasting, Listen shows On air and Up next with each track's title and artist. After a playlist edit, connected pages update Up next. The station status is shared by all viewers, including people who have opened the page without pressing Play.

Play joins the broadcast on the current device and Stop disconnects that listener. Stopping one device returns its button to Play. While another listener remains, the page still shows On air. When the station detects the final disconnection, open pages show Ready to play. After an unexpected connection loss, the affected player shows Reconnecting and retries until it reconnects or the listener presses Stop.

The player and manager controls default to English on first visit. Selecting Arabic switches the layout to right-to-left. A visible English/Arabic switch remembers the choice on that device. Track titles and artist names retain their supplied text.

## Verification plan

If the station cannot play a scheduled track, it advances to the next playable entry for every listener. Playlist Management keeps the failed entry in place and marks it Couldn't play. If a full rotation yields no playable audio, the station stops cycling, Listen shows Station temporarily unavailable, and Playlist Management identifies the failed tracks. When a manager adds a playable track to the playlist, players whose listeners requested playback recover automatically. Other visitors remain silent.

Verify both paths in a test database: make one recording unreadable and observe the transition on two devices, then make the entire rotation unplayable. Add a ready track to the playlist and check recovery on a player that requested playback and continued silence on a page that never started playback.

During playback, submit a batch containing new tracks, a duplicate, and an unreadable file. Check each result and verify that playback continues. Exercise the per-file size limit and low-space handling before deployment.

The player uses the browser's normal buffer for the continuous MP3 stream. During the connection-loss test, note whether the buffered audio runs out before playback reconnects.

A slow listener must not delay other branches. Disrupt one device's connection and verify that the other keeps playing and that the recovering device rejoins live. Separately, test ten simultaneous listeners while the manager uploads a track and report the observed behavior.

Record the VM size, stream bitrate, outbound bandwidth, CPU and memory use, and playback continuity during the load test. Use those observations to revise the provisional scalability section of the assessment report before submission.

Use SQLite's backup mechanism to capture a consistent database copy and store it in a private Google Cloud Storage bucket. Verify recovery by restoring into a separate test instance, playing a stored track, and checking the playlist order and saved restart position. Verify service recovery separately by rebooting the VM and checking that the first live connection starts the station at the expected position.

The first working milestone uses two demo tracks and two devices. Join the running broadcast late on the second device, listen through a track transition, disconnect and rejoin while the first continues, then disconnect both and verify restart from the saved playlist position.

Run an internal continuity test for approximately one hour. Keep one listener connected, join and leave from a second device, and edit the playlist during playback. Report observed playback behavior, interruptions, recovery, and any manual intervention.

## Assessment handoff

The hosted station is the committee's hands-on test environment. Each reviewer encounters the live library and playlist as the previous reviewer left them. Database backups serve operational recovery. The public GitHub repository accompanies the station for source inspection.

The applicant will choose the full-length songs for the demo. The English technical report opens with the public listening link, the public GitHub repository link, the demo credentials, and a hands-on walkthrough. Reviewers start on one device, join from a second, then use the manager controls to place a track next. They can inspect the new order immediately and hear the change on both devices when the current song ends. A short Arabic quick-start covers listening, signing in, finding a song, and placing it next. Omit timeframes from reviewer-facing materials.

The report explains the architecture and GCP deployment, central content management, observed test results, and challenges and their resolutions. Include actual AI prompts, the decisions they informed, and how the results were checked.
