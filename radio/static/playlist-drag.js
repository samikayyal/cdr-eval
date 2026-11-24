const DRAG_THRESHOLD = 6;
const EDGE_SCROLL_MARGIN = 56;
const DROP_MARGIN = 24;

function insideList(x, y, bounds) {
  return x >= bounds.left - DROP_MARGIN && x <= bounds.right + DROP_MARGIN
    && y >= bounds.top - DROP_MARGIN && y <= bounds.bottom + DROP_MARGIN;
}

export function enablePlaylistDrag(list, moveEntry) {
  let drag = null;
  let draggedGrip = null;
  let saving = false;

  function dropIndex(x, y) {
    if (!insideList(x, y, list.getBoundingClientRect())) {
      return null;
    }
    const before = drag.remaining.findIndex((item) => {
      const { top, height } = item.getBoundingClientRect();
      return y < top + height / 2;
    });
    return before === -1 ? drag.remaining.length : before;
  }

  function clearIndicator() {
    drag.indicator?.classList.remove("playlist-drop-before", "playlist-drop-after");
    drag.indicator = null;
  }

  function updateDrop() {
    drag.preview.style.top = `${drag.y - drag.offset}px`;
    const index = dropIndex(drag.x, drag.y);
    if (index === drag.index) {
      return;
    }
    clearIndicator();
    drag.index = index;
    if (index === null || index === drag.originalIndex) {
      return;
    }
    const before = drag.remaining[index];
    drag.indicator = before ?? drag.remaining.at(-1);
    drag.indicator.classList.add(before ? "playlist-drop-before" : "playlist-drop-after");
  }

  function scrollNearEdge() {
    const bounds = list.getBoundingClientRect();
    const { x, y } = drag;
    if (insideList(x, y, bounds)) {
      const height = document.documentElement.clientHeight;
      const up = Math.min(0, y - EDGE_SCROLL_MARGIN);
      const down = Math.max(0, y - height + EDGE_SCROLL_MARGIN);
      const speed = (bounds.top < 0 ? up : 0) + (bounds.bottom > height ? down : 0);
      if (speed) {
        window.scrollBy(0, Math.sign(speed) * Math.min(18, Math.ceil(Math.abs(speed) / 3)));
        updateDrop();
      }
    }
    drag.frame = requestAnimationFrame(scrollNearEdge);
  }

  function cancelDrag() {
    if (!drag) {
      return;
    }
    const { grip, pointerId, item, preview, frame } = drag;
    clearIndicator();
    drag = null;
    if (preview) {
      draggedGrip = grip;
      cancelAnimationFrame(frame);
      preview.remove();
      item.classList.remove("playlist-dragging");
    }
    if (grip.hasPointerCapture(pointerId)) {
      grip.releasePointerCapture(pointerId);
    }
  }

  list.addEventListener("pointerdown", (event) => {
    const grip = event.target.closest(".playlist-grip");
    if (!grip || !event.isPrimary || event.button !== 0 || drag) {
      return;
    }
    draggedGrip = null;
    if (saving) {
      return;
    }
    const item = grip.closest("li");
    const rows = [...list.children];
    const originalIndex = rows.indexOf(item);
    rows.splice(originalIndex, 1);
    drag = {
      grip, item, originalIndex, remaining: rows, pointerId: event.pointerId,
      startX: event.clientX, startY: event.clientY, index: null,
    };
    grip.setPointerCapture(event.pointerId);
  });

  list.addEventListener("pointermove", (event) => {
    if (drag?.pointerId !== event.pointerId) {
      return;
    }
    drag.x = event.clientX;
    drag.y = event.clientY;
    if (!drag.preview) {
      if (Math.hypot(drag.x - drag.startX, drag.y - drag.startY) < DRAG_THRESHOLD) {
        return;
      }
      const { left, top, width } = drag.item.getBoundingClientRect();
      drag.offset = drag.startY - top;
      drag.preview = drag.item.querySelector(".song").cloneNode(true);
      drag.preview.classList.add("playlist-drag-preview");
      drag.preview.setAttribute("aria-hidden", "true");
      drag.preview.style.left = `${left}px`;
      drag.preview.style.width = `${width}px`;
      document.body.append(drag.preview);
      drag.item.classList.add("playlist-dragging");
      drag.frame = requestAnimationFrame(scrollNearEdge);
    }
    updateDrop();
  });

  list.addEventListener("pointerup", async (event) => {
    if (drag?.pointerId !== event.pointerId) {
      return;
    }
    const { item, originalIndex, remaining, preview } = drag;
    const index = preview ? dropIndex(event.clientX, event.clientY) : null;
    cancelDrag();
    if (index === null || index === originalIndex) {
      return;
    }
    const entryId = (row) => row ? Number(row.dataset.entryId) : null;
    saving = true;
    try {
      await moveEntry(entryId(item), {
        after_entry_id: entryId(remaining[index - 1]),
        before_entry_id: entryId(remaining[index]),
      });
    } finally {
      saving = false;
    }
  });

  for (const type of ["pointercancel", "lostpointercapture"]) {
    list.addEventListener(type, (event) => {
      if (drag?.pointerId === event.pointerId) {
        cancelDrag();
      }
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drag) {
      cancelDrag();
    }
  });
  list.addEventListener("click", (event) => {
    const grip = event.target.closest(".playlist-grip");
    if (grip && (saving || (event.detail && grip === draggedGrip))) {
      event.preventDefault();
      event.stopPropagation();
      draggedGrip = null;
    }
  }, true);

  return cancelDrag;
}
