// 与后端的所有交互都收在这里，组件里不直接写 fetch
const BASE = ''

async function j(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

export const api = {
  health: () => j('/api/health'),
  chat: (body) => j('/api/chat', { method: 'POST', body: JSON.stringify(body) }),
  status: (runId) => j(`/api/chat/${runId}`),
  resume: (body) => j('/api/resume', { method: 'POST', body: JSON.stringify(body) }),
  memory: (user) => j(`/api/memory/${encodeURIComponent(user)}`),
  clearMemory: (user) =>
    j(`/api/memory/${encodeURIComponent(user)}`, { method: 'DELETE' }),
  runs: (user) => j(`/api/runs?user=${encodeURIComponent(user)}&limit=20`),
}

// 轮询直到 status 不是 running
export async function pollRun(runId, onTick, { interval = 400, timeout = 180000 } = {}) {
  const started = Date.now()
  while (Date.now() - started < timeout) {
    const data = await api.status(runId)
    onTick?.(data)
    if (!data.running) return data
    await new Promise((r) => setTimeout(r, interval))
  }
  throw new Error('轮询超时')
}
