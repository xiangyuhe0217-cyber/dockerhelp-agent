<script setup>
import { ref, onMounted, nextTick, computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from './api'
import TracePanel from './components/TracePanel.vue'

const user = ref(localStorage.getItem('dh_user') || 'web')
const thread = ref(localStorage.getItem('dh_thread') || 'main')

const messages = ref([])
const steps = ref([])
const running = ref(false)
const interrupt = ref(null)
const drawer = ref('') // '' | 'trace' | 'memory'
const memory = ref({ facts: [], events: [], backend: '', user: '' })
const error = ref('')
const input = ref('')
const listEl = ref(null)
const showSettings = ref(false)

const canSend = computed(() => input.value.trim().length > 0 && !running.value)

marked.setOptions({ breaks: true, gfm: true })

function md(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
}

onMounted(loadMemory)

async function loadMemory() {
  try {
    memory.value = await api.memory(user.value)
  } catch { /* ignore */ }
}

function saveIdentity() {
  localStorage.setItem('dh_user', user.value)
  localStorage.setItem('dh_thread', thread.value)
  loadMemory()
}

function scrollBottom() {
  nextTick(() => {
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  })
}

// ---------- 流式发送 ----------
function send() {
  const q = input.value.trim()
  if (!q || running.value) return
  saveIdentity()
  input.value = ''
  error.value = ''
  steps.value = []
  // 注意：不要把 ref() 塞进数组——Vue 不会对数组里的 ref 自动解包，
  // 模板里 m.text 会是 undefined（用户消息就渲染不出来）。
  // 正确做法：推普通对象，然后通过 messages.value[i] 这个响应式代理去改。
  messages.value.push({ role: 'user', text: q })
  const idx = messages.value.length
  messages.value.push({ role: 'assistant', text: '', meta: '' })
  scrollBottom()

  running.value = true
  const url =
    `/api/chat/stream?question=${encodeURIComponent(q)}` +
    `&user=${encodeURIComponent(user.value)}&thread=${encodeURIComponent(thread.value)}`
  streamRun(url, idx)
}

function streamRun(url, idx) {
  const es = new EventSource(url)
  const bubble = () => messages.value[idx]

  es.addEventListener('step', (e) => {
    steps.value = [...steps.value, JSON.parse(e.data)]
    scrollBottom()
  })

  es.addEventListener('token', (e) => {
    bubble().text += JSON.parse(e.data)
    scrollBottom()
  })

  // 后端判断"资料不足、要用网络搜索重写"时，会先发 reset 清空已推的草稿
  es.addEventListener('reset', () => {
    bubble().text = ''
  })

  es.addEventListener('done', (e) => {
    const data = JSON.parse(e.data)
    es.close()
    running.value = false
    if (data.status === 'need_confirm' && data.interrupt) {
      interrupt.value = { ...data.interrupt }
      bubble().meta = '等待你确认写操作…'
      return
    }
    const b = bubble()
    if (!b.text) b.text = data.answer || '（无输出）'
    b.meta = `route=${data.route || '-'} · ${steps.value.length} 步 · ${data.tokens || 0} tokens`
    loadMemory()
    scrollBottom()
  })

  es.addEventListener('failed', (e) => {
    es.close()
    running.value = false
    error.value = JSON.parse(e.data).message || '执行失败'
  })

  es.onerror = () => {
    // 服务端正常关闭也会触发 onerror，交给 done/failed 处理
    if (running.value) {
      es.close()
      running.value = false
      if (!bubble().text) error.value = '连接中断，请重试'
    }
  }
}

function decide(ok) {
  const info = interrupt.value
  interrupt.value = null
  steps.value = []
  running.value = true
  const idx = messages.value.length
  messages.value.push({ role: 'assistant', text: '', meta: '' })
  const url =
    `/api/resume/stream?user=${encodeURIComponent(user.value)}` +
    `&thread=${encodeURIComponent(thread.value)}&decision=${ok}` +
    `&question=${encodeURIComponent(info?.question || '')}`
  streamRun(url, idx)
}

async function clearMemory() {
  if (!confirm(`清空用户 ${user.value} 的长期记忆？`)) return
  await api.clearMemory(user.value)
  await loadMemory()
}

function onKey(e) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send()
}
</script>

<template>
  <div class="app">
    <div class="topbar">
      <h1>🛟 Docker 服务台 Agent</h1>
      <div class="spacer"></div>
      <button class="ghost" @click="showSettings = !showSettings">⚙ 身份</button>
      <button class="ghost" :class="{ active: drawer === 'trace' }" @click="drawer = drawer === 'trace' ? '' : 'trace'">
        轨迹<span v-if="steps.length"> {{ steps.length }}</span>
      </button>
      <button class="ghost" :class="{ active: drawer === 'memory' }" @click="drawer = drawer === 'memory' ? '' : 'memory'">
        记忆<span v-if="memory.facts.length"> {{ memory.facts.length }}</span>
      </button>
    </div>

    <div v-if="showSettings" class="settings">
      <label>用户 <input v-model="user" @change="saveIdentity" /></label>
      <label>会话 <input v-model="thread" @change="saveIdentity" /></label>
      <span class="muted">记忆后端：{{ memory.backend || '-' }} · 用户隔离靠「用户」字段</span>
    </div>

    <div class="main">
      <section class="chat">
        <div class="messages" ref="listEl">
          <div v-if="!messages.length" class="welcome">
            <div class="welcome-card">
              <div class="welcome-title">试试这些问题</div>
              <ul>
                <li>Docker Desktop 装完一直卡在 Starting 怎么办</li>
                <li>一个 premium 客户的 P1 工单，SLA 多久响应</li>
                <li>之前有没有人反馈过端口被占用的问题</li>
                <li>帮我建一个 P2 工单，标题「测试工单」，客户 cust-003</li>
              </ul>
            </div>
          </div>

          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="role">{{ m.role === 'user' ? '你' : '助手' }}</div>
            <div v-if="m.role === 'user'" class="bubble">{{ m.text }}</div>
            <div v-else class="bubble md" v-html="md(m.text)"></div>
            <div v-if="m.meta" class="meta">{{ m.meta }}</div>
          </div>

          <div v-if="running && !steps.length" class="muted">正在检索…</div>
          <div v-if="error" class="msg">
            <div class="bubble" style="border-color: var(--err); color: var(--err)">{{ error }}</div>
          </div>
        </div>

        <div class="composer">
          <div class="row">
            <textarea v-model="input" placeholder="描述你的问题…（Ctrl+Enter 发送）" @keydown="onKey"></textarea>
            <button class="primary" :disabled="!canSend" @click="send">
              {{ running ? '思考中…' : '发送' }}
            </button>
          </div>
        </div>
      </section>

      <aside v-if="drawer" class="drawer">
        <div class="drawer-head">
          <span>{{ drawer === 'trace' ? '执行轨迹' : '长期记忆' }}</span>
          <button class="ghost" @click="drawer = ''">关闭</button>
        </div>
        <div class="drawer-body">
          <TracePanel v-if="drawer === 'trace'" :steps="steps" :running="running" />

          <div v-else>
            <div class="muted" style="margin-bottom: 8px">
              {{ memory.backend }} · 用户 {{ memory.user }}
            </div>
            <div v-if="!memory.facts.length && !memory.events.length" class="muted">还没有记忆。</div>
            <div v-for="f in memory.facts" :key="f.key" class="mem-item">
              <b>{{ f.key }}</b> = {{ f.value }}
            </div>
            <div v-if="memory.events.length" style="margin-top: 12px">
              <div class="muted">最近操作</div>
              <div v-for="(e, i) in memory.events" :key="i" class="mem-item">{{ e.value }}</div>
            </div>
            <button class="danger" style="margin-top: 14px" @click="clearMemory">清空该用户记忆</button>
          </div>
        </div>
      </aside>
    </div>

    <div v-if="interrupt" class="dialog-backdrop">
      <div class="dialog">
        <h3>⚠ 写操作需要确认</h3>
        <div class="muted">工具：{{ interrupt.tool }}</div>
        <pre>{{ JSON.stringify(interrupt.args, null, 2) }}</pre>
        <div class="actions">
          <button class="danger" @click="decide(false)">拒绝</button>
          <button class="primary" @click="decide(true)">允许执行</button>
        </div>
      </div>
    </div>
  </div>
</template>
