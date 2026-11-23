import { setLabel } from "./language.js";
import { refreshCredits, songText } from "./listen.js";

const get = (id) => document.getElementById(id);
const api = "/api/manager";
const nav = get("manager-nav");
const tabs = [...nav.querySelectorAll('[role="tab"]')];
const login = get("sign-in");
const loginForm = get("sign-in-form");
const sessionMessage = get("session-message");
const uploadForm = get("upload-form");
const recordingInput = uploadForm.elements.recording;
const uploadFilename = get("upload-filename");
const editForm = get("metadata-form");
const searchForm = get("search-form");
const searchResults = get("search-results");
const playlistList = get("playlist-list");
const readyList = get("ready-list");
const readySection = get("ready-section");
const playlistPanel = get("playlist-panel");
const discoveryMessage = get("discovery-message");
const playlistMessage = get("playlist-message");
const searchMessage = get("search-message");
const live = get("player");
const preview = get("preview");
const previewPanel = get("preview-panel");
const previewMessage = get("preview-message");
const previewName = get("preview-name");
const requestFailed = "Request failed. Try again.";

const errors = {
  "invalid_credentials": "Incorrect username or password.",
  "sign_in_required": "Sign in to continue managing the station.",
  "unsupported_format": "Choose an MP3, WAV, or M4A file.",
  "upload_too_large": "The file exceeds 100 MB.",
  "preparation_busy": "Another recording is being prepared. Try again shortly.",
  "insufficient_space": "There is not enough disk space to prepare the file.",
  "upload_interrupted": "Upload interrupted. Try again.",
  "invalid_audio": "Could not prepare audio from this file.",
  "preparation_unavailable": "Audio preparation is unavailable.",
  "track_not_found": "This recording is no longer available.",
};

let editedTrack = null;
let previewTrack = null;
let priorMute = null;
let searchVersion = 0;
let searchSubmitted = false;
let playlistVersion = 0;

function showMessage(element, label = "") {
  setLabel(element, label);
  element.hidden = !label;
}

function openTab(selected, trackId) {
  closeSongActions();
  for (const tab of tabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    get(tab.getAttribute("aria-controls")).hidden = !active;
  }
  if (selected === get("playlist-tab")) {
    showMessage(playlistMessage);
    refreshPlaylist(trackId).catch((error) => showMessage(playlistMessage, error.message));
  }
}

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => openTab(tab));
  tab.addEventListener("keydown", (event) => {
    const rtl = document.documentElement.dir === "rtl";
    const next = event.key === (rtl ? "ArrowLeft" : "ArrowRight");
    const previous = event.key === (rtl ? "ArrowRight" : "ArrowLeft");
    if (next || previous) {
      event.preventDefault();
      const adjacent = tabs[(index + (next ? 1 : -1) + tabs.length) % tabs.length];
      adjacent.focus();
      openTab(adjacent);
    }
  });
}

function setSignedIn(signedIn) {
  nav.hidden = !signedIn;
  login.hidden = signedIn;
  const listenPanel = get("listen-panel");
  if (signedIn) {
    listenPanel.setAttribute("role", "tabpanel");
    listenPanel.setAttribute("aria-labelledby", "listen-tab");
    return;
  }
  listenPanel.removeAttribute("role");
  listenPanel.setAttribute("aria-labelledby", "listen-heading");
  openTab(tabs[0]);
  stopPreview();
  searchVersion += 1;
  searchSubmitted = false;
  playlistVersion += 1;
  playlistList.replaceChildren();
  readyList.replaceChildren();
  readySection.hidden = true;
  searchResults.replaceChildren();
  searchForm.reset();
  uploadForm.reset();
  editForm.hidden = true;
  editedTrack = null;
  for (const status of [playlistMessage, discoveryMessage, searchMessage]) {
    showMessage(status);
  }
}

async function request(path, options) {
  const response = await fetch(api + path, { cache: "no-store", ...options }).catch(() => {
    throw new Error(requestFailed);
  });
  if (!response.ok) {
    const { detail } = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/session") {
      setSignedIn(false);
      showMessage(sessionMessage, errors.sign_in_required);
    }
    throw new Error(errors[detail] ?? requestFailed);
  }
  return response;
}

function jsonRequest(method, data) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

async function withButton(button, status, action) {
  button.disabled = true;
  showMessage(status);
  try {
    await action();
  } catch (error) {
    showMessage(status, error.message);
  } finally {
    button.disabled = false;
  }
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  withButton(event.submitter, sessionMessage, async () => {
    await request("/session", jsonRequest("POST", Object.fromEntries(new FormData(loginForm))));
    loginForm.reset();
    login.open = false;
    setSignedIn(true);
    tabs[0].focus();
  });
});

get("sign-out").addEventListener("click", (event) => {
  withButton(event.currentTarget, sessionMessage, async () => {
    await request("/session", { method: "DELETE" });
    setSignedIn(false);
    login.querySelector("summary").focus();
  });
});

request("/session")
  .then((response) => response.json())
  .then(({ signed_in: signedIn }) => {
    if (signedIn) {
      setSignedIn(true);
    }
  })
  .catch(() => showMessage(sessionMessage, requestFailed));

function focusSong(trackId) {
  if (!playlistPanel.hidden) {
    playlistPanel.querySelector(`[data-track-id="${trackId}"] summary`)?.focus();
  }
}

async function refreshPlaylist(focusTrack) {
  const version = ++playlistVersion;
  const [playlist, library] = await Promise.all([
    request("/playlist").then((response) => response.json()),
    request("/library").then((response) => response.json()),
  ]);
  if (playlistVersion !== version) {
    return;
  }
  const scheduled = new Set(playlist.map((track) => track.id));
  const ready = library.filter((track) => !scheduled.has(track.id));
  playlistList.replaceChildren(...playlist.map(songRow));
  readyList.replaceChildren(...ready.map(songRow));
  readySection.hidden = ready.length === 0;
  if (focusTrack !== undefined) {
    focusSong(focusTrack);
  }
}

function actionButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  setLabel(button, label);
  button.addEventListener("click", () => action(button));
  return button;
}

function songRow(track) {
  const item = document.createElement("li");
  const row = document.createElement("div");
  row.className = "song-row";
  row.dataset.trackId = track.id;
  const details = document.createElement("details");
  details.className = "song-actions";
  details.name = "song-actions";
  const trigger = document.createElement("summary");
  const label = document.createElement("span");
  label.className = "sr-only";
  setLabel(label, "Song actions");
  const name = document.createElement("span");
  name.className = "sr-only";
  name.textContent = ` ${track.title}`;
  const dots = document.createElement("span");
  dots.textContent = "⋮";
  dots.setAttribute("aria-hidden", "true");
  trigger.append(label, name, dots);
  const actions = document.createElement("div");
  const option = (label, action) => actionButton(label, (button) => {
    details.open = false;
    trigger.focus();
    action(button);
  });
  actions.append(
    option("Preview", () => startPreview(track)),
    option("Edit details", () => openEditor(track)),
    option("Add to end", (button) => withButton(button, playlistMessage, async () => {
      await request("/playlist", jsonRequest("POST", { track_id: track.id }));
      showMessage(playlistMessage, "Added to the end of the playlist.");
      await refreshPlaylist(track.id);
      refreshCredits();
    })),
  );
  details.append(trigger, actions);
  row.append(songText(track), details);
  item.append(row);
  return item;
}

function closeSongActions() {
  const menu = document.querySelector(".song-actions[open]");
  if (menu) {
    menu.open = false;
  }
  return menu;
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".song-actions")) {
    closeSongActions();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    const focused = document.activeElement;
    const menu = closeSongActions();
    if (menu?.contains(focused)) {
      menu.querySelector("summary").focus();
    }
  }
});

async function searchLibrary() {
  const version = ++searchVersion;
  const query = new FormData(searchForm).get("query");
  const response = await request("/library?" + new URLSearchParams({ q: query }));
  const tracks = await response.json();
  if (version !== searchVersion) {
    return;
  }
  showMessage(searchMessage, tracks.length ? "" : "No matching recordings.");
  searchResults.replaceChildren(...tracks.map((track) => {
    const item = document.createElement("li");
    item.append(songText(track), actionButton("View in Playlist Management", () => {
      openTab(get("playlist-tab"), track.id);
    }));
    return item;
  }));
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  searchSubmitted = true;
  withButton(event.submitter, searchMessage, searchLibrary);
});

recordingInput.addEventListener("change", () => {
  uploadFilename.textContent = recordingInput.files[0]?.name ?? "";
});
uploadForm.addEventListener("reset", () => {
  uploadFilename.textContent = "";
});
uploadForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const file = recordingInput.files[0];
  if (!file) {
    return;
  }
  if (file.size > 100_000_000) {
    showMessage(discoveryMessage, errors.upload_too_large);
    return;
  }
  withButton(event.submitter, discoveryMessage, async () => {
    showMessage(discoveryMessage, "Preparing recording…");
    const response = await request("/library?" + new URLSearchParams({ filename: file.name }), {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const track = await response.json();
    uploadForm.reset();
    const message = response.status === 201
      ? "Recording ready in the library."
      : "This recording is already in the library.";
    if (!get("discovery-panel").hidden) {
      openTab(get("playlist-tab"), track.id);
      showMessage(playlistMessage, message);
    } else {
      showMessage(discoveryMessage, message);
    }
  });
});

function fillEditor(track) {
  editedTrack = track.id;
  editForm.elements.title.value = track.title;
  editForm.elements.artist.value = track.artist;
  editForm.hidden = false;
}

function openEditor(track) {
  fillEditor(track);
  editForm.elements.title.focus();
}

get("cancel-edit").addEventListener("click", () => {
  editForm.hidden = true;
  focusSong(editedTrack);
  editedTrack = null;
});

editForm.addEventListener("submit", (event) => {
  event.preventDefault();
  withButton(event.submitter, playlistMessage, async () => {
    const data = Object.fromEntries(new FormData(editForm));
    if (!data.title.trim()) {
      showMessage(playlistMessage, "Enter a title of up to 300 characters and an optional artist.");
      return;
    }
    const response = await request(`/library/${editedTrack}`, jsonRequest("PATCH", data));
    const updated = await response.json();
    if (previewTrack === updated.id) {
      previewName.textContent = updated.title;
    }
    if (editedTrack === updated.id) {
      editForm.hidden = true;
      editedTrack = null;
    }
    showMessage(playlistMessage, "Details saved.");
    if (searchSubmitted) {
      searchLibrary().catch((error) => showMessage(searchMessage, error.message));
    }
    await refreshPlaylist(updated.id);
    refreshCredits();
  });
});

function muteLive() {
  if (priorMute === null) {
    priorMute = live.muted;
    live.muted = true;
  }
}

function restoreLive() {
  if (priorMute !== null) {
    live.muted = priorMute;
    priorMute = null;
  }
}

function stopPreview() {
  previewTrack = null;
  preview.pause();
  preview.removeAttribute("src");
  preview.load();
  previewPanel.hidden = true;
  restoreLive();
}

function previewFailed() {
  if (previewTrack !== null) {
    restoreLive();
    showMessage(previewMessage, "Could not play the preview.");
  }
}

async function startPreview(track) {
  showMessage(previewMessage);
  previewPanel.hidden = false;
  previewName.textContent = track.title;
  get("stop-preview").focus();
  if (previewTrack !== track.id || preview.error) {
    previewTrack = track.id;
    preview.src = `${api}/library/${track.id}/preview`;
  }
  muteLive();
  try {
    await preview.play();
  } catch (error) {
    if (error.name !== "AbortError" && previewTrack === track.id) {
      previewFailed();
    }
  }
}

get("stop-preview").addEventListener("click", () => {
  const trackId = previewTrack;
  stopPreview();
  focusSong(trackId);
});
preview.addEventListener("play", () => {
  if (!preview.paused) {
    muteLive();
  }
});
preview.addEventListener("pause", () => {
  if (preview.paused) {
    restoreLive();
  }
});
preview.addEventListener("ended", restoreLive);
preview.addEventListener("error", previewFailed);
