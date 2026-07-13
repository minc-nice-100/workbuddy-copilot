# WorkBuddy Copilot — 技术架构文档

> 版本：0.3.0（端口-适配器模式）
> 更新日期：2026-07-02

## 一、系统概述

WorkBuddy Copilot 是为 Pioneers Learning Community (PLC) 学生构建的实时学习 Copilot。通过 Agent 框架（当前为 WorkBuddy）的 hooks 监控学员对话，后台 LLM 分析学习状态，浮标实时呈现给学员，导师观察台呈现给导师。

**核心设计**：采用 MVC 分层 + 端口-适配器模式，支持 Agent 框架替换（WorkBuddy → Cursor/Claude Code）、操作系统替换（Mac → Windows）、前端替换（桌面 → 移动端）、新交互扩展（飞书推送等）。

## 二、MVC + 端口-适配器架构

```
┌─────────────────────────────────────────────────────────────┐
│  View 层（呈现 — 可替换）                                      │
│  浮标 floating_native  ·  导师观察台 mentor/  ·  未来: 飞书/CLI │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / WebSocket
┌──────────────────────────┴──────────────────────────────────┐
│  Controller 层（service.py / mentor/routes.py）               │
│  只做 HTTP 编解码 + 调用 Service + 返回 JSON                   │
└──────────────────────────┬──────────────────────────────────┘
                           │ 函数调用
┌──────────────────────────┴──────────────────────────────────┐
│  Service 层（services.py — 可复用）                            │
│  AnalysisService     — 依赖端口，不依赖具体 Agent 实现          │
│  SessionQueryService — 依赖端口，不依赖具体 Agent 实现          │
└──────────┬───────────────────────────────┬──────────────────┘
           │ 端口接口                        │ EventBus
┌──────────┴──────────────────┐  ┌──────────┴──────────────────┐
│  Ports（抽象接口）            │  │  EventBus（横切）            │
│  AgentSessionRepository      │  │  publish → subscribe        │
│  TranscriptParser            │  │  WS / 飞书 / 定时报告          │
│  FloatingWindow              │  └─────────────────────────────┘
│  Notifier                    │
└──────────┬──────────────────┘
           │ 适配器实现
┌──────────┴──────────────────────────────────────────────────┐
│  Adapters（当前实现）                                          │
│  WorkBuddySessionRepository  ·  WorkBuddyTranscriptParser    │
│  MacFloatingWindow (PyObjC)  ·  MacNotifier (osascript)     │
│  未来: CursorRepo / WinWindow / PushNotifier / ...           │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│  Model 层                                                     │
│  models.py（领域模型 dataclass）                               │
│  Store（copilot.db 读写）  ·  wb_db.py（workbuddy.db 只读）    │
│  transcript.py（JSONL 解析）  ·  llm.py（LLM 调用）            │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│  数据源                                                        │
│  copilot.db (4表)  ·  workbuddy.db (sessions/workspaces)     │
│  projects/*.jsonl  ·  memory/*.md                            │
└─────────────────────────────────────────────────────────────┘
```

### 端口定义（copilot/ports.py）

| 端口 | 方法 | 当前实现 | 未来实现 |
|------|------|---------|---------|
| `AgentSessionRepository` | list_sessions / get_session_title / get_active_session | WorkBuddySessionRepository（wb_db + wb_sessions） | CursorSessionRepository / ClaudeCodeSessionRepository |
| `TranscriptParser` | parse / recent_messages | WorkBuddyTranscriptParser（JSONL 6 种 type） | ClaudeCodeTranscriptParser |
| `FloatingWindow` | show / hide / update_state / set_position / run | MacFloatingWindow（PyObjC NSPanel） | WinFloatingWindow（win32gui / PyQt6） |
| `Notifier` | notify(title, body, severity) | MacNotifier（osascript） | WinNotifier（toast） / PushNotifier（APNs/FCM） |

### 工厂函数

`config.json` 增加 `agent.framework` 字段，工厂函数根据配置创建适配器：

```json
{
  "agent": { "framework": "workbuddy" }
}
```

```python
# copilot/ports.py
def create_session_repository(config) -> AgentSessionRepository:
    framework = config.get("agent", {}).get("framework", "workbuddy")
    if framework == "workbuddy":
        from .wb_sessions import WorkBuddySessionRepository
        return WorkBuddySessionRepository()
    raise ValueError(f"不支持的框架: {framework}")
```

切换到 Cursor 时，只需：
1. 实现 `CursorSessionRepository` 和 `CursorTranscriptParser`
2. config.json 改 `"framework": "cursor"`
3. Service / Controller / Model 层**零改动**

## 三、各层职责

### 3.1 View 层（呈现 — 可替换）

| 组件 | 文件 | 说明 |
|------|------|------|
| 浮标 | `floating_native.py` | PyObjC NSPanel，跨 Space 显示，点击弹分析卡片 |
| 导师观察台 | `mentor/` + `static/mentor/` | 三栏布局：学员/对话/时间线，WS 实时推送 |
| 未来扩展 | — | 飞书推送 / CLI / Mobile 均可作为新 View |

**关键设计**：View 层只依赖 Service 层接口，替换浮标为其他客户端时 Service 层零改动。

### 3.2 Controller 层（HTTP 编解码）

| 文件 | 职责 |
|------|------|
| `service.py` | API Gateway：/health /report /recent /sessions /current_session /ws |
| `mentor/routes.py` | 导师 API：/api/mentor/students /sessions /timeline |

**关键设计**：Controller 只做编解码，不含业务逻辑。`/report` 收到事件后委托给 `AnalysisService`，`/sessions` 委托给 `SessionQueryService`。

### 3.3 Service 层（业务编排 — 可复用）

| 类 | 文件 | 职责 |
|----|------|------|
| `AnalysisService` | `services.py` | UserPromptSubmit→存prompt→发事件；Stop→LLM分析→存结果→发事件 |
| `SessionQueryService` | `services.py` | 学员列表/对话列表/时间线/当前会话（浮标和导师台共用） |

**关键设计**：Service 层依赖**端口接口**（AgentSessionRepository / TranscriptParser / Notifier），不依赖具体 Agent 实现。切换 Agent 框架时只换适配器，Service 层零改动。

### 3.4 Model 层（领域模型 + 数据访问）

| 组件 | 文件 | 职责 |
|------|------|------|
| 领域模型 | `models.py` | Student/Conversation/Prompt/AISummary/Analysis/TimelineEntry dataclass |
| CopilotRepo | `store.py` | copilot.db 读写（reports/analyses/prompts/ai_summaries 4 表） |
| WorkBuddyRepo | `wb_db.py` | workbuddy.db 只读（sessions/workspaces） |
| JSONL 解析 | `transcript.py` | 解析对话记录，提取 message/ai-title |
| LLM 调用 | `llm.py` | DeepSeek 分析 + AI 回答摘要 |

**关键设计**：领域模型替代全程 dict 传递，消除字段名漂移。LLM 返回结果先映射成 `AnalysisResult` 模型再入库/推送。

### 3.5 EventBus（横切 — 解耦推送）

| 文件 | 机制 |
|------|------|
| `eventbus.py` | publish/subscribe 模式 |

Service 层 `await bus.publish(payload)`，WS 层注册订阅者接收。未来增加飞书推送只需注册新订阅者，Service 层零改动。

## 四、数据流

### 4.1 学员提问（UserPromptSubmit 事件）

```
WorkBuddy hook → POST /report → Controller
  → AnalysisService.handle_user_prompt_submit()
    → CopilotRepo.add_prompt()
    → EventBus.publish({type: "prompt", ...})
      → WS 订阅者推送给浮标 + 导师台
```

### 4.2 AI 回复完成（Stop 事件）

```
WorkBuddy hook → POST /report → Controller
  → AnalysisService.handle_stop()
    → CopilotRepo.add_prompt()（若有）
    → transcript.parse_transcript()
    → llm.analyze() → AnalysisResult
    → WorkBuddyRepo.get_session_title()
    → CopilotRepo.add_ai_summary()
    → CopilotRepo.add_analysis()
    → EventBus.publish({type: "ai_summary"})
    → EventBus.publish({type: "analysis"})
      → WS 订阅者推送给浮标 + 导师台
```

### 4.3 导师查看时间线

```
浏览器 → GET /api/mentor/sessions/{id}/timeline → Controller
  → SessionQueryService.get_timeline()
    → CopilotRepo.get_timeline_by_session()（三表 UNION）
    → 返回 list[TimelineEntry]
```

### 4.4 浮标跟随当前对话

```
浮标 → GET /current_session → Controller
  → SessionQueryService.get_active_session()
    → WorkBuddyRepo（workbuddy.db sessions 表，resumed_at 最新）
  → SessionQueryService.list_all_sessions_with_title()
    → 返回当前会话 + 切换栏列表
```

## 五、数据源

### 5.1 WorkBuddy 数据（只读）

| 数据源 | 内容 | 用途 |
|--------|------|------|
| `~/.workbuddy/workbuddy.db` | sessions 表（205 条，含 title/status/mode） | 对话列表 + 标题（权威） |
| `~/.workbuddy/workbuddy.db` | workspaces 表（13 个项目目录） | 空间/任务分组 |
| `~/.workbuddy/app/sessions.json` | 7 条运行时缓存 | DB 不可用时降级 |
| `~/.workbuddy/projects/<enc>/<sid>.jsonl` | 对话转录（6 种 type） | LLM 分析输入 |
| `~/.workbuddy/memory/<userId>_memory.md` | 用户画像 | 用户名（AI 积累） |

### 5.2 Copilot 自有数据（读写）

| 表 | 内容 |
|----|------|
| reports | 原始上报记录 |
| analyses | LLM 学习状态分析（诊断+建议+严重程度） |
| prompts | 学员提示词全文（原样不截断） |
| ai_summaries | AI 回答客观摘要 |

## 六、空间与任务

WorkBuddy UI 将对话分为"空间"和"任务"两组：

| UI 分组 | 规则 | 验证 |
|--------|------|------|
| 空间 (13) | sessions.cwd 在 workspaces 表中 | 13 个目录 ✅ |
| 任务 (54) | sessions.cwd 不在 workspaces 表中 | 54 个目录 ✅ |

详见 `docs/workbuddy-file-structure.md`。

## 七、技术栈

| 组件 | 技术 |
|------|------|
| 后台服务 | Python 3.13 + FastAPI + uvicorn（端口 8765） |
| 浮标 | PyObjC NSPanel（原生 macOS，跨 Space） |
| 导师台 | 纯静态 HTML+JS+WS |
| LLM | 腾讯云 TokenHub (deepseek-v3-0324) |
| 实时推送 | websockets（浮标 /ws + 导师 /ws/mentor 独立隔离） |
| Copilot 存储 | SQLite（data/copilot.db） |
| WorkBuddy 数据 | SQLite 只读（~/.workbuddy/workbuddy.db） |
| 事件总线 | 自实现 EventBus（async publish/subscribe） |
| WorkBuddy hooks | UserPromptSubmit + Stop（只读旁路，不注入） |
| **端口-适配器** | ports.py 定义 4 个抽象接口 + 工厂函数 |

## 八、文件结构

```
workbuddy-copilot/
├── copilot/
│   ├── models.py             # ⭐ 领域模型 dataclass
│   ├── eventbus.py           # ⭐ 事件总线（pub/sub 解耦）
│   ├── services.py           # ⭐ Service 层（依赖端口，不依赖具体实现）
│   ├── ports.py              # ⭐ 端口定义 + 工厂函数（AgentSessionRepository 等）
│   ├── service.py            # Controller 层（HTTP 路由 + WS）
│   ├── config.py             # 配置加载
│   ├── transcript.py         # JSONL 解析 + WorkBuddyTranscriptParser 适配器
│   ├── llm.py                # LLM 封装 + 学习分析 prompt
│   ├── store.py              # CopilotRepo（copilot.db 读写）
│   ├── wb_db.py              # WorkBuddy DB 只读访问
│   ├── wb_sessions.py        # 会话读取 + WorkBuddySessionRepository 适配器
│   ├── notifier_macos.py     # MacNotifier 适配器（osascript）
│   ├── hook.py               # 事件采集（独立进程）
│   ├── floating_native.py    # View: 浮标（PyObjC，macOS 适配器）
│   └── mentor/               # View: 导师观察台
│       ├── routes.py         # Controller: 导师 API
│       ├── ws.py             # WS 客户端池
│       └── timeline.py       # 时间线工具函数
├── copilot/static/mentor/    # 导师前端
├── docs/                     # 文档
├── tests/                    # 112 个测试
└── data/                     # DB + 图标
```

## 九、可替换性评估

| 替换场景 | 状态 | 需要的工作 |
|---------|------|-----------|
| **新交互形式**（飞书推送） | ✅ 已支持 | 注册 EventBus 订阅者，Service 零改动 |
| **Agent 框架替换**（Cursor） | ✅ 端口已就绪 | 实现 CursorSessionRepository + CursorTranscriptParser，config 改 framework |
| **操作系统替换**（Windows） | ⚠️ 接口已定义 | 实现 WinFloatingWindow + WinNotifier，floating_native 适配器待写 |
| **前端替换**（移动端） | ⚠️ HTTP/WS 已有 | 加 auth + TLS + public_url + APNs/FCM 推送出口 |

## 十、后续迭代方向

- [ ] 实现 CursorSessionRepository 适配器（验证端口抽象）
- [ ] 飞书推送告警（注册 EventBus 订阅者）
- [ ] 多学生支持（hook 远程上报 + 认证）
- [ ] additionalContext 回注 WorkBuddy（双工模式）
- [ ] 五学维度评估（做人/思维/学习/交流/合作）
- [ ] 全部空间/任务组视图（基于 workspaces 表分组）
- [ ] Windows 适配器（WinFloatingWindow + WinNotifier）
