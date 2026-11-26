import { setLabel } from "./language.js";

const player = document.querySelector("#player");
const playback = document.querySelector("#playback");
const playbackMessage = document.querySelector("#playback-message");
const broadcastState = document.querySelector("#broadcast-state");
const deviceState = document.querySelector("#device-state");
const tracks = document.querySelector("#tracks");
const onAirTitle = document.querySelector("#on-air-title");
const onAirArtist = document.querySelector("#on-air-artist");
const upNextTrack = document.querySelector("#up-next-track");
const upNextTitle = document.querySelector("#up-next-title");
const upNextArtist = document.querySelector("#up-next-artist");
const idleTrack = document.querySelector("#idle-track");
const startsWithTitle = document.querySelector("#starts-with-title");
const startsWithArtist = document.querySelector("#starts-with-artist");

const RETRY_DELAY_MS = 2000;
const PLAYBACK_TIMEOUT_MS = 8000;
let wantsPlayback = false;
let stationUnavailable = false;
let playbackAttempt = 0;
let retryTimer;
let playbackTimeout;
export const broadcastUpdates = new EventTarget();

function disconnect() {
  playbackAttempt += 1;
  clearTimeout(retryTimer);
  clearTimeout(playbackTimeout);
  deviceState.hidden = true;
  player.pause();
  player.removeAttribute("src");
  player.load();
}

function reconnect() {
  if (!wantsPlayback || !player.hasAttribute("src")) {
    return;
  }
  disconnect();
  playbackMessage.hidden = false;
  retryTimer = setTimeout(connect, RETRY_DELAY_MS);
}

function waitForPlayback() {
  // Give the browser time to fill its audio buffer before reopening the stream.
  if (player.hasAttribute("src")) {
    clearTimeout(playbackTimeout);
    playbackTimeout = setTimeout(reconnect, PLAYBACK_TIMEOUT_MS);
  }
}

async function connect() {
  if (!wantsPlayback || stationUnavailable) {
    return;
  }
  const attempt = ++playbackAttempt;
  player.src = "/live.mp3";
  waitForPlayback();
  try {
    await player.play();
  } catch {
    if (attempt === playbackAttempt) {
      reconnect();
    }
  }
}

playback.addEventListener("click", () => {
  wantsPlayback = !wantsPlayback;
  setLabel(playback, wantsPlayback ? "Stop" : "Play");
  if (wantsPlayback) {
    connect();
  } else {
    disconnect();
    playbackMessage.hidden = true;
  }
});

player.addEventListener("error", reconnect);
player.addEventListener("ended", reconnect);
player.addEventListener("waiting", waitForPlayback);
player.addEventListener("playing", () => {
  clearTimeout(playbackTimeout);
  if (wantsPlayback && player.hasAttribute("src")) {
    playbackMessage.hidden = true;
    deviceState.hidden = false;
  }
});

function showBroadcastState(message) {
  if (broadcastState.dataset.i18n !== message) {
    setLabel(broadcastState, message);
  }
  broadcastState.hidden = false;
  tracks.hidden = true;
}

function updateText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}

function updateAvailability(unavailable) {
  if (stationUnavailable === unavailable) {
    return;
  }
  stationUnavailable = unavailable;
  if (wantsPlayback) {
    if (stationUnavailable) {
      disconnect();
      playbackMessage.hidden = true;
    } else {
      connect();
    }
  }
}

async function refreshBroadcast() {
  try {
    const response = await fetch("/api/broadcast", { signal: AbortSignal.timeout(4000) });
    if (!response.ok) {
      throw new Error("Station status request failed");
    }
    const {
      on_air: onAir, up_next: upNext, starts_with: startsWith, unavailable, failed_entry_ids: failedEntryIds,
    } = await response.json();
    updateAvailability(unavailable);
    broadcastUpdates.dispatchEvent(new CustomEvent("status", {
      detail: {
        onAirEntryId: response.headers.get("X-On-Air-Entry-Id"),
        playbackId: response.headers.get("X-Playback-Id"),
        failedEntryIds,
      },
    }));
    if (unavailable) {
      showBroadcastState("Station temporarily unavailable");
    } else if (onAir) {
      updateText(onAirTitle, onAir.title);
      updateText(onAirArtist, onAir.artist);
      if (upNext) {
        updateText(upNextTitle, upNext.title);
        updateText(upNextArtist, upNext.artist);
      }
      upNextTrack.hidden = !upNext;
      broadcastState.hidden = true;
      tracks.hidden = false;
    } else {
      showBroadcastState("Ready to play");
    }
    if (startsWith) {
      updateText(startsWithTitle, startsWith.title);
      updateText(startsWithArtist, startsWith.artist);
    }
    idleTrack.hidden = !startsWith;
  } catch {
    broadcastUpdates.dispatchEvent(new Event("status-error"));
    showBroadcastState("Station status unavailable");
    idleTrack.hidden = true;
  } finally {
    setTimeout(refreshBroadcast, 1000);
  }
}

refreshBroadcast();

export function songText(track, artistBadge = null) {
  const title = document.createElement("bdi");
  title.className = "song-title";
  title.textContent = track.title;
  const artist = document.createElement("bdi");
  artist.className = "song-artist";
  artist.textContent = track.artist;
  const song = document.createElement("div");
  song.className = "song";
  song.append(title);
  if (artistBadge) {
    const artistLine = document.createElement("div");
    artistLine.className = "song-artist-line";
    artistLine.append(artist, artistBadge);
    song.append(artistLine);
  } else {
    song.append(artist);
  }
  return song;
}
