const player = document.querySelector("#player");
const playback = document.querySelector("#playback");
const playbackMessage = document.querySelector("#playback-message");
const broadcastState = document.querySelector("#broadcast-state");
const tracks = document.querySelector("#tracks");
const onAirTitle = document.querySelector("#on-air-title");
const onAirArtist = document.querySelector("#on-air-artist");
const upNextTitle = document.querySelector("#up-next-title");
const upNextArtist = document.querySelector("#up-next-artist");

let playbackAttempt = 0;

function stop() {
  playbackAttempt += 1;
  player.pause();
  player.removeAttribute("src");
  player.load();
  playback.textContent = "Play";
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
  playback.textContent = "Stop";
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
  broadcastState.textContent = message;
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
