<script setup>
import CommentComposer from './CommentComposer.vue'

defineProps({
  title: { type: String, default: 'Comments' },
  events: { type: Array, default: () => [] },
  loading: Boolean,
  error: { type: String, default: '' },
  busy: Boolean,
  expanded: Boolean,
  draft: { type: String, default: '' },
  draftFiles: { type: Array, default: () => [] },
  editingName: { type: String, default: '' },
  retainedFiles: { type: Array, default: () => [] },
  canLoadMore: Boolean,
})
const emit = defineEmits([
  'expand', 'close', 'refresh', 'submit', 'edit', 'delete', 'copy', 'cancel-edit', 'load-more',
  'update:draft', 'update:draftFiles', 'remove-retained',
])

function kindLabel(kind) {
  return ({
    comment: 'commented', communication: 'sent an email', automated_message: 'automated message',
    assignment: 'assignment', share: 'sharing', workflow: 'workflow', attachment: 'attachment',
    info: 'update', like: 'liked', version: 'edited', view: 'viewed', energy: 'energy points',
    milestone: 'milestone', custom: 'activity', created: 'created', modified: 'modified',
  })[kind] || kind.replace(/_/g, ' ')
}

function formatWhen(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const delta = Date.now() - date.getTime()
  const minutes = Math.floor(delta / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return date.toLocaleString()
}
</script>

<template>
  <section class="ap" :class="{ 'ap--expanded': expanded }">
    <header class="ap__head">
      <div><strong>{{ title }}</strong><small>Desk activity and record updates</small></div>
      <div class="ap__head-actions">
        <button type="button" title="Refresh" :disabled="loading" @click="emit('refresh')">↻</button>
        <button v-if="!expanded" type="button" title="Expand comments" @click="emit('expand')">↗</button>
        <button v-else type="button" title="Close" @click="emit('close')">×</button>
      </div>
    </header>

    <div class="ap__composer">
      <div v-if="editingName" class="ap__editing">Editing comment</div>
      <CommentComposer
        :model-value="draft"
        :files="draftFiles"
        :busy="busy"
        :editing="!!editingName"
        @update:model-value="value => emit('update:draft', value)"
        @update:files="value => emit('update:draftFiles', value)"
        @submit="emit('submit')"
        @cancel="emit('cancel-edit')"
      />
      <div v-if="editingName && retainedFiles.length" class="ap__retained">
        <span v-for="file in retainedFiles" :key="file.name">
          {{ file.file_name }}
          <button type="button" title="Remove attachment" @click="emit('remove-retained', file.name)">×</button>
        </span>
      </div>
    </div>

    <p v-if="error" class="ap__state ap__state--error">{{ error }}</p>
    <p v-else-if="loading && !events.length" class="ap__state">Loading activity…</p>
    <p v-else-if="!events.length" class="ap__state">No activity yet. Start the conversation above.</p>

    <div v-else class="ap__feed" aria-live="polite">
      <article v-for="event in events" :key="event.id" class="ap__event" :class="'ap__event--' + event.kind">
        <div class="ap__rail"><span>{{ event.kind === 'comment' ? '●' : '·' }}</span></div>
        <div class="ap__card">
          <header>
            <div class="ap__actor">
              <img v-if="event.actor?.image" :src="event.actor.image" alt="">
              <span v-else class="ap__avatar">{{ (event.actor?.name || '?').slice(0, 1).toUpperCase() }}</span>
              <strong>{{ event.actor?.name || 'System' }}</strong>
              <em>{{ kindLabel(event.kind) }}</em>
            </div>
            <div class="ap__meta">
              <time :title="event.creation">{{ formatWhen(event.creation) }}</time>
              <button v-if="event.kind === 'comment'" type="button" title="Copy link" @click="emit('copy', event)">⌁</button>
              <button v-if="event.editable" type="button" title="Edit" @click="emit('edit', event)">✎</button>
              <button v-if="event.deletable" type="button" title="Delete" @click="emit('delete', event)">×</button>
            </div>
          </header>
          <div v-if="event.subject" class="ap__subject">{{ event.subject }}</div>
          <div class="ap__content" v-html="event.content_html" />
          <div v-if="event.attachments?.length" class="ap__attachments">
            <a v-for="file in event.attachments" :key="file.name || file.file_url" :href="file.file_url" target="_blank" rel="noopener noreferrer">
              <span>⌕</span>{{ file.file_name || file.file_url?.split('/').pop() }}<small v-if="file.is_private">Private</small>
            </a>
          </div>
        </div>
      </article>
      <button v-if="canLoadMore" type="button" class="ap__more" :disabled="loading" @click="emit('load-more')">
        {{ loading ? 'Loading…' : 'Load more activity' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.ap{display:flex;flex-direction:column;min-height:0;color:#e6e8ec}.ap__head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:9px}.ap__head>div:first-child{display:flex;flex-direction:column}.ap__head strong{font-size:12px}.ap__head small{font-size:9px;color:rgba(230,232,236,.4);margin-top:2px}.ap__head-actions{display:flex;gap:4px}.ap__head-actions button,.ap__meta button{width:26px;height:26px;border:1px solid rgba(255,255,255,.08);border-radius:6px;background:rgba(255,255,255,.03);color:rgba(230,232,236,.65)}.ap__composer{position:relative;margin-bottom:11px}.ap__editing{margin-bottom:5px;color:#f7b84b;font-size:9px;text-transform:uppercase;letter-spacing:.08em}.ap__retained{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}.ap__retained span{padding:4px 6px;border-radius:5px;background:rgba(255,255,255,.06);font-size:10px}.ap__retained button{border:0;background:none;color:inherit}.ap__state{padding:24px 8px;text-align:center;color:rgba(230,232,236,.4);font-size:11px}.ap__state--error{color:#fb7185}.ap__feed{display:flex;flex-direction:column;min-height:0}.ap--expanded .ap__feed{overflow:auto;padding-right:4px}.ap__event{display:grid;grid-template-columns:16px minmax(0,1fr);position:relative}.ap__rail{position:relative;text-align:center;color:rgba(230,232,236,.28);font-size:9px}.ap__rail:after{content:"";position:absolute;left:7px;top:18px;bottom:-3px;width:1px;background:rgba(255,255,255,.07)}.ap__event:last-of-type .ap__rail:after{display:none}.ap__event--comment .ap__rail{color:#f59e0b}.ap__card{min-width:0;margin:0 0 8px 2px;padding:9px 10px;border:1px solid rgba(255,255,255,.07);border-radius:9px;background:rgba(255,255,255,.025)}.ap__event:not(.ap__event--comment):not(.ap__event--communication):not(.ap__event--automated_message) .ap__card{padding:6px 9px;background:transparent;border-color:transparent}.ap__card>header{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;margin-bottom:6px}.ap__actor{display:flex;align-items:center;gap:5px;min-width:0;font-size:10px}.ap__actor img,.ap__avatar{width:19px;height:19px;border-radius:50%;object-fit:cover;background:rgba(245,158,11,.17);display:grid;place-items:center;color:#f7b84b}.ap__actor strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ap__actor em{font-style:normal;color:rgba(230,232,236,.38);white-space:nowrap}.ap__meta{display:flex;align-items:center;gap:2px;color:rgba(230,232,236,.35);font-size:8px;white-space:nowrap}.ap__meta button{width:20px;height:20px;border:0;background:transparent}.ap__subject{font-weight:600;font-size:11px;margin-bottom:4px}.ap__content{font-size:11px;line-height:1.5;color:rgba(230,232,236,.8);overflow-wrap:anywhere}.ap__content :deep(p){margin:0 0 5px}.ap__content :deep(p:last-child){margin-bottom:0}.ap__content :deep(img){max-width:100%;border-radius:7px}.ap__content :deep(a){color:#f7b84b}.ap__content :deep(.mention){color:#f7b84b;background:rgba(245,158,11,.1);border-radius:4px;padding:1px 3px}.ap__attachments{display:flex;flex-direction:column;gap:4px;margin-top:7px}.ap__attachments a{display:flex;align-items:center;gap:5px;color:rgba(230,232,236,.65);font-size:9px;text-decoration:none}.ap__attachments small{margin-left:auto;color:rgba(245,158,11,.65)}.ap__more{align-self:center;margin:4px 0 10px;padding:6px 11px;border:1px solid rgba(255,255,255,.08);border-radius:7px;background:rgba(255,255,255,.04);color:rgba(230,232,236,.68);font-size:10px}
</style>
