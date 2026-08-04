<script setup>
import { nextTick, ref, watch } from "vue";
import { call } from "../../api/client.js";

const props = defineProps({
  modelValue: { type: String, default: "" },
  files: { type: Array, default: () => [] },
  busy: Boolean,
  editing: Boolean,
});
const emit = defineEmits([
  "update:modelValue",
  "update:files",
  "submit",
  "cancel",
]);

const editor = ref(null);
const fileInput = ref(null);
const imageInput = ref(null);
const mentions = ref([]);
const mentionOpen = ref(false);
const mentionIndex = ref(0);
let mentionTimer = null;
let savedRange = null;

watch(
  () => props.modelValue,
  async (value) => {
    await nextTick();
    if (editor.value && editor.value.innerHTML !== (value || ""))
      editor.value.innerHTML = value || "";
  },
  { immediate: true },
);

function sync() {
  emit("update:modelValue", editor.value?.innerHTML || "");
  detectMention();
}

function rememberRange() {
  const selection = window.getSelection();
  if (selection?.rangeCount && editor.value?.contains(selection.anchorNode))
    savedRange = selection.getRangeAt(0).cloneRange();
}

function command(name, value = null) {
  editor.value?.focus();
  if (savedRange) {
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(savedRange);
  }
  document.execCommand(name, false, value);
  sync();
  rememberRange();
}

function addLink() {
  const url = window.prompt("Link URL");
  if (url) command("createLink", url);
}

function block(format) {
  command("formatBlock", format);
}
function toggleDirection() {
  if (!editor.value) return;
  editor.value.style.direction =
    editor.value.style.direction === "rtl" ? "ltr" : "rtl";
  editor.value.focus();
}

function textBeforeCaret() {
  const selection = window.getSelection();
  if (!selection?.rangeCount || !editor.value?.contains(selection.anchorNode))
    return "";
  const range = selection.getRangeAt(0).cloneRange();
  range.selectNodeContents(editor.value);
  range.setEnd(selection.anchorNode, selection.anchorOffset);
  return range.toString();
}

function detectMention() {
  rememberRange();
  const match = textBeforeCaret().match(/(?:^|\s)@([A-Za-z0-9_]*)$/);
  if (!match) {
    mentionOpen.value = false;
    return;
  }
  const term = match[1];
  window.clearTimeout(mentionTimer);
  mentionTimer = window.setTimeout(async () => {
    try {
      mentions.value =
        (await call("expedition.api.comment.search_mentions", {
          search_term: term,
        })) || [];
      mentionIndex.value = 0;
      mentionOpen.value = mentions.value.length > 0;
    } catch {
      mentionOpen.value = false;
    }
  }, 220);
}

function chooseMention(item) {
  if (!savedRange) return;
  const range = savedRange.cloneRange();
  const node = range.startContainer;
  if (node.nodeType === Node.TEXT_NODE) {
    const before = node.data.slice(0, range.startOffset);
    const match = before.match(/@([A-Za-z0-9_]*)$/);
    if (match) range.setStart(node, range.startOffset - match[0].length);
  }
  range.deleteContents();
  const span = document.createElement("span");
  span.className = "mention";
  span.dataset.id = item.id;
  span.dataset.value = item.value;
  span.dataset.isGroup = item.is_group ? "true" : "false";
  span.contentEditable = "false";
  span.textContent = `@${item.value}`;
  range.insertNode(span);
  const space = document.createTextNode("\u00a0");
  span.after(space);
  range.setStartAfter(space);
  range.collapse(true);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  mentionOpen.value = false;
  sync();
}

function onKeydown(event) {
  if (mentionOpen.value) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      mentionIndex.value =
        (mentionIndex.value + delta + mentions.value.length) %
        mentions.value.length;
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      chooseMention(mentions.value[mentionIndex.value]);
      return;
    }
    if (event.key === "Escape") {
      event.stopPropagation();
      mentionOpen.value = false;
      return;
    }
  }
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    emit("submit");
  }
}

function addFiles(fileList) {
  const next = [...props.files];
  for (const file of Array.from(fileList || [])) {
    if (
      !next.some(
        (existing) =>
          existing.name === file.name && existing.size === file.size,
      )
    )
      next.push(file);
  }
  emit("update:files", next);
}

function addImages(fileList) {
  for (const file of Array.from(fileList || [])) {
    const reader = new FileReader();
    reader.onload = () => {
      editor.value?.focus();
      command("insertImage", reader.result);
    };
    reader.readAsDataURL(file);
  }
}

function onPaste(event) {
  const images = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (images.length) {
    event.preventDefault();
    addImages(images);
  }
}

function removeFile(index) {
  emit(
    "update:files",
    props.files.filter((_, i) => i !== index),
  );
}
</script>

<template>
  <div class="cc" :class="{ 'cc--busy': busy }">
    <div class="cc__toolbar" aria-label="Comment formatting">
      <button type="button" title="Bold" @mousedown.prevent="command('bold')">
        <b>B</b>
      </button>
      <button
        type="button"
        title="Italic"
        @mousedown.prevent="command('italic')"
      >
        <i>I</i>
      </button>
      <button
        type="button"
        title="Underline"
        @mousedown.prevent="command('underline')"
      >
        <u>U</u>
      </button>
      <button
        type="button"
        title="Strike"
        @mousedown.prevent="command('strikeThrough')"
      >
        <s>S</s>
      </button>
      <button
        type="button"
        title="Quote"
        @mousedown.prevent="block('blockquote')"
      >
        ❝
      </button>
      <button type="button" title="Code" @mousedown.prevent="block('pre')">
        &lt;/&gt;
      </button>
      <button
        type="button"
        title="Bulleted list"
        @mousedown.prevent="command('insertUnorderedList')"
      >
        •≡
      </button>
      <button
        type="button"
        title="Numbered list"
        @mousedown.prevent="command('insertOrderedList')"
      >
        1.
      </button>
      <button
        type="button"
        title="Align left"
        @mousedown.prevent="command('justifyLeft')"
      >
        ≡
      </button>
      <button
        type="button"
        title="Align center"
        @mousedown.prevent="command('justifyCenter')"
      >
        ≣
      </button>
      <button
        type="button"
        title="Right-to-left"
        @mousedown.prevent="toggleDirection"
      >
        RTL
      </button>
      <button type="button" title="Link" @mousedown.prevent="addLink">↗</button>
      <button
        type="button"
        title="Inline image"
        @mousedown.prevent="imageInput?.click()"
      >
        ▧
      </button>
      <button
        type="button"
        title="Attach files"
        @mousedown.prevent="fileInput?.click()"
      >
        ⌕
      </button>
      <button
        type="button"
        title="Clear formatting"
        @mousedown.prevent="command('removeFormat')"
      >
        Tx
      </button>
      <input
        ref="imageInput"
        hidden
        type="file"
        accept="image/*"
        multiple
        @change="
          (e) => {
            addImages(e.target.files);
            e.target.value = '';
          }
        "
      />
      <input
        ref="fileInput"
        hidden
        type="file"
        multiple
        @change="
          (e) => {
            addFiles(e.target.files);
            e.target.value = '';
          }
        "
      />
    </div>
    <div class="cc__editor-wrap">
      <div
        ref="editor"
        class="cc__editor"
        contenteditable="true"
        role="textbox"
        aria-multiline="true"
        data-placeholder="Type a comment — use @ to mention someone"
        @input="sync"
        @keyup="rememberRange"
        @mouseup="rememberRange"
        @keydown="onKeydown"
        @paste="onPaste"
      />
      <div v-if="mentionOpen" class="cc__mentions">
        <button
          v-for="(item, index) in mentions"
          :key="item.id + ':' + !!item.is_group"
          type="button"
          :class="{ active: index === mentionIndex }"
          @mousedown.prevent="chooseMention(item)"
        >
          <span>{{ item.value }}</span
          ><small>{{ item.is_group ? "Group" : item.id }}</small>
        </button>
      </div>
    </div>
    <div v-if="files.length" class="cc__files">
      <span v-for="(file, index) in files" :key="file.name + index">
        {{ file.name }}
        <button
          type="button"
          aria-label="Remove file"
          @click="removeFile(index)"
        >
          ×
        </button>
      </span>
    </div>
    <div class="cc__footer">
      <span>Ctrl/⌘ + Enter</span>
      <div>
        <button
          v-if="editing"
          type="button"
          class="cc__cancel"
          :disabled="busy"
          @click="emit('cancel')"
        >
          Dismiss
        </button>
        <button
          type="button"
          class="cc__submit"
          :disabled="busy"
          @click="emit('submit')"
        >
          {{ busy ? "Saving…" : editing ? "Save" : "Comment" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cc {
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.025);
  overflow: visible;
}
.cc--busy {
  opacity: 0.72;
}
.cc__toolbar {
  display: flex;
  gap: 2px;
  padding: 5px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  overflow-x: auto;
}
.cc__toolbar button {
  flex: none;
  width: 27px;
  height: 25px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: rgba(230, 232, 236, 0.68);
  font-size: 11px;
}
.cc__toolbar button:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.cc__editor-wrap {
  position: relative;
}
.cc__editor {
  min-height: 68px;
  max-height: 170px;
  overflow: auto;
  padding: 10px 11px;
  color: #e6e8ec;
  font-size: 12px;
  line-height: 1.55;
  outline: 0;
}
.cc__editor:empty:before {
  content: attr(data-placeholder);
  color: rgba(230, 232, 236, 0.32);
  pointer-events: none;
}
.cc__editor :deep(img) {
  max-width: 100%;
  border-radius: 7px;
}
.cc__editor :deep(.mention) {
  color: #f7b84b;
  background: rgba(245, 158, 11, 0.12);
  border-radius: 4px;
  padding: 1px 3px;
}
.cc__mentions {
  position: absolute;
  left: 8px;
  bottom: 8px;
  width: min(300px, calc(100% - 16px));
  max-height: 190px;
  overflow: auto;
  padding: 4px;
  background: #151a22;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  box-shadow: 0 12px 34px rgba(0, 0, 0, 0.45);
  z-index: 5;
}
.cc__mentions button {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 8px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #e6e8ec;
  text-align: left;
}
.cc__mentions button.active,
.cc__mentions button:hover {
  background: rgba(245, 158, 11, 0.12);
}
.cc__mentions small {
  color: rgba(230, 232, 236, 0.4);
  overflow: hidden;
  text-overflow: ellipsis;
}
.cc__files {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding: 0 8px 7px;
}
.cc__files span {
  max-width: 100%;
  padding: 4px 6px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.06);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cc__files button {
  border: 0;
  background: none;
  color: inherit;
}
.cc__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 7px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 9px;
  color: rgba(230, 232, 236, 0.35);
}
.cc__footer > div {
  display: flex;
  gap: 5px;
}
.cc__submit,
.cc__cancel {
  padding: 5px 10px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  color: #e6e8ec;
  background: rgba(255, 255, 255, 0.05);
  font-size: 10px;
}
.cc__submit {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #14100a;
  font-weight: 650;
}
</style>
