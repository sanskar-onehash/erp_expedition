import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { call } from '../api/client.js'
import { ICON_NAMES } from '../api/icons.js'

export const useIconsStore = defineStore('icons', () => {
  const custom = ref([])
  const canCreate = ref(false)
  const canManageGlobal = ref(false)
  const loaded = ref(false)
  const version = ref(0)

  const builtin = computed(() =>
    ICON_NAMES.map((id) => ({
      key: id,
      title: id,
      source: 'builtin',
      scope: 'Built-in',
      can_edit: false,
      can_delete: false,
    }))
  )
  const all = computed(() => [...builtin.value, ...custom.value])
  const byKey = computed(() => new Map(all.value.map((icon) => [icon.key, icon])))
  const personal = computed(() => custom.value.filter((icon) => icon.scope === 'Personal'))
  const global = computed(() => custom.value.filter((icon) => icon.scope === 'Global'))

  async function loadIcons({ force = false } = {}) {
    if (loaded.value && !force) return all.value
    const resp = await call('expedition.api.icon.list_icons')
    custom.value = Array.isArray(resp?.custom) ? resp.custom : []
    canCreate.value = !!resp?.can_create
    canManageGlobal.value = !!resp?.can_manage_global
    loaded.value = true
    version.value += 1
    return all.value
  }

  async function uploadIcon({ title, scope, svgText, imageDataUrl }) {
    const icon = await call('expedition.api.icon.upload_icon', {
      title,
      scope,
      svg_text: svgText,
      image_data_url: imageDataUrl,
    })
    custom.value = [...custom.value.filter((i) => i.key !== icon.key), icon]
    loaded.value = true
    version.value += 1
    return icon
  }

  async function updateIcon(iconName, { title, svgText, imageDataUrl, isActive } = {}) {
    const icon = await call('expedition.api.icon.update_icon', {
      icon_name: iconName,
      title,
      svg_text: svgText,
      image_data_url: imageDataUrl,
      is_active: isActive,
    })
    custom.value = [...custom.value.filter((i) => i.name !== iconName), icon]
    version.value += 1
    return icon
  }

  async function deleteIcon(iconName) {
    await call('expedition.api.icon.delete_icon', { icon_name: iconName })
    custom.value = custom.value.filter((i) => i.name !== iconName)
    version.value += 1
  }

  return {
    builtin,
    custom,
    personal,
    global,
    all,
    byKey,
    canCreate,
    canManageGlobal,
    loaded,
    version,
    loadIcons,
    uploadIcon,
    updateIcon,
    deleteIcon,
  }
})
