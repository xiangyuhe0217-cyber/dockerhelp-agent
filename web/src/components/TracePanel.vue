<script setup>
defineProps({
  steps: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})

function fmtArgs(args) {
  if (!args || Object.keys(args).length === 0) return ''
  return JSON.stringify(args)
}

function typeLabel(t) {
  return {
    plan: '决策',
    tool: '工具',
    guard: '拦截',
    confirm: '确认',
    answer: '回答',
  }[t] || t
}
</script>

<template>
  <div>
    <div v-if="!steps.length" class="muted">
      {{ running ? '正在思考…' : '还没有执行记录，发一条消息试试。' }}
    </div>

    <div v-for="s in steps" :key="s.step_no" class="step" :class="s.type">
      <div class="head">
        <span>#{{ s.step_no }} {{ typeLabel(s.type) }}</span>
        <span v-if="s.tool" class="tool">{{ s.tool }}</span>
        <span v-if="s.latency_ms">{{ s.latency_ms }}ms</span>
        <span v-if="s.tokens">{{ s.tokens }} tok</span>
      </div>

      <div v-if="s.thought" class="thought">{{ s.thought }}</div>
      <div v-if="fmtArgs(s.args)" class="muted" style="margin-top: 4px">
        {{ fmtArgs(s.args) }}
      </div>
      <div v-if="s.observation" class="obs">{{ s.observation }}</div>
    </div>

    <div v-if="running" class="muted" style="margin-top: 8px">● 运行中…</div>
  </div>
</template>
