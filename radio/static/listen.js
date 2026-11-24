import { setLabel } from "./language.js";

const player = document.querySelector("#player");
const playback = document.querySelector("#playback");
const playbackMessage = document.querySelector("#playback-message");
const broadcastState = document.querySelector("#broadcast-state");
const tracks = document.querySelector("#tracks");
const onAirTitle = document.querySelector("#on-air-title");
const onAirArtist = document.querySelector("#on-air-artist");
const upNextTitle = document.querySelector("#up-next-title");
const upNextArtist = document.querySelector("#up-next-artist");
const idleTrack = document.querySelector("#idle-track");
const startsWithTitle = document.querySelector("#starts-with-title");
const startsWithArtist = document.querySelector("#starts-with-artist");
const credits = document.querySelector("#credits");
const creditsList = document.querySelector("#credits-list");
const creditsMessage = document.querySelector("#credits-message");

const RETRY_DELAY_MS = 2000;
const PLAYBACK_TIMEOUT_MS = 8000;
let wantsPlayback = false;
let playbackAttempt = 0;
let retryTimer;
let playbackTimeout;
export const broadcastUpdates = new EventTarget();

function disconnect() {
  playbackAttempt += 1;
  clearTimeout(retryTimer);
  clearTimeout(playbackTimeout);
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
  if (player.hasAttribute("src")) {
    playbackMessage.hidden = true;
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

async function refreshBroadcast() {
  try {
    const response = await fetch("/api/broadcast", { signal: AbortSignal.timeout(4000) });
    if (!response.ok) {
      throw new Error("Station status request failed");
    }
    const { on_air: onAir, up_next: upNext, starts_with: startsWith } = await response.json();
    broadcastUpdates.dispatchEvent(new CustomEvent("on-air", { detail: response.headers.get("X-On-Air-Entry-Id") }));
    if (onAir && upNext) {
      updateText(onAirTitle, onAir.title);
      updateText(onAirArtist, onAir.artist);
      updateText(upNextTitle, upNext.title);
      updateText(upNextArtist, upNext.artist);
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
    showBroadcastState("Station status unavailable");
    idleTrack.hidden = true;
  } finally {
    setTimeout(refreshBroadcast, 1000);
  }
}

refreshBroadcast();

export function songText(track) {
  const title = document.createElement("bdi");
  title.className = "song-title";
  title.textContent = track.title;
  const artist = document.createElement("bdi");
  artist.className = "song-artist";
  artist.textContent = track.artist;
  const song = document.createElement("div");
  song.className = "song";
  song.append(title, artist);
  return song;
}

export async function refreshCredits() {
  if (!credits.open) {
    return;
  }
  try {
    const response = await fetch("/api/credits", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Credits request failed");
    }
    const songs = await response.json();
    creditsList.replaceChildren(...songs.map((track) => {
      const item = document.createElement("li");
      item.append(songText(track));
      return item;
    }));
    creditsMessage.hidden = true;
  } catch {
    setLabel(creditsMessage, "Request failed. Try again.");
    creditsMessage.hidden = false;
  }
}

credits.addEventListener("toggle", refreshCredits);
