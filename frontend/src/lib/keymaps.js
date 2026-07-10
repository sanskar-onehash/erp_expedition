export const SHORTCUTS = [
  { id: 'shortcuts', label: 'Keyboard shortcuts', keys: ['?'], group: 'Help', description: 'Open this shortcuts list.' },
  { id: 'layers', label: 'Map', keys: ['M'], group: 'Workspace', description: 'Open or close the map layers panel.' },
  { id: 'search', label: 'Search', keys: ['Ctrl', 'F'], macKeys: ['⌘', 'F'], group: 'Workspace', description: 'Open or close map search.' },
  { id: 'settings', label: 'Settings', keys: ['Ctrl', ','], macKeys: ['⌘', ','], group: 'Workspace', description: 'Open or close settings.' },
  { id: 'hide-ui', label: 'Hide UI', keys: ['H'], group: 'Workspace', description: 'Hide or show map chrome.' },
  { id: 'layout', label: 'Customize layout', keys: ['L'], group: 'Workspace', description: 'Move map controls on the snapped layout grid.' },
  { id: 'basemap', label: 'Basemap', keys: ['B'], group: 'Map view', description: 'Open or close the basemap picker.' },
  { id: 'fit-visible', label: 'Fit visible features', keys: ['F'], group: 'Map view', description: 'Fit to data currently in view.' },
  { id: 'fit-all', label: 'Fit all enabled data', keys: ['Shift', 'F'], group: 'Map view', description: 'Fit to all enabled layer data.' },
  { id: 'tilt-reset', label: 'Reset tilt', keys: ['0'], group: 'Map view', description: 'Return the map to top-down view.' },
  { id: 'select', label: 'Select / pan', keys: ['V'], group: 'Drawing tools', description: 'Use the normal select and pan tool.' },
  { id: 'draw-shape', label: 'Draw shape', keys: ['D'], group: 'Drawing tools', description: 'Start drawing the selected shape.' },
  { id: 'choose-shape', label: 'Choose shape', keys: ['Shift', 'D'], group: 'Drawing tools', description: 'Open the shape picker.' },
  { id: 'measure-line', label: 'Measure distance', keys: ['R'], group: 'Drawing tools', description: 'Measure a line distance.' },
  { id: 'measure-area', label: 'Measure area', keys: ['Shift', 'R'], group: 'Drawing tools', description: 'Measure polygon area.' },
  { id: 'drawing-color', label: 'Drawing color', keys: ['C'], group: 'Drawing tools', description: 'Open drawing color controls.' },
  { id: 'zone-edit', label: 'Zone edit mode', keys: ['E'], group: 'Drawing tools', description: 'Toggle zone edit mode.' },
  { id: 'tilt-rotate', label: 'Tilt / rotate', keys: ['T'], group: 'Drawing tools', description: 'Open the tilt joystick.' },
  { id: 'undo-vertex', label: 'Undo vertex', keys: ['Z'], group: 'Drawing tools', description: 'Remove the last draft vertex while drawing.' },
  { id: 'finish-drawing', label: 'Finish drawing', keys: ['Enter'], group: 'Drawing tools', description: 'Finish the current drawing.' },
  { id: 'cancel-tool', label: 'Cancel tool', keys: ['Esc'], group: 'Drawing tools', description: 'Cancel drawing, measuring, or open tool popovers.' },
]

export const SHORTCUT_BY_ID = Object.fromEntries(SHORTCUTS.map((item) => [item.id, item]))

export function shortcutFor(id) {
  return SHORTCUT_BY_ID[id] || null
}

export function shortcutLabel(id) {
  const shortcut = shortcutFor(id)
  return shortcut ? formatShortcut(shortcut) : ''
}

export function formatShortcut(shortcut) {
  const keys = shortcut?.macKeys && isMacPlatform() ? shortcut.macKeys : shortcut?.keys
  return Array.isArray(keys) ? keys.join(' + ') : ''
}

export function isMacPlatform() {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || '')
}

export function isEditableTarget(target) {
  const el = typeof Element !== 'undefined' && target instanceof Element ? target : null
  if (!el) return false
  const tag = el.tagName
  return el.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}
