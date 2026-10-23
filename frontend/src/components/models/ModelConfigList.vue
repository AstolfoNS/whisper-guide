<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag } from 'naive-ui'
import type { ModelConfig } from '../../api/modelConfigs.ts'

const props = defineProps<{ profiles: ModelConfig[]; selectedId?: string; loading?: boolean; busy?: boolean; createDisabled?: boolean }>()
const emit = defineEmits<{ select: [id: string]; create: [] }>()
const alignNames = { off: '语义对齐关闭', fallback: '低分兜底', hybrid: '混合校准' }
const count = computed(() => props.profiles.length)
</script>

<template>
  <aside class="profile-list" aria-labelledby="profile-list-title" :aria-busy="loading">
    <div class="profile-list__heading"><h2 id="profile-list-title">配置方案 <span>{{ count }}</span></h2><NButton size="small" :disabled="busy || createDisabled" @click="emit('create')">新建配置</NButton></div>
    <p v-if="loading && !count" class="profile-list__empty" role="status">正在读取配置…</p>
    <ul v-else class="profile-list__items" aria-label="模型配置方案">
      <li v-for="profile in profiles" :key="profile.id">
        <button type="button" class="profile-item" :class="{ 'profile-item--selected': selectedId === profile.id }" :aria-current="selectedId === profile.id ? 'true' : undefined" :disabled="busy" @click="emit('select', profile.id)">
          <span class="profile-item__name">{{ profile.name }}</span>
          <span class="profile-item__summary">{{ profile.settings.stt_provider === 'local' ? '本地 STT' : '云端 STT' }} · {{ alignNames[profile.settings.llm_align_mode] }}</span>
          <span class="profile-item__badges"><NTag v-if="profile.is_active" size="small" type="success" :bordered="false">当前生效</NTag><NTag v-if="profile.pending_activation" size="small" type="warning" :bordered="false">修改待激活</NTag><span v-if="profile.is_default" class="profile-item__default">内置方案</span></span>
        </button>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.profile-list { border: 1px solid var(--line); border-radius: 12px; background: var(--surface); padding: 20px 12px 12px; align-self: start; }
.profile-list__heading { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:0 8px 16px; }
.profile-list h2 { font-size:15px; margin:0; }
.profile-list h2 span { color:var(--muted); font-weight:400; margin-left:4px; }
.profile-list__items { list-style:none; padding:0; margin:0; display:grid; gap:8px; }
.profile-item { display:flex; flex-direction:column; gap:8px; width:100%; text-align:left; border:1px solid transparent; background:transparent; color:var(--ink); border-radius:8px; padding:14px 12px; cursor:pointer; }
.profile-item:hover { background:var(--soft); }
.profile-item--selected { background:var(--soft); border-color:var(--accent); }
.profile-item__name { font-weight:650; overflow-wrap:anywhere; }
.profile-item__summary,.profile-item__default,.profile-list__empty { color:var(--muted); font-size:12px; }
.profile-item__badges { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.profile-item:disabled { cursor:wait; }
</style>
