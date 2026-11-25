# Al Qubtan Restaurants Radio assessment report

- [Listen to the hosted station](https://quptan-radio.samikayyal.com/)
- [Browse the public GitHub repository](https://github.com/samikayyal/cdr-eval)
- **Demo sign-in** · Username `admin` · Password `admin`

## Reviewer walkthrough

1. Open the station on two devices. Press **Play** on the first, then join from the second once music is audible. The second device should join the song already on air. Compare **On air** and **Up next** on both devices. Browsers buffer independently, so the audible transitions can differ slightly.
2. On the first device, open **Manager sign-in**, enter the demo credentials, and open **Song Discovery**. Search YouTube for `فيروز` or a song title. Select **Preview** on a result. The **Private preview** dialog opens with the selected title, a loading indicator, and **Close**. Audio starts when it is available. If the browser blocks automatic playback, use the preview player’s Play control. If retrieval or playback fails, select **Retry preview**.
3. The second device should keep playing the shared broadcast during private preview. Close the preview to hear the broadcast again on the manager’s device. Use **Import** to save that YouTube recording, then **Add to playlist** when it is ready. To use a recording already on the station, switch the search results to **Music library** and select **Add to playlist**, or select it from the song’s menu under **Playlist Management → Downloaded songs**.
4. Select **Choose position**, select the gap immediately after the on-air entry, and select **Add here**. Return to **Listen**. After both pages refresh their status, **Up next** should show your selection while the current song continues. Listen for your selection at the next transition on both devices.
5. Open the station in a fresh browser session and sign in again to see your entry in **Playlist Management → Station playlist**. Reviewers share the live station, and their edits persist. If you added the entry solely for this check, you can remove that occurrence from the playlist after listening. Use **Stop** on each device to leave the broadcast.

The music credits list the distinct recordings currently in the rotation with their titles and artists or credited source channels. [Music credits](https://quptan-radio.samikayyal.com/api/credits)

## دليل سريع بالعربية

1. افتح المحطة على جهازين واختر **العربية**. اضغط **تشغيل** على الجهاز الأول، ثم اضغط **تشغيل** على الثاني بعد سماع الموسيقى. يفترض أن ينضم الجهاز الثاني إلى الأغنية الجارية.
2. افتح **دخول المدير** واستخدم `admin` لاسم المستخدم وكلمة المرور. افتح **اكتشاف الأغاني** وابحث عن `فيروز` أو عن اسم أغنية. اختر **معاينة** لسماع النتيجة على جهازك فقط. يمكنك إغلاق المعاينة أثناء التحميل أو اختيار **إعادة محاولة المعاينة** عند تعذّر التشغيل.
3. اختر **استيراد** لحفظ نتيجة يوتيوب، ثم اختر **إضافة إلى قائمة التشغيل** بعد تجهيزها. يمكنك أيضًا اختيار أغنية جاهزة من **المكتبة الموسيقية**.
4. اختر **اختيار موضع** وحدّد الفراغ بعد الأغنية المعروضة **على الهواء**، ثم اضغط **إضافة هنا**. ارجع إلى **استمع** وتحقّق من ظهور الأغنية تحت **التالي**. عند انتهاء الأغنية الحالية، يفترض أن تسمع اختيارك على الجهازين.

[فتح محطة إذاعة مطاعم القبطان](https://quptan-radio.samikayyal.com/)

## Architecture and branch connections

```text
Branch browsers ─── HTTPS MP3 ───┐
                                 ├── Caddy ── FastAPI ── SQLite on persistent disk
Manager browser ─── HTTPS UI ────┘               ├── Shared broadcast
                                                 └── yt-dlp and FFmpeg
```

Each branch needs a browser, an internet connection, and an audio output. Cloudflare DNS points the station hostname to a Google Compute Engine VM. Caddy serves HTTPS and forwards requests to one FastAPI worker managed by systemd. The worker serves the HTML, CSS, and JavaScript interface and owns the shared broadcast. SQLite on a separate persistent disk stores prepared audio, library metadata, playlist order, and the saved restart position.

The first live audio connection starts the broadcast. The worker reads the prepared MP3 at playback speed and distributes the same audio to all connected branches. A joining branch receives the stream in progress and buffers it locally. The worker stops producing audio after the last listener disconnects. [Broadcast design](adr/0001-central-live-broadcast.md) · [Deployment runbook](deployment.md)

## Central operations and recovery

- **Manage recordings.** In **Song Discovery**, search the music library or YouTube, preview privately, import the exact selected result, or upload MP3, WAV, and M4A files. FFmpeg validates and normalizes recordings for playback. A private preview leaves the live connection open and mutes it locally while the preview plays.
- **Program the rotation.** A library track can have several independent playlist entries. Managers can append a recording, choose a gap, drag an entry, or remove an occurrence with an Undo option. Ordinary insertions and moves let the current recording finish and update the shared **Up next** display. If a requested gap changes before the edit is saved, the manager gets the refreshed order and can choose again.
- **Recover playback.** The browser retries a dropped live connection until it reconnects or the listener selects **Stop**. The server skips unreadable entries and reports an unavailable station if the whole rotation fails. After idle time or a service restart, the next listener starts the recording at the saved playlist position. A backup to private Google Cloud Storage can recover the library, rotation, and restart position. A separate restore and a hosted VM reboot were exercised. [Recovery drill](deployment.md#recovery-drill-observations)

## Challenges, AI assistance, and verification

| Challenge | Decision and verification |
| --- | --- |
| A branch joining late must hear the same program. | Use one worker to distribute a paced continuous stream. Hosted browser and decoder runs exercised independent joins and departures. A manager’s edit changed **Up next**, and the new entry went on air at the next transition. |
| YouTube retrieval from the VM and FFmpeg preparation made first previews slow. | Use yt-dlp’s downloader to stage the selected source and stream it as it arrives. Run loudness normalization and MP3 encoding on explicit import. A hosted test verified that the imported recording matched the source fingerprint and a repeat import reused the library track. |
| A network cut affected one branch while others still needed audio. | Keep each connection independent and retry playback in the browser. In the network-cut drill, the affected physical device fell silent and recovered without another tap, while a separate decoder continued. Hosted Chrome also recovered from four buffering waits on the same connection. |

I used OpenAI Codex to implement and review GitHub issues, write checks, and investigate failures. I checked its work with Playwright and Chromium for browser behavior, Pytest and Pyright for the Python code, and FFmpeg and VM counters for the hosted audio and capacity checks. These prompts came from the project conversations.

| Prompt | How I checked the result |
| --- | --- |
| “And add pagination with 10 results per page” | A signed-in browser searched for `فيروز` and walked three pages of YouTube results. It showed distinct results and kept the selected video tied to Preview and Import. |
| “Why does preview take so long?” | Compared the original path that waited for FFmpeg with progressive preview on the hosted VM. A controlled Chromium run held back source bytes and verified that playback could begin before the complete recording arrived. |
| “What will you do for issue 13, dont do anything just discuss” | Used the discussion to agree on the visible preview flow. When implementation was requested, exercised a delayed first preview, failure and retry, and Close in English and Arabic at desktop and phone widths. |

[YouTube implementation evidence](https://github.com/samikayyal/cdr-eval/pull/17) · [Hosted verification record](hosted-verification.md)

## Scalability

The test used one Compute Engine `e2-micro` with two shared guest vCPUs and 1 GiB of configured RAM. Each listener received a stereo 128 kbps MP3 stream. All ten independent HTTPS decoders stayed connected and advanced while a manager uploaded and prepared an 86.4 MB WAV. The decoders reported zero errors and zero playback retries.

| Measured load | CPU mean / peak | Used RAM mean / peak | VM outbound mean / peak |
| --- | ---: | ---: | ---: |
| Ten listeners | 1.41% / 2.48% | 373 / 373 MiB | 1.35 / 1.50 Mbps |
| Ten listeners with upload and preparation | 21.63% / 71.13% | 415 / 757 MiB | 1.45 / 1.67 Mbps |

Encoded audio alone would require about **3.84 Mbps for 30 listeners** or **12.8 Mbps for 100 listeners**, before delivery overhead. Ten concurrent listeners were tested. Capacity at larger counts is unmeasured. Preparation drove the largest observed CPU and memory peaks. Separating preparation would address that load if it grows. A delivery relay would address origin egress if future listener tests identify it as the bottleneck. [Measurement method and results](hosted-verification.md#capacity)

## Things we could improve

**Playback during branch Wi-Fi interruptions.** The live player uses the browser’s normal MP3 buffer. When the connection drops, any buffered audio can play until it runs out, and the branch then waits for reconnection. The interrupted device in the drill reported an immediate audible stop and automatic recovery. Testing a larger buffer on representative branch networks would show how much interruption it can absorb and how much it delays hearing central playlist changes. [Network interruption evidence](hosted-verification.md#continuity)
