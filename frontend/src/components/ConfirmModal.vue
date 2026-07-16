<script setup>
/**
 * ConfirmModal — global in-app confirm dialog.
 *
 * Renders when `ui.confirmRequest` is non-null. Calls to `ui.ask(...)`
 * from anywhere return a Promise<boolean> that resolves when the user
 * clicks a button. Replaces `window.confirm` / `window.alert`.
 *
 * Mounted once at the App.vue root, above all other chrome. Backdrop
 * is heavier than the settings modal so the user reads it as a
 * separate yes/no decision. Self-click on the backdrop = cancel.
 */
import { onMounted, onBeforeUnmount } from 'vue'
import { useUiStore } from '../state/ui.js'

const ui = useUiStore()

function onKeydown(e) {
  if (!ui.confirmRequest) return
  if (e.key === 'Escape') {
    e.preventDefault()
    ui.resolveConfirm(false)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    ui.resolveConfirm(true)
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div
    v-if="ui.confirmRequest"
    class="cm"
    role="alertdialog"
    :aria-label="ui.confirmRequest.title"
    @mousedown.self="ui.resolveConfirm(false)"
  >
    <div class="cm__card" @mousedown.stop>
      <div class="cm__title">{{ ui.confirmRequest.title }}</div>
      <div v-if="ui.confirmRequest.body" class="cm__body">{{ ui.confirmRequest.body }}</div>
      <div class="cm__actions">
        <button
          v-if="ui.confirmRequest.cancelLabel"
          class="cm__btn cm__btn--ghost"
          type="button"
          @click="ui.resolveConfirm(false)"
        >{{ ui.confirmRequest.cancelLabel }}</button>
        <button
          class="cm__btn"
          :class="ui.confirmRequest.destructive ? 'cm__btn--danger' : 'cm__btn--primary'"
          type="button"
          @click="ui.resolveConfirm(true)"
        >{{ ui.confirmRequest.confirmLabel }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cm {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(11, 14, 20, 0.55);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
  animation: cm-fade 120ms ease;
}
@keyframes cm-fade {
  from { opacity: 0; }
  to   { opacity: 1; }
}

.cm__card {
  width: min(380px, calc(100% - 32px));
  background: rgba(11, 14, 20, 0.96);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 12px;
  padding: 18px 18px 14px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
  pointer-events: auto;
  animation: cm-pop 140ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes cm-pop {
  from { transform: translateY(8px) scale(0.98); opacity: 0; }
  to   { transform: translateY(0) scale(1); opacity: 1; }
}

.cm__title {
  font-size: 13px;
  font-weight: 500;
  color: #E6E8EC;
}
.cm__body {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.5;
  color: rgba(230, 232, 236, 0.7);
}
.cm__actions {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.cm__btn {
  font-family: inherit;
  font-size: 11px;
  font-weight: 500;
  padding: 7px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease;
}
.cm__btn--ghost {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: rgba(230, 232, 236, 0.85);
}
.cm__btn--ghost:hover { background: rgba(255, 255, 255, 0.06); color: #fff; }
.cm__btn--primary {
  background: #3B82F6;
  border: 1px solid #3B82F6;
  color: #fff;
}
.cm__btn--primary:hover { background: #2563EB; border-color: #2563EB; }
.cm__btn--danger {
  background: rgba(239, 68, 68, 0.18);
  border: 1px solid rgba(239, 68, 68, 0.45);
  color: #FCA5A5;
}
.cm__btn--danger:hover { background: rgba(239, 68, 68, 0.28); color: #fff; }
</style>
