# Al Qubtan Restaurants Radio assessment report

Planned public station URL: <https://quptan-radio.samikayyal.com>

## Scalability

**Draft note — revise before submission.** After the 10-listener test with a concurrent upload, report the VM size, stream bitrate, observed outbound bandwidth, CPU and memory use, and any playback interruptions. Distinguish measured results from bandwidth estimates for larger branch counts. Revisit whether relay servers would address a delivery bottleneck or whether the results point to a different next step.

## Things we could improve

**Playback during branch Wi-Fi interruptions.** The station design uses the browser’s normal buffer for the live MP3 stream. If a branch loses Wi-Fi, listeners can hear the audio already buffered. Once that audio runs out, the branch falls silent until playback reconnects. A future improvement would be to test whether keeping more audio on the device can bridge typical branch outages while still letting listeners hear central playlist changes promptly.
