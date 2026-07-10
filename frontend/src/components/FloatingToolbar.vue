<script setup>
/**
 * FloatingToolbar — a vertical or horizontal column of icon buttons.
 *
 * Generic, presentational. Each button is a square hit area with a
 * centered SVG glyph. Hover reveals a label tooltip. Active buttons
 * (controlled via `:active` or a v-model) get a tinted bg.
 *
 * Size scale — read from `ui.prefs.toolbarSize`:
 *   xs  = 32px button, 14px icon
 *   s   = 36px button, 16px icon
 *   m   = 38px button, 18px icon (default)
 *   lg  = 44px button, 20px icon
 *   xlg = 50px button, 22px icon
 *
 * The toolbar reads the size once at mount via a CSS class; if the
 * user changes it in Settings, the buttons re-render at the new
 * size. SVG width/height are also driven by `--ft-size` so the icon
 * scales together with the button.
 *
 * Sits in a fixed corner via the parent. The toolbar itself is the
 * click target; the panel it triggers is owned by the parent.
 */
import { computed } from 'vue'
import { useUiStore } from '../state/ui.js'

const ui = useUiStore()

const props = defineProps({
  /** "vertical" (default, for left/right edges) or "horizontal" (top/bottom). */
  orientation: { type: String, default: 'vertical' },
  /**
   * Array of button specs. Each is:
   *   { id, label, glyph (svg path d string), active?: bool, badge?: string|number }
   * Glyph is a single 24x24 path. We use stroke style, no fill, so
   * the icon is recolorable via currentColor.
   */
  buttons: { type: Array, required: true },
})
const emit = defineEmits(['trigger'])

const cls = computed(() => {
  const size = ui.prefs.toolbarSize || 'm'
  return {
    ['ft ft--' + props.orientation + ' ft--' + size]: true,
    'ft--hint-hover': ui.shortcutAltDown,
    'ft--hint-all': ui.shortcutHintsAll,
  }
})
</script>

<template>
  <div :class="cls" role="toolbar" :aria-orientation="orientation">
    <button
      v-for="b in buttons"
      :key="b.id"
      type="button"
      class="ft__btn"
      :class="{ 'ft__btn--active': b.active }"
      :aria-label="b.label"
      :title="b.label"
      :aria-pressed="b.active ? 'true' : 'false'"
      @click="emit('trigger', b.id)"
    >
      <svg viewBox="0 0 24 24" class="ft__icon" aria-hidden="true">
        <path :d="b.glyph" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span v-if="b.badge != null && b.badge !== ''" class="ft__badge">{{ b.badge }}</span>
      <span v-if="b.shortcut" class="ft__key" aria-hidden="true">{{ b.shortcut }}</span>
    </button>
  </div>
</template>

<style scoped>
.ft {
  display: inline-flex;
  gap: 4px;
  background: rgba(11, 14, 20, 0.72);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 4px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
  pointer-events: auto;
}
.ft--vertical { flex-direction: column; }
.ft--horizontal { flex-direction: row; }

/* Size scale. Each modifier sets --ft-size (button edge) and
   --ft-icon (SVG size inside the button). The button + icon then
   read these vars, so swapping the modifier instantly resizes the
   whole toolbar without touching individual selectors.

   The .ft container adds 4px of padding + 4px gap, so a single
   button on screen reads as (--ft-size + 8) tall/wide. Sizes below
   are the *button* edge, picked so the visible pill ends up around
   the labelled tier (XS ~24, S ~32, M ~38, L ~46, XL ~54). */
.ft--xs  { --ft-size: 22px; --ft-icon: 12px; }
.ft--s   { --ft-size: 28px; --ft-icon: 14px; }
.ft--m   { --ft-size: 32px; --ft-icon: 16px; }
.ft--lg  { --ft-size: 40px; --ft-icon: 18px; }
.ft--xlg { --ft-size: 48px; --ft-icon: 20px; }

.ft__btn {
  position: relative;
  width: var(--ft-size, 32px);
  height: var(--ft-size, 32px);
  display: flex; align-items: center; justify-content: center;
  background: transparent;
  border: 0;
  border-radius: 8px;
  color: rgba(230, 232, 236, 0.88);
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease;
  font-family: inherit;
}
.ft__btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.ft__btn--active {
  background: rgba(59, 130, 246, 0.18);
  color: #93C5FD;
}
.ft__btn--active:hover {
  background: rgba(59, 130, 246, 0.26);
  color: #fff;
}
.ft__icon {
  width: var(--ft-icon, 16px);
  height: var(--ft-icon, 16px);
  flex: none;
}
.ft__badge {
  position: absolute;
  top: -10px; right: -10px;
  min-width: 16px; height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #3B82F6;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
  letter-spacing: 0;
  box-shadow: 0 0 0 2px rgba(11, 14, 20, 0.9);
  pointer-events: none;
}
.ft__key {
  position: absolute;
  left: calc(100% + 6px);
  top: 50%;
  transform: translateY(-50%) scale(0.96);
  opacity: 0;
  pointer-events: none;
  padding: 3px 6px;
  border-radius: 6px;
  background: rgba(8, 10, 15, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 10px;
  font-weight: 650;
  line-height: 1;
  letter-spacing: 0;
  white-space: nowrap;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.38);
  transition: opacity 80ms ease, transform 80ms ease;
  z-index: 3;
}
.ft--horizontal .ft__key {
  left: 50%;
  top: calc(100% + 6px);
  transform: translateX(-50%) scale(0.96);
}
.ft--hint-hover .ft__btn:hover .ft__key,
.ft--hint-all .ft__key {
  opacity: 1;
  transform: translateY(-50%) scale(1);
}
.ft--horizontal.ft--hint-hover .ft__btn:hover .ft__key,
.ft--horizontal.ft--hint-all .ft__key {
  transform: translateX(-50%) scale(1);
}
</style>
