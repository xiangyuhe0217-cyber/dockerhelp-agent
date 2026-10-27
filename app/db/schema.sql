-- ============================================================
-- helpdesk-agent 数据层 schema
-- W1 建全表；chunks 由 W2 写入，runs/steps 由 W5 写入，eval_* 由 W3 写入
-- 所有语句幂等：可重复执行
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- 元信息：记录数据来源，保证可复现 ----------
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- 知识库文档 ----------
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,             -- sha1(rel_path)
    rel_path    TEXT NOT NULL UNIQUE,         -- 相对 docker/docs 仓库的路径
    url         TEXT,                         -- docs.docker.com 可点击链接（优先 alias），否则 GitHub blob
    title       TEXT,
    description TEXT,                         -- front-matter 摘要，切片时作为上下文前缀
    section     TEXT,                         -- manuals / guides / reference ...
    content_md  TEXT NOT NULL,
    word_count  INTEGER NOT NULL DEFAULT 0,
    has_code    INTEGER NOT NULL DEFAULT 0,
    has_table   INTEGER NOT NULL DEFAULT 0,
    lang        TEXT,                         -- zh / en / mixed
    sha256      TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_section ON documents(section);
CREATE INDEX IF NOT EXISTS idx_documents_lang    ON documents(lang);

-- ---------- 历史工单（真实 Issues + 合成工单） ----------
CREATE TABLE IF NOT EXISTS tickets (
    id              TEXT PRIMARY KEY,         -- "docker/for-win#1234" / "synthetic#0001"
    source          TEXT NOT NULL,            -- docker/for-win | moby/moby | synthetic
    number          INTEGER,
    title           TEXT NOT NULL,
    body            TEXT,
    labels          TEXT,                     -- JSON 数组字符串
    state           TEXT,
    created_at      TEXT,
    closed_at       TEXT,
    resolution      TEXT,                     -- 首条 maintainer 回复，无则 NULL
    url             TEXT,
    product_version TEXT,                     -- 从 title/body 抽出的 x.y.z
    is_synthetic    INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tickets_source ON tickets(source);
CREATE INDEX IF NOT EXISTS idx_tickets_synth  ON tickets(is_synthetic);
CREATE INDEX IF NOT EXISTS idx_tickets_closed ON tickets(closed_at);

-- ---------- 合成业务数据（仅用于「查SLA / 升级工单」工具演示） ----------
CREATE TABLE IF NOT EXISTS customers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tier            TEXT NOT NULL,            -- basic / standard / premium
    contract_level  TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS sla (
    id                TEXT PRIMARY KEY,
    tier              TEXT NOT NULL,
    priority          TEXT NOT NULL,          -- P1 / P2 / P3
    first_response_h  INTEGER NOT NULL,
    resolution_h      INTEGER NOT NULL
);

-- ---------- 切片（W2 写入） ----------
CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord           INTEGER NOT NULL,           -- 文档内序号
    heading_path  TEXT,                       -- "Docker Desktop > Install > Windows > WSL2"
    content       TEXT NOT NULL,
    n_tokens      INTEGER NOT NULL DEFAULT 0,
    has_code      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (doc_id, ord)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

-- ---------- Agent 运行与 Trace（W5 写入） ----------
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    session_id    TEXT,
    user_id       TEXT,
    question      TEXT,
    answer        TEXT,
    status        TEXT,                       -- running / done / failed / need_confirm
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at      TEXT,
    total_steps   INTEGER DEFAULT 0,
    total_tokens  INTEGER DEFAULT 0,
    total_cost    REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);

-- ---------- 长期记忆（W6） ----------
-- 只记"确定性"的东西：用户陈述的事实 + 工具执行产生的事件。
-- 不记对话原文（原文在 LangGraph checkpoint 里），也不记一次性检索结果（会过期）。
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,                -- fact | event
    key         TEXT,                         -- fact 的键，如 tier / product / version
    value       TEXT,                         -- 值
    source_run  TEXT,                         -- 来自哪次 run（可追溯）
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, kind, created_at);

-- ---------- 工单案例入库记录（W7 新增） ----------
-- 工单被 resolve 后，会把「问题描述 + 解决方案」写成一篇知识库文档并向量化，
-- 下次遇到同类问题就能检索到。这张表防止重复摄取，也方便追溯。
CREATE TABLE IF NOT EXISTS case_index (
    ticket_id   TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    chunks      INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS steps (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_no     INTEGER NOT NULL,
    type        TEXT NOT NULL,                -- plan / tool / observe / reflect / answer / confirm
    thought     TEXT,
    tool        TEXT,
    args        TEXT,                         -- JSON
    observation TEXT,
    latency_ms  INTEGER DEFAULT 0,
    tokens      INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (run_id, step_no)
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id);

-- ---------- 评测（W3 写入） ----------
CREATE TABLE IF NOT EXISTS eval_cases (
    id          TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    type        TEXT NOT NULL,                -- simple / multi_hop / unanswerable
    difficulty  TEXT,                         -- easy / medium / hard
    gold_doc_ids TEXT,                        -- JSON 数组（精确 doc id，可选）
    gold_paths  TEXT,                         -- JSON 数组：文档路径前缀，命中即算相关
    gold_points TEXT,                         -- JSON 数组：判分要点
    answerable  INTEGER NOT NULL DEFAULT 1,
    topic       TEXT,                         -- 安装/网络/资源/存储/权限/compose
    note        TEXT
);

CREATE TABLE IF NOT EXISTS eval_results (
    id            TEXT PRIMARY KEY,
    run_id        TEXT,                       -- 评测批次
    case_id       TEXT NOT NULL REFERENCES eval_cases(id) ON DELETE CASCADE,
    config        TEXT NOT NULL,              -- E1 / E2 / E3 / E4 / E5 / E6
    retrieved_ids TEXT,                       -- JSON 数组：召回 chunk_id
    answer        TEXT,
    recall5       REAL,
    recall10      REAL,
    mrr           REAL,
    ndcg10        REAL,
    faithfulness  REAL,
    correct       INTEGER,
    latency_ms    INTEGER,
    tokens        INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eval_results_cfg ON eval_results(config, case_id);
