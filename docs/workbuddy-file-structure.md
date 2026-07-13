# WorkBuddy 本地文件结构调研报告

> 调研日期：2026-07-02（第二轮，含 Space/Task 概念修正）
> 调研方法：实际读取 `~/.workbuddy/` 目录文件 + SQLite 数据库 + 用户截图验证
> 目的：彻底搞清楚 WorkBuddy 的对话列表、对话标题、用户信息、空间/任务分组等在本地文件中的存储结构

---

## 一、核心概念速览

| 概念 | 本地体现 | 关键字段/文件 |
|------|---------|-------------|
| **会话 (Session)** | `workbuddy.db` sessions 表一行 | id / cwd / title / status / mode |
| **空间 (Space)** | cwd 在 `workspaces` 表中的会话分组 | sessions.cwd JOIN workspaces.path |
| **任务 (Task)** | cwd 不在 `workspaces` 表中的会话分组 | sessions.cwd NOT IN workspaces.path |
| **对话标题** | sessions.title（AI 生成）+ sessions.custom_title（用户自定义） | DB 直接读取，无需扫 JSONL |
| **对话记录** | `projects/<enc>/<sid>.jsonl` | 6 种 type：message/reasoning/function_call/function_call_result/file-history-snapshot/ai-title |
| **用户名** | `memory/<userId>_memory.md`（AI 积累，非结构化） | 无 users 表，userId 是 UUID |
| **TaskCreate 任务** | `tasks/<conversationId>/N.json` | 与 UI "任务"分组是不同概念 |

---

## 二、目录结构总览

```
~/.workbuddy/
├── workbuddy.db                    # ⭐ 核心数据库（SQLite，610KB）
├── workbuddy.db-wal / -shm         # SQLite WAL 日志 / 共享内存
├── app/
│   ├── sessions.json               # 运行时会话缓存（仅最近 7 条，无标题）
│   ├── session/                    # Electron Chromium 会话数据
│   └── window-state.json
├── projects/                       # ⭐ 对话记录（按工作目录分组）
│   └── <workDir编码>/
│       ├── <sessionId>.jsonl       # 对话转录（每行一个事件）
│       └── <sessionId>/            # 工具结果子目录
├── tasks/                          # TaskCreate/TaskUpdate 持久化
│   └── <conversationId>/N.json     # 每文件一个 task
├── sessions/<pid>.json             # 活跃 CLI 进程心跳
├── memory/
│   └── <userId>_memory.md          # ⭐ 用户画像（唯一用户名来源）
├── connectors/<userId>/            # 连接器配置与 token
├── file-history/<uuid>/            # 文件快照（用于 /rewind）
├── artifact-index/<uuid>.json      # 产物索引
├── blobs/ / traces/ / logs/        # 二进制/日志/追踪
├── settings.json                   # 用户设置（含 hooks 配置）
├── models.json                     # 自定义模型配置
├── mcp.json                        # MCP 服务器配置
├── .neodata_token                  # NeoData 认证 token（无用户名）
├── USER.md / IDENTITY.md / SOUL.md # 身份模板（本机未填写）
└── argv.json / user-state.json / workspace-state.json
```

---

## 三、核心数据库：workbuddy.db

### 3.1 表结构

**sessions 表**（权威会话数据源）：
```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,              -- 会话 UUID
    cwd TEXT NOT NULL,                -- 工作目录绝对路径
    user_id TEXT NOT NULL,            -- 用户 UUID（非姓名）
    title TEXT,                       -- AI 生成的对话标题
    custom_title TEXT,                -- 用户自定义标题
    status TEXT DEFAULT 'Pending',    -- Pending / completed / ...
    created_at INTEGER NOT NULL,      -- 创建时间（毫秒时间戳）
    updated_at INTEGER NOT NULL,      -- 更新时间（毫秒时间戳）
    deleted_at INTEGER,               -- 软删除（NULL = 未删除）
    mode TEXT,                        -- craft / plan / ask
    last_activity_at INTEGER,         -- 最后活动时间（毫秒）
    permission_mode TEXT,             -- fullAccess 等
    is_playground INTEGER DEFAULT 0,
    project_id TEXT,                  -- 预留字段（全部为 NULL）
    model TEXT, expert_id TEXT, ...   -- 其他元数据
);
```

**workspaces 表**（用户显式打开的工作目录）：
```sql
CREATE TABLE workspaces (
    path TEXT PRIMARY KEY,            -- 项目目录绝对路径
    last_opened_at INTEGER NOT NULL   -- 最后打开时间（毫秒）
);
```

其他表：`session_usage`（会话用量）、`automations` / `automation_runs` / `automation_runtime_state`（自动化任务）、`migration_meta`。

### 3.2 关键数据量

| 维度 | 值 |
|------|-----|
| sessions 总数 | 205 条 |
| 未删除会话 | 204 条 |
| workspaces 记录 | 13 个（= UI "空间"数） |
| 不在 workspaces 的 cwd | 54 个（= UI "任务"数） |

---

## 四、空间与任务

### 4.1 UI 分组规则

WorkBuddy 侧边栏将对话分为"空间"和"任务"两组：

| UI 分组 | 本地数据规则 | 验证 |
|--------|------------|------|
| **空间 (13)** | 会话的 `cwd` 在 `workspaces` 表中 | 13 个目录，与 UI 一致 ✅ |
| **任务 (54)** | 会话的 `cwd` 不在 `workspaces` 表中 | 54 个目录，与 UI 一致 ✅ |

**核心规则**：
- 用户**指定了工作目录**的对话 → WorkBuddy 记录到 `workspaces` 表 → UI 显示在"空间"组
- 用户**没有指定工作目录**的对话 → WorkBuddy 自动生成 `~/WorkBuddy/2026-...` 目录 → 不在 `workspaces` 表 → UI 显示在"任务"组

**"空间"不是独立的表**，而是 WorkBuddy UI 根据 `workspaces` 表对会话做的分组。

验证 SQL：
```sql
-- 空间数：cwd 在 workspaces 表中的不同目录数
SELECT COUNT(DISTINCT s.cwd) FROM sessions s
  JOIN workspaces w ON s.cwd = w.path WHERE s.deleted_at IS NULL;
-- 结果：13

-- 任务数：cwd 不在 workspaces 表中的不同目录数
SELECT COUNT(DISTINCT s.cwd) FROM sessions s
  LEFT JOIN workspaces w ON s.cwd = w.path
  WHERE w.path IS NULL AND s.deleted_at IS NULL AND s.cwd != '';
-- 结果：54
```

### 4.2 两个"任务"概念（勿混淆）

| 概念 | 位置 | 含义 |
|------|------|------|
| UI "任务"组 | workbuddy.db sessions + workspaces | 会话分组（cwd 不在 workspaces 表） |
| TaskCreate 任务 | `~/.workbuddy/tasks/<cid>/N.json` | 开发任务持久化（subject/status 等） |

---

## 五、对话记录：JSONL 文件

### 5.1 路径规则

```
~/.workbuddy/projects/<workDir编码>/<sessionId>.jsonl
```

**编码规则**：workDir 的 `/` 替换为 `-`，去掉开头前导 `-`，中文/空格原样保留。

| workDir | 编码目录名 |
|---------|-----------|
| `/Users/student/projects/sample-project` | `Users-student-projects-sample-project` |
| `/Users/student/notes` | `Users-student-notes` |

### 5.2 JSONL 行结构（6 种 type）

| type | 含义 | 频率 |
|------|------|------|
| `message` | 用户/助手/系统消息 | ~10% |
| `reasoning` | LLM 思考过程 | ~17% |
| `function_call` | 工具调用 | ~31% |
| `function_call_result` | 工具返回 | ~31% |
| `file-history-snapshot` | 文件快照锚点 | ~10% |
| `ai-title` | AI 生成的对话标题 | 多条 |

**message 结构**：
```json
{
  "id": "...", "timestamp": 1782828714330,
  "type": "message", "role": "user",
  "content": [{"type": "input_text", "text": "..."}],
  "sessionId": "...", "cwd": "..."
}
```

⚠️ 用户消息的 text 里通常包含 `<system-reminder>` 包裹的系统注入 + `<user_query>` 标签包裹的真实用户输入。提取真实 prompt 需用正则 `re.search(r'<user_query>(.*?)</user_query>', text, re.DOTALL)`。

**ai-title 结构**：
```json
{"timestamp": ..., "type": "ai-title", "aiTitle": "通用对话", "sessionId": "..."}
```
⚠️ 一个对话会有多条 ai-title（话题漂移时重新生成）。但**不需要扫 JSONL 取标题**——直接读 `workbuddy.db.sessions.title` 即可，DB 存的是最终值。

---

## 六、用户名与用户信息

### 6.1 查找结果

| 来源 | 含用户名？ | 说明 |
|------|----------|------|
| `workbuddy.db` | ❌ | 无 users 表，user_id 是 UUID |
| `.neodata_token` | ❌ | 只有 token + saved_at |
| `connector-states.json` | ❌ | 只有 accountIdentityKey（UUID） |
| `settings.json` | ❌ | 含 legacyOwnerUid（=userId），无姓名 |
| `USER.md` | ❌ | 模板，本机未填写 |
| **`memory/<userId>_memory.md`** | ✅ | AI 在对话中积累的用户画像，含"Michael" |

### 6.2 结论

- **"王佳梁"在本地文件中未找到**（所有文件均无此字符串）
- memory 文件含"技术专家Michael"，但这是 AI 提取的描述，非账号字段
- 本地无结构化用户名字段，建议 Copilot 用"学员 1"作为占位

---

## 七、数据源优先级（对 Copilot 项目）

| 数据 | 权威源 | 降级源 | 不要用 |
|------|--------|--------|--------|
| 对话列表 | `workbuddy.db` sessions 表（205 条） | `app/sessions.json`（7 条缓存） | — |
| 对话标题 | `workbuddy.db` sessions.title / custom_title | JSONL 最后一条 ai-title | sessions.json（无标题字段） |
| 用户名 | `memory/<userId>_memory.md` | config.json student_name | userId（只是 UUID） |
| 对话内容 | `projects/<enc>/<sid>.jsonl` | — | — |
| 空间/任务分组 | sessions.cwd JOIN workspaces 表 | — | — |

---

## 八、对 Copilot 项目的影响

### 8.1 已完成的重构（2026-07-02）

| 改动 | 文件 | 说明 |
|------|------|------|
| 新增 workbuddy.db 只读层 | `copilot/wb_db.py` | `mode=ro` 连接，封装 sessions/workspaces 查询 |
| 重构会话读取 | `copilot/wb_sessions.py` | 优先读 DB，降级 sessions.json |
| 标题来源改用 DB | `copilot/store.py` | 不再扫 JSONL 的 ai-title |
| 删除 JSONL 扫标题 | `copilot/transcript.py` | 移除 `latest_ai_title()` 函数 |

### 8.2 旧会话 prompt 补录（2026-07-02）

**问题**：prompts 表是 7/1 导师观察台上线后才建的，之前的会话只有 analysis 没有 prompt。

**原因**：
- "继续排查未解决问题"会话的 analyses 时间范围：6/30 16:05 ~ 7/1 18:52
- prompts 表全局最早记录：7/1 21:24（晚了 2.5 小时）
- 该会话的分析发生在 prompts 表建立之前

**修复**：从 JSONL 补录旧会话的 prompt 数据
- 从 `type=message` + `role=user` 的行提取 `<user_query>` 标签内容
- 过滤 system-reminder 注入和空消息
- 补录结果：a9f2361e 补录 6 条，db8da782 补录 27 条

### 8.3 后续可扩展（基于空间/任务概念）

当前导师观察台只展示当前项目（单个 cwd）的对话。基于空间/任务概念，可扩展：

1. **全部空间视图**：列出 `workspaces` 表的所有目录，每个目录作为一个空间，展示其下的会话
2. **任务组视图**：展示 cwd 不在 `workspaces` 表的会话
3. **跨空间搜索**：在所有空间/任务中搜索学员的对话

MVP 建议保留"当前项目"视图，后续按需扩展。
