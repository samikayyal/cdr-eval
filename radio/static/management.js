import { setLabel } from "./language.js";
import { broadcastUpdates, refreshCredits, songText } from "./listen.js";
import { enablePlaylistDrag } from "./playlist-drag.js";

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
const uploadSelectionLabel = get("upload-selection-label");
const uploadResults = get("upload-results");
const uploadButton = uploadForm.querySelector('button[type="submit"]');
const editForm = get("metadata-form");
const searchForm = get("search-form");
const searchResults = get("search-results");
const libraryResults = get("library-results");
const youtubeResults = get("youtube-results");
const youtubeRecordings = get("youtube-recordings");
const youtubeMessage = get("youtube-message");
const youtubePagination = get("youtube-pagination");
const youtubePrevious = get("youtube-previous");
const youtubeNext = get("youtube-next");
const youtubePageSize = 10;
const playlistList = get("playlist-list");
const playlistRemovals = get("playlist-removals");
const undoDuration = 10_000;
const undoTimers = new Map();
const readyList = get("ready-list");
const readySection = get("ready-section");
const playlistPanel = get("playlist-panel");
const discoveryMessage = get("discovery-message");
const playlistMessage = get("playlist-message");
const searchMessage = get("search-message");
const placementPanel = get("placement-panel");
const placementHeading = get("placement-heading");
const placementTrack = get("placement-track");
const placementMessage = get("placement-message");
const placementForm = get("placement-form");
const placementControls = get("placement-controls");
const placementOptions = get("placement-options");
const appendEntry = get("append-entry");
const placementPicker = get("placement-picker");
const placementGaps = get("placement-gaps");
const confirmPlacement = get("confirm-placement");
const closePlacement = get("close-placement");
const deletionPanel = get("delete-recording-panel");
const deletionForm = get("delete-recording-form");
const deletionControls = get("delete-recording-controls");
const deletionMessage = get("delete-recording-message");
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
  "entry_not_found": "This playlist entry is no longer available.",
  "last_playable_entry": "Keep at least one playable entry in the playlist.",
  "undo_unavailable": "This removal can no longer be undone.",
  "track_in_use": "Remove all playlist entries using this recording before deleting it.",
  "youtube_search_failed": "YouTube search is unavailable. You can still upload a recording.",
  "youtube_search_busy": "YouTube search is busy. Try again shortly.",
  "youtube_retrieval_failed": "Could not retrieve this YouTube recording. You can upload a file instead.",
  "preview_storage_full": "Temporary previews are full. Try again in a few minutes.",
};

let editedTrack = null;
let previewTrack = null;
let priorMute = null;
let searchVersion = 0;
let youtubeVersion = 0;
let youtubeMatches = [];
let youtubePage = 1;
let searchedQuery = null;
let playlistVersion = 0;
let uploadController = null;
let placement = null;
let onAirEntry = null;
let usageTrackId = null;
let deletingTrack = null;
const cancelPlaylistDrag = enablePlaylistDrag(playlistList, dropPlaylistEntry);

function showMessage(element, label = "") {
  setLabel(element, label);
  element.hidden = !label;
}

function openTab(selected, trackId, showUsage = false) {
  cancelPlaylistDrag();
  closeSongActions();
  usageTrackId = showUsage ? trackId : null;
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
  previewPanel.close();
  placementPanel.close();
  deletionPanel.close();
  for (const item of undoTimers.keys()) {
    dismissUndo(item);
  }
  searchVersion += 1;
  youtubeVersion += 1;
  searchedQuery = null;
  playlistVersion += 1;
  playlistList.replaceChildren();
  readyList.replaceChildren();
  readySection.hidden = true;
  searchResults.replaceChildren();
  youtubeMatches = [];
  renderYoutubePage();
  libraryResults.hidden = true;
  youtubeResults.hidden = true;
  searchForm.reset();
  uploadController?.abort();
  uploadForm.reset();
  uploadResults.replaceChildren();
  uploadResults.hidden = true;
  editForm.hidden = true;
  editedTrack = null;
  for (const status of [playlistMessage, discoveryMessage, searchMessage, youtubeMessage]) {
    showMessage(status);
  }
}

async function request(path, options, acceptedStatus) {
  const response = await fetch(api + path, { cache: "no-store", ...options }).catch(() => {
    throw new Error(requestFailed);
  });
  if (!response.ok && response.status !== acceptedStatus) {
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

function focusSong(trackId, entryId = null) {
  if (!playlistPanel.hidden) {
    const row = entryId === null ? `[data-track-id="${trackId}"]` : `[data-entry-id="${entryId}"]`;
    playlistPanel.querySelector(`${row} summary`)?.focus();
  }
}

function markOnAir(entryId) {
  const active = entryId === null ? null : String(entryId);
  if (active === onAirEntry) {
    return;
  }
  onAirEntry = active;
  for (const badge of document.querySelectorAll("[data-on-air-for]")) {
    badge.hidden = badge.dataset.onAirFor !== active;
  }
}

broadcastUpdates.addEventListener("on-air", (event) => markOnAir(event.detail));

function entryText(track, entryId) {
  const song = songText(track);
  if (entryId !== null) {
    const badge = document.createElement("span");
    badge.className = "on-air-badge";
    badge.dataset.onAirFor = entryId;
    badge.hidden = String(entryId) !== onAirEntry;
    setLabel(badge, "On air");
    song.append(badge);
  }
  return song;
}

async function fetchPlaylistOrder() {
  return (await request("/playlist/order", { signal: AbortSignal.timeout(5000) })).json();
}

function renderPlaylistOrder(order) {
  cancelPlaylistDrag();
  markOnAir(order.on_air_entry_id);
  playlistList.replaceChildren(...order.entries.map(({ id, track }) => songRow(track, id)));
}

async function refreshPlaylist(focusTrack, focusEntry = null) {
  const version = ++playlistVersion;
  const [order, library] = await Promise.all([
    fetchPlaylistOrder(),
    request("/library").then((response) => response.json()),
  ]);
  if (playlistVersion !== version) {
    return;
  }
  renderPlaylistOrder(order);
  const scheduled = new Set(order.entries.map(({ track }) => track.id));
  const ready = library.filter((track) => !scheduled.has(track.id));
  readyList.replaceChildren(...ready.map((track) => songRow(track)));
  readySection.hidden = ready.length === 0;
  if (focusTrack !== undefined) {
    focusSong(focusTrack, focusEntry);
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

function playlistGrip(track, entryId) {
  const grip = document.createElement("button");
  grip.type = "button";
  grip.className = "secondary playlist-grip";
  grip.addEventListener("click", () => openPlacement(track, entryId));
  const gripLabel = document.createElement("span");
  gripLabel.className = "sr-only";
  setLabel(gripLabel, "Reorder");
  const title = document.createElement("span");
  title.className = "sr-only";
  title.textContent = ` ${track.title}`;
  grip.append(gripLabel, title);
  return grip;
}

function songRow(track, entryId = null) {
  const item = document.createElement("li");
  const row = document.createElement("div");
  row.className = "song-row";
  row.dataset.trackId = track.id;
  if (entryId !== null) {
    item.dataset.entryId = entryId;
    item.classList.toggle("playlist-usage-match", track.id === usageTrackId);
    row.classList.add("playlist-row");
    row.append(playlistGrip(track, entryId));
  }
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
    option("Add to playlist", () => openPlacement(track)),
  );
  if (entryId !== null) {
    actions.append(option("Move to…", () => openPlacement(track, entryId)));
  }
  details.append(trigger, actions);
  row.append(entryText(track, entryId));
  if (entryId === null) {
    row.append(details);
  } else {
    const controls = document.createElement("div");
    controls.className = "playlist-row-controls";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary playlist-remove";
    const icon = document.createElement("span");
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "×";
    const label = document.createElement("span");
    label.className = "sr-only";
    setLabel(label, "Remove from playlist");
    const title = document.createElement("bdi");
    title.className = "sr-only";
    title.textContent = track.title;
    remove.append(icon, label, title);
    remove.addEventListener("click", () => removeEntry(remove, entryId, track));
    controls.append(details, remove);
    row.append(controls);
  }
  item.append(row);
  return item;
}

function refreshLibraryResults() {
  if (searchedQuery !== null) {
    searchLibrary().catch((error) => showMessage(searchMessage, error.message));
  }
}

function refreshAfterPlaylistEdit(focusTrack, focusEntry) {
  refreshPlaylist(focusTrack, focusEntry).catch((error) => showMessage(playlistMessage, error.message));
  refreshLibraryResults();
  refreshCredits();
}

function dismissUndo(item) {
  clearTimeout(undoTimers.get(item));
  undoTimers.delete(item);
  const hadFocus = item.contains(document.activeElement);
  item.remove();
  if (hadFocus && !nav.hidden) {
    nav.querySelector('[aria-selected="true"]')?.focus({ preventScroll: true });
  }
}

function showUndo(track, entryId, token) {
  const item = document.createElement("li");
  item.dataset.removedTrackId = track.id;
  item.className = "undo-popup";
  const details = document.createElement("div");
  details.className = "song";
  const title = document.createElement("bdi");
  title.className = "song-title";
  title.textContent = track.title;
  const message = document.createElement("span");
  message.className = "song-artist";
  setLabel(message, "Removed from playlist.");
  details.append(title, message);

  const undo = actionButton("Undo", (button) => withButton(button, message, async () => {
    const response = await request(`/playlist/undo/${token}`, { method: "POST" });
    const order = await response.json();
    dismissUndo(item);
    renderPlaylistOrder(order);
    showMessage(playlistMessage, "Playlist entry restored.");
    refreshAfterPlaylistEdit(track.id, entryId);
  }));
  const ring = document.createElement("span");
  ring.className = "undo-ring";
  ring.setAttribute("aria-hidden", "true");
  ring.style.setProperty("--undo-duration", `${undoDuration}ms`);
  item.append(details, undo, ring);
  playlistRemovals.prepend(item);
  undoTimers.set(item, setTimeout(() => dismissUndo(item), undoDuration));

  if (!playlistPanel.hidden) {
    undo.focus({ preventScroll: true });
  }
}

function removeEntry(button, entryId, track) {
  return withButton(button, playlistMessage, async () => {
    const response = await request(`/playlist/${entryId}`, { method: "DELETE" });
    const { order, undo_token: token } = await response.json();
    renderPlaylistOrder(order);
    if (!nav.hidden) {
      showUndo(track, entryId, token);
    }
    refreshAfterPlaylistEdit();
  });
}

function openPlacement(track, entryId = null) {
  const moving = entryId !== null;
  placement = { track, entryId, gaps: [], busy: false };
  placementTrack.replaceChildren(entryText(track, entryId));
  setLabel(placementHeading, moving ? "Move to…" : "Add to playlist");
  setLabel(confirmPlacement, moving ? "Move here" : "Add here");
  placementOptions.hidden = false;
  appendEntry.hidden = moving;
  placementPicker.hidden = true;
  placementGaps.replaceChildren();
  showMessage(placementMessage);
  placementControls.disabled = false;
  closePlacement.disabled = false;
  placementPanel.showModal();
  placementHeading.focus();
  if (moving) {
    choosePosition();
  }
}

function renderGaps(current, order) {
  markOnAir(order.on_air_entry_id);
  const entries = order.entries.filter(({ id }) => id !== current.entryId);
  current.gaps = [];
  const rows = [];
  for (let index = 0; index <= entries.length; index += 1) {
    const after = entries[index - 1];
    const before = entries[index];
    current.gaps.push({ after_entry_id: after?.id ?? null, before_entry_id: before?.id ?? null });
    const radios = document.createElement("li");
    const label = document.createElement("label");
    label.className = "placement-gap";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "gap";
    input.value = index;
    const neighbors = [after, before].filter(Boolean).map(({ id }) => `placement-entry-${id}`);
    if (neighbors.length) {
      input.setAttribute("aria-describedby", neighbors.join(" "));
    }
    const name = document.createElement("span");
    const gapLabel = !entries.length ? "First position"
      : !after ? "Start of playlist" : !before ? "End of playlist" : "Between these entries";
    setLabel(name, gapLabel);
    label.append(input, name);
    radios.append(label);
    rows.push(radios);
    if (before) {
      const row = document.createElement("li");
      row.className = "placement-entry";
      row.id = `placement-entry-${before.id}`;
      row.append(entryText(before.track, before.id));
      rows.push(row);
    }
  }
  placementGaps.replaceChildren(...rows);
  placementGaps.scrollTop = 0;
  placementPicker.hidden = false;
  confirmPlacement.disabled = true;
  placementHeading.focus();
}

async function runPlacement(action) {
  const current = placement;
  if (!current || current.busy || !placementPanel.open) {
    return;
  }
  current.busy = true;
  placementControls.disabled = true;
  closePlacement.disabled = true;
  showMessage(placementMessage);
  try {
    await action(current);
  } catch (error) {
    if (placementPanel.open) {
      showMessage(placementMessage, error.message);
    }
  } finally {
    current.busy = false;
    placementControls.disabled = false;
    closePlacement.disabled = false;
  }
}

function choosePosition() {
  runPlacement(async (current) => {
    showMessage(placementMessage, "Loading playlist…");
    const order = await fetchPlaylistOrder();
    showMessage(placementMessage);
    renderGaps(current, order);
    placementOptions.hidden = true;
  });
}

function requestPlaylistMove(entryId, gap) {
  return request(`/playlist/${entryId}`, jsonRequest("PATCH", gap), 409);
}

async function dropPlaylistEntry(entryId, gap) {
  const handle = `[data-entry-id="${entryId}"] .playlist-grip`;
  const hadFocus = document.activeElement?.matches(handle);
  let moved = false;
  showMessage(playlistMessage);
  try {
    const response = await requestPlaylistMove(entryId, gap);
    if (response.status === 409) {
      const { detail } = await response.json();
      playlistVersion += 1;
      renderPlaylistOrder(detail.order);
      showMessage(playlistMessage, "The playlist order changed. Drag the entry again.");
      playlistMessage.scrollIntoView({ block: "nearest" });
    } else {
      moved = true;
      await refreshPlaylist();
      showMessage(playlistMessage, "Playlist entry moved.");
      refreshCredits();
    }
  } catch (error) {
    showMessage(playlistMessage, error.message);
  } finally {
    if (hadFocus && !playlistPanel.hidden && document.activeElement === document.body) {
      playlistList.querySelector(handle)?.focus({ preventScroll: true });
    }
  }
  return moved;
}

function savePlacement(gap) {
  return runPlacement(async (current) => {
    const moving = current.entryId !== null;
    const response = moving
      ? await requestPlaylistMove(current.entryId, gap)
      : await request("/playlist", jsonRequest("POST", { track_id: current.track.id, gap }), 409);
    if (response.status === 409) {
      const { detail } = await response.json();
      renderGaps(current, detail.order);
      showMessage(placementMessage, "The playlist order changed. Choose a position again.");
      refreshPlaylist().catch((error) => showMessage(playlistMessage, error.message));
      return;
    }
    placementPanel.close();
    showMessage(playlistMessage, moving ? "Playlist entry moved."
      : gap ? "Added to the playlist." : "Added to the end of the playlist.");
    refreshAfterPlaylistEdit(current.track.id, current.entryId);
  });
}

get("choose-position").addEventListener("click", choosePosition);
appendEntry.addEventListener("click", () => savePlacement(null));
placementGaps.addEventListener("change", () => { confirmPlacement.disabled = false; });
placementForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const selected = new FormData(placementForm).get("gap");
  if (selected !== null) {
    savePlacement(placement.gaps[Number(selected)]);
  }
});
closePlacement.addEventListener("click", () => placementPanel.close());
placementPanel.addEventListener("cancel", (event) => {
  if (placement?.busy) {
    event.preventDefault();
  }
});
placementPanel.addEventListener("close", () => { placement = null; });

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
  const response = await request("/library/usage?" + new URLSearchParams({ q: searchedQuery }));
  const tracks = await response.json();
  if (version !== searchVersion) {
    return;
  }
  showMessage(searchMessage, tracks.length ? "" : "No matching recordings.");
  searchResults.replaceChildren(...tracks.map(({ track, entry_count: count }) => {
    const item = document.createElement("li");
    const song = songText(track);
    const usage = document.createElement("span");
    usage.className = "song-artist";
    const label = document.createElement("span");
    setLabel(label, "Playlist entries");
    const number = document.createElement("bdi");
    number.textContent = count;
    usage.append(label, " ", number);
    song.append(usage);
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.append(actionButton(count ? "View playlist entries" : "View in Playlist Management", () => {
      openTab(get("playlist-tab"), track.id, count > 0);
    }));
    if (!count) {
      actions.append(actionButton("Delete recording", () => confirmRecordingDeletion(track)));
    }
    item.append(song, actions);
    return item;
  }));
}

function confirmRecordingDeletion(track) {
  deletingTrack = track;
  get("delete-recording-track").replaceChildren(songText(track));
  showMessage(deletionMessage);
  deletionPanel.showModal();
}

get("cancel-delete-recording").addEventListener("click", () => deletionPanel.close());
deletionPanel.addEventListener("cancel", (event) => {
  if (deletionControls.disabled) {
    event.preventDefault();
  }
});
deletionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const track = deletingTrack;
  withButton(deletionControls, deletionMessage, async () => {
    const response = await request(`/library/${track.id}?confirm=true`, { method: "DELETE" }, 409);
    if (response.status === 409) {
      showMessage(deletionMessage, errors.track_in_use);
      refreshLibraryResults();
      return;
    }
    deletionPanel.close();
    searchForm.elements.query.focus();
    showMessage(discoveryMessage, response.status === 202
      ? "The recording will be deleted when it finishes on air." : "Recording deleted.");
    for (const item of playlistRemovals.querySelectorAll(`[data-removed-track-id="${track.id}"]`)) {
      dismissUndo(item);
    }
    refreshAfterPlaylistEdit();
  });
});

function readyMessage(status) {
  return status === 201
    ? "Recording ready in the library."
    : "This recording is already in the library.";
}

function showReadyRecording(track, status, fallbackMessage) {
  if (!get("discovery-panel").hidden) {
    openTab(get("playlist-tab"), track.id);
    showMessage(playlistMessage, readyMessage(status));
  } else {
    showMessage(fallbackMessage, readyMessage(status));
  }
}

function youtubeRow(recording) {
  const item = document.createElement("li");
  const song = songText({ title: recording.title, artist: recording.channel });
  const details = document.createElement("bdi");
  const duration = recording.duration;
  const time = duration
    ? `${Math.floor(duration / 60)}:${String(duration % 60).padStart(2, "0")}`
    : "";
  details.className = "song-artist";
  details.textContent = time;
  song.append(details);

  const actions = document.createElement("div");
  actions.className = "discovery-actions";
  const path = `/youtube/${recording.id}`;
  actions.append(
    actionButton("Preview", (button) => withButton(button, youtubeMessage, async () => {
      showMessage(youtubeMessage, "Loading preview…");
      await request(`${path}/preview`, { method: "POST" });
      showMessage(youtubeMessage);
      if (!get("discovery-panel").hidden && button.isConnected && !previewPanel.open) {
        startPreview(recording, `${api}${path}/audio`);
      }
    })),
    actionButton("Import to library", (button) => withButton(button, youtubeMessage, async () => {
      showMessage(youtubeMessage, "Preparing recording…");
      const response = await request(`${path}/import`, { method: "POST" });
      showMessage(youtubeMessage);
      showReadyRecording(await response.json(), response.status, youtubeMessage);
    })),
  );
  item.append(song, actions);
  return item;
}

function renderYoutubePage(page = 1) {
  youtubePage = page;
  const pageCount = Math.ceil(youtubeMatches.length / youtubePageSize);
  const first = (page - 1) * youtubePageSize;
  const visible = youtubeMatches.slice(first, first + youtubePageSize);
  youtubeRecordings.replaceChildren(...visible.map(youtubeRow));
  youtubePagination.hidden = pageCount <= 1;
  youtubePrevious.disabled = page <= 1;
  youtubeNext.disabled = page >= pageCount;
  get("youtube-page").textContent = `${page} / ${pageCount}`;
}

for (const [button, step] of [[youtubePrevious, -1], [youtubeNext, 1]]) {
  button.addEventListener("click", () => {
    renderYoutubePage(youtubePage + step);
    get("youtube-results-heading").focus();
  });
}

async function searchYoutube(query) {
  const version = ++youtubeVersion;
  youtubeResults.hidden = !query;
  if (!query) {
    youtubeMatches = [];
    renderYoutubePage();
    return;
  }
  showMessage(youtubeMessage, "Searching YouTube…");
  try {
    const response = await request("/youtube?" + new URLSearchParams({ q: query }));
    const recordings = await response.json();
    if (version === youtubeVersion) {
      youtubeMatches = recordings;
      renderYoutubePage();
      showMessage(youtubeMessage, recordings.length ? "" : "No matching recordings.");
    }
  } catch (error) {
    if (version === youtubeVersion) {
      showMessage(youtubeMessage, error.message);
    }
  }
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  libraryResults.hidden = false;
  const query = new FormData(searchForm).get("query").trim();
  searchedQuery = query;
  withButton(event.submitter, searchMessage, () => Promise.all([searchLibrary(), searchYoutube(query)]));
});

recordingInput.addEventListener("change", () => {
  const files = recordingInput.files;
  uploadFilename.textContent = files.length > 1 ? files.length : (files[0]?.name ?? "");
  uploadSelectionLabel.hidden = files.length < 2;
});
uploadForm.addEventListener("reset", () => {
  uploadFilename.textContent = "";
  uploadSelectionLabel.hidden = true;
});

function uploadRow(file) {
  const item = document.createElement("li");
  const name = document.createElement("bdi");
  name.className = "song-title";
  name.textContent = file.name;
  const status = document.createElement("span");
  status.className = "song-artist";
  status.setAttribute("role", "status");
  setLabel(status, "Waiting to prepare…");
  const summary = document.createElement("div");
  summary.className = "song";
  summary.append(name, status);
  item.append(summary);
  return { file, item, status };
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = [...recordingInput.files];
  if (uploadController || !files.length) {
    return;
  }
  const rows = files.map(uploadRow);
  const controller = new AbortController();
  uploadController = controller;
  recordingInput.disabled = true;
  uploadButton.disabled = true;
  showMessage(discoveryMessage);
  uploadResults.replaceChildren(...rows.map(({ item }) => item));
  uploadResults.hidden = false;

  try {
    for (const { file, item, status } of rows) {
      if (controller.signal.aborted) {
        break;
      }
      if (file.size > 100_000_000) {
        setLabel(status, errors.upload_too_large);
        continue;
      }
      setLabel(status, "Preparing recording…");
      try {
        const response = await request("/library?" + new URLSearchParams({ filename: file.name }), {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
          signal: controller.signal,
        });
        const track = await response.json();
        if (controller.signal.aborted) {
          break;
        }
        setLabel(status, readyMessage(response.status));
        const actions = document.createElement("div");
        actions.className = "discovery-actions";
        actions.append(actionButton("View in Playlist Management", () => {
          openTab(get("playlist-tab"), track.id);
        }));
        item.append(actions);
        if (files.length === 1) {
          showReadyRecording(track, response.status, discoveryMessage);
        }
      } catch (error) {
        setLabel(status, error.message);
      }
    }
  } finally {
    uploadController = null;
    uploadForm.reset();
    recordingInput.disabled = false;
    uploadButton.disabled = false;
  }
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
    refreshLibraryResults();
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
  const trackId = previewTrack;
  previewTrack = null;
  preview.pause();
  preview.removeAttribute("src");
  preview.load();
  restoreLive();
  focusSong(trackId);
}

function previewFailed() {
  if (previewTrack !== null) {
    restoreLive();
    showMessage(previewMessage, "Could not play the preview.");
  }
}

async function startPreview(track, url = `${api}/library/${track.id}/preview`) {
  showMessage(previewMessage);
  previewName.textContent = track.title;
  previewPanel.showModal();
  previewTrack = track.id;
  preview.src = url;
  try {
    await preview.play();
  } catch (error) {
    if (error.name === "NotAllowedError" && previewTrack === track.id) {
      restoreLive();
    } else if (error.name !== "AbortError" && previewTrack === track.id) {
      previewFailed();
    }
  }
}

get("close-preview").addEventListener("click", () => previewPanel.close());
previewPanel.addEventListener("close", stopPreview);
preview.addEventListener("playing", () => {
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
