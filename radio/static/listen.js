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
const credits = document.querySelector("#credits");
const creditsList = document.querySelector("#credits-list");
const creditsMessage = document.querySelector("#credits-message");

let playbackAttempt = 0;
export const broadcastUpdates = new EventTarget();

function stop() {
  playbackAttempt += 1;
  player.pause();
  player.removeAttribute("src");
  player.load();
  setLabel(playback, "Play");
}

function playbackFailed() {
  if (!player.hasAttribute("src")) {
    return;
  }
  stop();
  playbackMessage.hidden = false;
}

playback.addEventListener("click", async () => {
  if (player.hasAttribute("src")) {
    stop();
    return;
  }

  const attempt = ++playbackAttempt;
  playbackMessage.hidden = true;
  setLabel(playback, "Stop");
  player.src = "/live.mp3";
  try {
    await player.play();
  } catch {
    if (attempt === playbackAttempt) {
      playbackFailed();
    }
  }
});

player.addEventListener("error", playbackFailed);
player.addEventListener("ended", playbackFailed);

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
    const { on_air: onAir, up_next: upNext } = await response.json();
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
  } catch {
    showBroadcastState("Station status unavailable");
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
