# Hosted continuity and capacity evidence

Internal operator record for issue #12. The tests used the public HTTPS station, the original **Harbor Lights** and **Evening Tide** rotation, and VM release ea749d4. [Issue #12](https://github.com/samikayyal/cdr-eval/issues/12) · [Station](https://quptan-radio.samikayyal.com) · [VM release](https://github.com/samikayyal/cdr-eval/commit/ea749d4ef81d41c742e2483f434df2e87ee63a97) · [Deployment runbook](deployment.md)

## Continuity

The November 25 browser session used the public **Listen** page in Chrome for one hour. The primary player was instrumented with a Web Audio analyser and media-event timestamps. A second Chrome session joined about 49 seconds into playback and used **Stop** after about 71 seconds of playing. A separate VM client also joined and left during the hour, decoding about 45 seconds over HTTPS. The primary browser played through both departures and the manager edit. The desktop browser ran headless, so this evidence measures digital audio and the player’s behavior.

Across 3,556 primary-player samples, the browser issued one live request and never reset its media clock. It recorded four buffering waits. Three lasted 7–27 ms and one lasted about two seconds. Playback resumed on the same connection each time without user action. The media clock advanced about 59 minutes and 58 seconds over the hour. After **Stop**, the station returned to idle.

During the session the signed-in manager placed a second **Harbor Lights** immediately after the original on-air entry. **Up next** changed from **Evening Tide** to **Harbor Lights** while the original entry remained on air. The added entry went on air at the next transition, and **Evening Tide** followed. Removing the test entry restored the two original entries in order.

An earlier decoder run on November 24 ended after 31 minutes and 16 seconds of decoded audio. The workstation MP3 transfer and the independent status request both timed out at about 22:45 Amman time. Both VM services stayed active without a restart. The exact cause of the timeout was undetermined.

- Earlier in that run, a second listener joined on a physical device and heard the station. They reported that playback stopped as soon as they cut the device’s internet connection and resumed as soon as they restored it, without tapping **Play** again. The primary decoder kept advancing during that test. The second device’s exact buffer duration and **Reconnecting** display were unmeasured.
- In the retry, the workstation decoded **60 minutes** of stereo MP3 from a single HTTPS request in 60 minutes and 1 second of wall time. A separate decoder started on the VM about 13 seconds later, connected to the public hostname, and also decoded approximately 60 minutes. No decoder errors or runs of silence of 0.75 seconds or longer at or below −55 dBFS were logged.
- The workstation status poll timed out four times during the retry. Its five-second decoder progress reports briefly arrived farther apart, with a maximum observed interval of 14.5 seconds between advances. The stream connection stayed open and decoding caught up. The FFmpeg checks measured decoded audio and progress, so they cannot establish the exact duration of any speaker-level pause.

## Capacity

The Compute Engine `e2-micro` in `europe-west1-b` has two shared guest vCPUs and 1 GiB of configured RAM. Linux exposed about 953 MiB. The run used ten separate HTTPS MP3 connections from one workstation, each decoding independently. After a zero-listener baseline and a ten-listener warm-up, the manager uploaded a unique five-minute WAV made from the station demo audio. It was prepared into a library MP3 and left unscheduled. Samples came from Linux guest counters about once a second.

The station streams stereo 44.1 kHz MP3 at 128 kbps per listener. The bandwidth table uses decimal Mbps and the VM interface’s total outbound bytes. It includes stream delivery, protocol traffic and other VM traffic. CPU is the share of total guest CPU ticks spent in user and kernel work, including interrupt handling. Memory used is total RAM minus available RAM.

Measured on November 25, Amman time. The upload endpoint returned HTTP 201 after 51.8 seconds, including transfer and preparation. FFmpeg appeared in 16 of the VM’s one-second samples during that window.

| Phase | CPU mean / peak | Used RAM mean / peak | Outbound mean / peak |
| --- | ---: | ---: | ---: |
| Idle | 0.76% / 1.00% | 372 / 372 MiB | 0.0017 / 0.0020 Mbps |
| Ten listeners, before upload | 1.41% / 2.48% | 373 / 373 MiB | 1.35 / 1.50 Mbps |
| Ten listeners, during upload and preparation | 21.63% / 71.13% | 415 / 757 MiB | 1.45 / 1.67 Mbps |

All ten streams remained connected throughout the 86.4 MB upload and advanced by 51.77 seconds of decoded audio during its 51.85-second transfer and preparation window. The longest interval between roughly one-second decoder progress updates during the upload was 1.03 seconds. Each listener decoded the full three minutes and exited successfully. There were no logged playback retries, decoder errors, or silence detections. The test recording was removed from the library after the run, and the station returned to idle with the original rotation.

## Larger audiences

At 128 kbps per listener, ten listeners require 1.28 Mbps of encoded audio payload. By the same calculation, 30 listeners require 3.84 Mbps and 100 require 12.8 Mbps before delivery overhead. These projections cover audio payload only.

## Repeat the checks

1. Open the [station](https://quptan-radio.samikayyal.com) on two devices. Press **Play** on the first and leave it running. Join on the second, then disconnect only the second device’s network. Listen for audio to stop on that device and continue on the first. Restore the network and check that the second device resumes without another tap. Press **Stop** on the second device.
2. While the first device plays, sign in with the assessment credentials and use **Choose position** in **Playlist Management** to place a recording immediately after the on-air entry. Return to **Listen** and check that **Up next** changes while the current recording continues. Listen for the new entry at the next transition. Remove the test entry if you added it only for this check.
3. After the continuity listeners stop, start ten separate decoders against the public `/live.mp3` URL. Confirm all ten decode before uploading a fresh WAV through **Song Discovery**. Record stream progress from each decoder and capture the VM’s `/proc/stat`, `/proc/meminfo`, and external-interface transmit counter from `/proc/net/dev` before and during preparation. Stop the decoders, remove the unscheduled test upload, and check that **Listen** returns to **Ready to play**.
