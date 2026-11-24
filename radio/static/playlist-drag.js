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
    const bounds = list.getBoundingClientRect();
    if (!insideList(x, y, bounds)) {
      return null;
    }
    // Use the rows' layout positions so the animation cannot change the drop target.
    const localY = y - bounds.top;
    const before = drag.remaining.findIndex((item) => localY < item.offsetTop + item.offsetHeight / 2);
    return before === -1 ? drag.remaining.length : before;
  }

  function clearShiftedRows() {
    for (const row of drag.shifted) {
      row.classList.remove("playlist-shift-up", "playlist-shift-down");
    }
    drag.shifted = [];
  }

  function updateDrop() {
    const index = dropIndex(drag.x, drag.y);
    const height = drag.item.offsetHeight;
    drag.preview.style.translate = `0 ${drag.y - drag.offset}px`;
    if (index === drag.index && height === drag.height) {
      return;
    }
    clearShiftedRows();
    drag.index = index;
    drag.height = height;
    list.style.setProperty("--playlist-row-height", `${height}px`);
    const target = index ?? drag.originalIndex;
    const from = drag.originalIndex;
    let slotTop = drag.item.offsetTop;
    if (target < from) {
      slotTop = drag.remaining[target].offsetTop;
    } else if (target > from) {
      const previous = drag.remaining[target - 1];
      slotTop = previous.offsetTop + previous.offsetHeight - height;
    }
    drag.item.style.setProperty("--playlist-slot-offset", `${slotTop - drag.item.offsetTop}px`);
    drag.shifted = drag.remaining.slice(Math.min(from, target), Math.max(from, target));
    for (const row of drag.shifted) {
      row.classList.add(target < from ? "playlist-shift-down" : "playlist-shift-up");
    }
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
    clearShiftedRows();
    drag = null;
    if (preview) {
      draggedGrip = grip;
      cancelAnimationFrame(frame);
      preview.remove();
      item.classList.remove("playlist-drag-source");
      item.style.removeProperty("--playlist-slot-offset");
      list.classList.remove("playlist-drag-active");
      list.style.removeProperty("--playlist-row-height");
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
      shifted: [],
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
      list.classList.add("playlist-drag-active");
      drag.item.classList.add("playlist-drag-source");
      drag.frame = requestAnimationFrame(scrollNearEdge);
    }
    updateDrop();
  });

  list.addEventListener("pointerup", async (event) => {
    if (drag?.pointerId !== event.pointerId) {
      return;
    }
    const { grip, item, originalIndex, remaining, preview } = drag;
    const index = preview ? dropIndex(event.clientX, event.clientY) : null;
    cancelDrag();
    if (index === null || index === originalIndex) {
      return;
    }
    const entryId = (row) => row ? Number(row.dataset.entryId) : null;
    const originalNext = item.nextElementSibling;
    const before = remaining[index] ?? null;
    const hadFocus = document.activeElement === grip;
    saving = true;
    list.insertBefore(item, before);
    if (hadFocus) {
      grip.focus({ preventScroll: true });
    }
    let moved = false;
    try {
      moved = await moveEntry(entryId(item), {
        after_entry_id: entryId(remaining[index - 1]),
        before_entry_id: entryId(before),
      });
    } finally {
      if (!moved && item.parentElement === list) {
        const restoreFocus = document.activeElement === grip;
        list.insertBefore(item, originalNext);
        if (restoreFocus) {
          grip.focus({ preventScroll: true });
        }
      }
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
