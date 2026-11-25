# Al Qubtan Restaurants Radio

[Open the station](https://quptan-radio.samikayyal.com/) · [View the source on GitHub](https://github.com/samikayyal/cdr-eval)

Use `admin` for both the username and password.

## Try it on two devices

1. Open the station on a phone and a computer. Press **Play** on one, wait until you hear music, then press **Play** on the other. Both devices should be on the same song. Check **On air** and **Up next** on both screens.
2. On the computer, open **Manager sign-in**, then go to **Song Discovery**. Search for `فيروز` and click **Preview** on a YouTube result. The song title appears while the preview loads. If you see **Press Play to hear the preview**, use the preview’s Play button. If the preview fails, click **Retry preview**. The phone should keep playing the station. Click **Close** to go back to the live audio on the computer.
3. To try a playlist change, pick a song from **Playlist Management → Downloaded songs**. Open its three-dot menu and click **Add to playlist**. Click **Choose position**, pick the gap just after the song marked **On air**, and click **Add here**.
4. Go back to **Listen**. Your song should appear under **Up next** on both screens while the current song keeps playing. Listen for the change. The devices may switch at slightly different moments because each browser buffers its own audio.
5. Open a private browser window and sign in again. Your entry should still be in **Playlist Management → Station playlist**. The demo is shared, so feel free to remove the entry you added after the test. Press **Stop** on both devices when you’re done.

For the titles and credits of the songs in the playlist, open the [music credits](https://quptan-radio.samikayyal.com/api/credits).

## دليل سريع بالعربية

1. افتح المحطة على جهازين واختر **العربية**. اضغط **تشغيل** على الجهاز الأول. بعد أن تسمع الموسيقى، اضغط **تشغيل** على الجهاز الثاني. ستسمع الأغنية نفسها على الجهازين.
2. افتح **دخول المدير** واكتب `admin` في خانتي اسم المستخدم وكلمة المرور. في **اكتشاف الأغاني**، ابحث عن `فيروز` واضغط **معاينة** على إحدى النتائج. ستسمع المعاينة على جهازك فقط.
3. لإضافة أغنية جاهزة، افتح **إدارة قائمة التشغيل** ثم **الأغاني المحمّلة**. من قائمة النقاط الثلاث بجانب الأغنية، اختر **إضافة إلى قائمة التشغيل** ثم **اختيار موضع**.
4. اختر الفراغ بعد الأغنية التي تحمل علامة **على الهواء** واضغط **إضافة هنا**. ارجع إلى **استمع**. ستظهر أغنيتك تحت **التالي** على الجهازين، وستسمعها بعد انتهاء الأغنية الحالية.

[افتح إذاعة مطاعم القبطان](https://quptan-radio.samikayyal.com/)

## How the station works

A branch only needs a browser, internet access, and speakers. Cloudflare DNS points the station address to a Google Compute Engine VM. Caddy handles HTTPS and passes requests to a single FastAPI worker. The same worker serves the web page and runs the broadcast. A SQLite database on a separate persistent disk holds the music, playlist, and saved playlist position. [Architecture decision](adr/0001-central-live-broadcast.md) · [Deployment guide](deployment.md)

When the first listener presses **Play**, the station starts sending MP3 audio. The worker reads the recording once at playback speed and sends the same audio to every connected browser. A branch that joins late comes in partway through the song. After the last listener disconnects, the worker stops sending audio. The next listener starts from the beginning of the song at the saved playlist position.

## How managers run it

A manager can upload a recording or find one on YouTube. **Preview** plays it privately. **Import** saves the selected recording to the music library, where it stays available for later use. Managers can then add it to the playlist, move it, or remove an entry and undo the removal. Adding or moving an upcoming song takes effect for everyone while the song on air finishes.

The browser retries a dropped connection. If a song file can’t play, the station skips it. If every song fails, the player shows that the station is unavailable. If the FastAPI worker crashes, systemd restarts it. I tested restoring the SQLite backup from private Google Cloud Storage into a separate instance, then checked the track order and playback from the saved position. [Backup and reboot checks](deployment.md#recovery-drill-observations)

## What I changed after testing

The first YouTube preview felt slow because it waited for FFmpeg to prepare the whole track. I changed it to start playing the audio as yt-dlp downloads it. FFmpeg still normalizes and encodes the track when the manager imports it. On the VM, I checked that Chrome started playing before the download finished and that importing again reused the saved recording. I also tested the loading message, retry, and automatic playback in English and Arabic at desktop and phone widths. [YouTube preview checks](https://github.com/samikayyal/cdr-eval/pull/17) · [Preview dialog checks](https://github.com/samikayyal/cdr-eval/pull/28)

I tested a playlist change with two browser sessions on the hosted station. Both showed the new **Up next** and kept streaming through the change. The new entry was still there when I signed in from a fresh session. The walkthrough above lets you hear that transition on two devices.

## How I used AI

I used OpenAI Codex to work through the GitHub issues and investigate problems. Two prompts from those conversations were “And add pagination with 10 results per page” and “Why does preview take so long?” I checked the first by searching for `فيروز` in Chrome and going through three pages. I checked the second by holding back part of the audio in a Playwright test and watching playback start while the download was still waiting. I used pytest and Pyright for the Python checks, and FFmpeg and VM counters to measure the hosted stream. [YouTube work and test results](https://github.com/samikayyal/cdr-eval/pull/17)

## Scalability

I tested ten HTTPS listeners on one Compute Engine `e2-micro` with two shared vCPUs and 1 GiB of RAM. Every listener kept decoding a 128 kbps MP3 stream while the station received and prepared an 86.4 MB WAV. The decoders reported no playback errors or reconnects.

| What was running | CPU average / peak | RAM used average / peak | Outbound average / peak |
| --- | ---: | ---: | ---: |
| Ten listeners | 1.41% / 2.48% | 373 / 373 MiB | 1.35 / 1.50 Mbps |
| Ten listeners and one upload | 21.63% / 71.13% | 415 / 757 MiB | 1.45 / 1.67 Mbps |

At this bitrate, 30 listeners would need about 3.84 Mbps of audio traffic and 100 would need 12.8 Mbps, plus network overhead. I tested up to ten. The biggest jump in CPU and RAM came from preparing the upload. If uploads start to interfere with playback, I’d move preparation off the station VM. I’d consider a relay if a larger test shows that outbound bandwidth is the limit. [Detailed measurements](hosted-verification.md#capacity)

## Things we could improve

In the Wi-Fi test, one device went quiet as soon as it lost its connection. It started playing again when Wi-Fi came back, and the other stream kept playing. Chrome paused to buffer four times in a separate run and recovered on its own. I’d try a larger buffer on a branch network to see how long it can cover a Wi-Fi drop and how much it delays hearing a playlist change. [Continuity and Wi-Fi test](hosted-verification.md#continuity)
