<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { call, upload } from "../../api/client.js";
import { deskDocRoute } from "../../lib/desk.js";
import { useUiStore } from "../../state/ui.js";
import ActivityPane from "./ActivityPane.vue";

const props = defineProps({
  doctype: { type: String, required: true },
  name: { type: String, required: true },
});
const emit = defineEmits(["count"]);
const ui = useUiStore();

const events = ref([]);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const expanded = ref(false);
const visibleLimit = ref(25);
const hasMoreCommunications = ref(false);
const draft = ref("");
const draftFiles = ref([]);
const editingName = ref("");
const retainedFiles = ref([]);
const modalEl = ref(null);
let requestVersion = 0;
let refreshTimer = null;
let socket = null;
let subscribed = null;
let pollTimer = null;

function loadSocketIo() {
  if (window.io) return Promise.resolve(window.io);
  if (window.__expeditionSocketIoPromise)
    return window.__expeditionSocketIoPromise;
  window.__expeditionSocketIoPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/socket.io/socket.io.js";
    script.async = true;
    script.onload = () =>
      window.io
        ? resolve(window.io)
        : reject(new Error("Socket.IO client unavailable"));
    script.onerror = () => reject(new Error("Could not load Socket.IO client"));
    document.head.appendChild(script);
  });
  return window.__expeditionSocketIoPromise;
}

const compactEvents = computed(() => events.value.slice(0, 5));
const expandedEvents = computed(() =>
  events.value.slice(0, visibleLimit.value),
);
const canLoadMore = computed(
  () => visibleLimit.value < events.value.length || hasMoreCommunications.value,
);

function applyResult(result) {
  if (!result) return;
  events.value = result.events || [];
  hasMoreCommunications.value = !!result.has_more_communications;
  emit("count", result.comment_count || 0);
}

async function load({ quiet = false } = {}) {
  const version = ++requestVersion;
  if (!quiet) loading.value = true;
  error.value = "";
  try {
    const result = await call("expedition.api.comment.get_activity", {
      reference_doctype: props.doctype,
      reference_name: props.name,
    });
    if (version === requestVersion) applyResult(result);
  } catch (e) {
    if (version === requestVersion) error.value = e.message || String(e);
  } finally {
    if (version === requestVersion) loading.value = false;
  }
}

function resetEditor() {
  draft.value = "";
  draftFiles.value = [];
  editingName.value = "";
  retainedFiles.value = [];
}

function textContent(html) {
  const el = document.createElement("div");
  el.innerHTML = html || "";
  return (el.textContent || "").trim();
}

async function submit() {
  if (busy.value) return;
  if (
    !textContent(draft.value) &&
    !draft.value.toLowerCase().includes("<img") &&
    !draftFiles.value.length
  ) {
    error.value = "Write a comment or attach a file first.";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const result = editingName.value
      ? await upload(
          "expedition.api.comment.update",
          {
            name: editingName.value,
            content: draft.value,
            retained_files: retainedFiles.value.map((file) => file.name),
          },
          draftFiles.value,
        )
      : await upload(
          "expedition.api.comment.add",
          {
            reference_doctype: props.doctype,
            reference_name: props.name,
            content: draft.value || "<p>Attached files</p>",
          },
          draftFiles.value,
        );
    applyResult(result);
    resetEditor();
  } catch (e) {
    error.value = e.message || String(e);
  } finally {
    busy.value = false;
  }
}

function editComment(event) {
  editingName.value = event.name;
  draft.value = event.content_html || "";
  retainedFiles.value = [...(event.attachments || [])];
  draftFiles.value = [];
}

async function deleteComment(event) {
  const ok = await ui.ask({
    title: "Delete comment?",
    body: "The comment and files attached to it will be removed.",
    confirmLabel: "Delete",
    destructive: true,
  });
  if (!ok) return;
  busy.value = true;
  error.value = "";
  try {
    applyResult(
      await call("expedition.api.comment.delete", { name: event.name }),
    );
    if (editingName.value === event.name) resetEditor();
  } catch (e) {
    error.value = e.message || String(e);
  } finally {
    busy.value = false;
  }
}

async function copyLink(event) {
  const route = deskDocRoute(props.doctype, props.name);
  const url = `${new URL(route, window.location.origin)}#comment-${event.name}`;
  try {
    await navigator.clipboard.writeText(url);
  } catch {
    error.value = "Could not copy the comment link.";
  }
}

async function loadMore() {
  if (visibleLimit.value < events.value.length) {
    visibleLimit.value += 25;
    return;
  }
  if (!hasMoreCommunications.value || loading.value) return;
  loading.value = true;
  try {
    const communicationCount = events.value.filter((row) =>
      ["communication", "automated_message"].includes(row.kind),
    ).length;
    const more =
      (await call("expedition.api.comment.get_more_communications", {
        reference_doctype: props.doctype,
        reference_name: props.name,
        start: Math.max(communicationCount - 1, 0),
        limit: 21,
      })) || [];
    const seen = new Set(events.value.map((row) => row.id));
    const additions = more.filter((row) => !seen.has(row.id));
    events.value = [...events.value, ...additions].sort((a, b) =>
      String(b.creation || "").localeCompare(String(a.creation || "")),
    );
    hasMoreCommunications.value = more.length >= 21;
    visibleLimit.value += 25;
  } catch (e) {
    error.value = e.message || String(e);
  } finally {
    loading.value = false;
  }
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => load({ quiet: true }), 180);
}

async function connectRealtime() {
  disconnectRealtime();
  const site = window.expeditionSession?.site;
  if (!site || !props.doctype || !props.name) return;
  try {
    const socketIo = await loadSocketIo();
    socket = socketIo(`${window.location.origin}/${site}`, {
      withCredentials: true,
      reconnectionAttempts: 3,
    });
    subscribed = [props.doctype, props.name];
    socket.on("connect", () => socket?.emit("doc_subscribe", ...subscribed));
    socket.on("docinfo_update", scheduleRefresh);
    socket.on("doc_update", scheduleRefresh);
  } catch (e) {
    console.warn(
      "[expedition] realtime comments unavailable; using polling",
      e,
    );
    pollTimer = window.setInterval(() => load({ quiet: true }), 30000);
  }
}

function disconnectRealtime() {
  if (socket && subscribed) socket.emit("doc_unsubscribe", ...subscribed);
  socket?.disconnect();
  socket = null;
  subscribed = null;
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = null;
}

watch(
  () => [props.doctype, props.name],
  () => {
    visibleLimit.value = 25;
    resetEditor();
    load();
    connectRealtime();
  },
  { immediate: true },
);

watch(expanded, async (open) => {
  if (!open) return;
  await nextTick();
  modalEl.value?.focus();
});

function onModalKey(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    expanded.value = false;
    return;
  }
  if (event.key !== "Tab" || !modalEl.value) return;
  const focusable = Array.from(
    modalEl.value.querySelectorAll(
      'button:not([disabled]),a[href],input:not([disabled]),[contenteditable="true"],[tabindex]:not([tabindex="-1"])',
    ),
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

onBeforeUnmount(() => {
  requestVersion++;
  window.clearTimeout(refreshTimer);
  disconnectRealtime();
});
</script>

<template>
  <ActivityPane
    v-if="!expanded"
    :events="compactEvents"
    :loading="loading"
    :error="error"
    :busy="busy"
    :draft="draft"
    :draft-files="draftFiles"
    :editing-name="editingName"
    :retained-files="retainedFiles"
    :can-load-more="events.length > 5"
    @expand="expanded = true"
    @refresh="load"
    @submit="submit"
    @edit="editComment"
    @delete="deleteComment"
    @copy="copyLink"
    @cancel-edit="resetEditor"
    @load-more="expanded = true"
    @update:draft="(value) => (draft = value)"
    @update:draft-files="(value) => (draftFiles = value)"
    @remove-retained="
      (name) =>
        (retainedFiles = retainedFiles.filter((file) => file.name !== name))
    "
  />

  <Teleport to="body">
    <div v-if="expanded" class="ra__overlay" @mousedown.self="expanded = false">
      <div
        ref="modalEl"
        class="ra__modal"
        role="dialog"
        aria-modal="true"
        :aria-label="`Comments for ${doctype} ${name}`"
        tabindex="-1"
        @mousedown.stop
        @keydown="onModalKey"
      >
        <ActivityPane
          expanded
          title="Comments & activity"
          :events="expandedEvents"
          :loading="loading"
          :error="error"
          :busy="busy"
          :draft="draft"
          :draft-files="draftFiles"
          :editing-name="editingName"
          :retained-files="retainedFiles"
          :can-load-more="canLoadMore"
          @close="expanded = false"
          @refresh="load"
          @submit="submit"
          @edit="editComment"
          @delete="deleteComment"
          @copy="copyLink"
          @cancel-edit="resetEditor"
          @load-more="loadMore"
          @update:draft="(value) => (draft = value)"
          @update:draft-files="(value) => (draftFiles = value)"
          @remove-retained="
            (name) =>
              (retainedFiles = retainedFiles.filter(
                (file) => file.name !== name,
              ))
          "
        />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ra__overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 16px;
  background: rgba(5, 7, 11, 0.58);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  animation: ra-fade 0.14s ease;
  font-family:
    "Inter",
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
  font-feature-settings: "cv11", "ss01", "ss03";
}
.ra__modal {
  width: min(760px, calc(100vw - 32px));
  height: min(680px, calc(100vh - 32px));
  padding: 15px;
  background: rgba(11, 14, 20, 0.97);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 15px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.62);
  overflow: hidden;
}
.ra__modal :deep(*) {
  font-family: inherit;
}
.ra__modal :deep(.ap) {
  height: 100%;
}
.ra__modal :deep(.ap__composer) {
  flex: none;
}
.ra__modal :deep(.ap__feed) {
  flex: 1;
}
@keyframes ra-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@media (max-width: 640px) {
  .ra__overlay {
    padding: 0;
  }
  .ra__modal {
    width: 100vw;
    height: 100dvh;
    border-radius: 0;
    border: 0;
    padding: 12px;
  }
}
</style>
