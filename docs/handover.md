# WorkBuddy Copilot — 智能体交接文档

> 交接日期：2026-07-02
> 版本：v0.3.0（MVC + 端口-适配器模式）
> 项目路径：仓库根目录

---

## 一、项目目标

为 Pioneers Learning Community (PLC) 学生构建实时学习 Copilot：
- 监控学员与 WorkBuddy（AI 编程助手）的对话
- 后台 LLM 分析学习状态（理解/走偏/卡点/进展）
- macOS 桌面浮标实时呈现引导提示给学员
- 导师通过浏览器观察台查看学员情况，**只看不介入**

**用户**：PLC 学习社区（45 名学生 + 17-18 名导师，五学维度：做人/思维/学习/交流/合作）
**当前阶段**：MVP 单机跑通，单学员本地运行

---

## 二、开发历程与版本迭代

### v0.1.0（2026-06-24 ~ 06-30）— MVP 闭环

| 里程碑 | 内容 |
|--------|------|
| 06-24 | 需求澄清：场景锁定为教学辅助，MVP 先单机 |
| 06-30 | E2E 闭环跑通：hook → service → LLM → DB + WS → 浮标 |
| 06-30 | PyObjC NSPanel 浮标（跨 Space 显示，替代 rumps/PyQt6） |
| 06-30 | 多对话区分（session_id 贯穿全链路）+ 技术助教诊断 |
| 06-30 | 自动跟随 WorkBuddy 当前激活对话 |

### v0.2.0（2026-07-01 ~ 07-02）— 导师观察台 + workbuddy.db 重构

| 里程碑 | 内容 |
|--------|------|
| 07-01 | 导师观察台模块实现（auto-dev 工作流，50 个测试） |
| 07-02 | **重大发现**：workbuddy.db SQLite（205 条会话，权威数据源），之前只用了 sessions.json（7 条缓存） |
| 07-02 | 数据读取层重构：workbuddy.db 优先，sessions.json 降级 |
| 07-02 | Space/Task 概念调研：cwd 在 workspaces 表 → 空间；不在 → 任务 |
| 07-02 | 旧会话 prompt/ai_summary 补录（从 JSONL 提取 `<user_query>` 标签） |

### v0.3.0（2026-07-02）— MVC 分层 + 端口-适配器

| 里程碑 | 内容 |
|--------|------|
| 07-02 | MVC 分层重构：View → Controller → Service → Model |
| 07-02 | 领域模型 dataclass（models.py）替代全程 dict |
| 07-02 | EventBus（eventbus.py）解耦推送 |
| 07-02 | Service 层（services.py）抽离业务逻辑 |
| 07-02 | 端口-适配器模式（ports.py）：支持 Agent 框架/OS/前端替换 |
| 07-02 | 可替换性审查 + 多学员支持分析 |

---

## 三、当前架构

### 架构图

```
View 层      → 浮标 floating_native / 导师观察台 mentor/ / 未来: 飞书
Controller   → service.py / mentor/routes.py（只做 HTTP 编解码）
Service 层   → AnalysisService / SessionQueryService（依赖端口，可复用）
Ports        → AgentSessionRepository / TranscriptParser / FloatingWindow / Notifier
Adapters     → WorkBuddySessionRepository / WorkBuddyTranscriptParser / MacFloatingWindow / MacNotifier
Model 层     → models.py（领域模型）+ Store（copilot.db）+ wb_db（workbuddy.db 只读）
EventBus     → Service publish → WS subscribe（解耦推送）
```

### 端口-适配器设计

| 端口 | 当前实现 | 切换时 |
|------|---------|--------|
| AgentSessionRepository | WorkBuddySessionRepository（wb_db + wb_sessions） | 实现 CursorSessionRepository |
| TranscriptParser | WorkBuddyTranscriptParser（JSONL 6 种 type） | 实现 CursorTranscriptParser |
| FloatingWindow | MacFloatingWindow（PyObjC NSPanel） | 实现 WinFloatingWindow |
| Notifier | MacNotifier（osascript） | 实现 WinNotifier / PushNotifier |

config.json 增加 `agent.framework` 字段，工厂函数据此创建适配器。

---

## 四、文档索引（按阅读顺序）

### 入门必读

| 文档 | 内容 | 路径 |
|------|------|------|
| **本交接文档** | 项目全貌 + 开发历程 + 架构 + 下一步 | `docs/handover.md`（本文件） |
| README | 项目概述 + 快速开始 + 文件结构 | `README.md` |
| 技术架构文档 | MVC + 端口-适配器架构图 + 数据流 + 各层职责 | `docs/architecture.md` |
| 技术原理文档 | 采集/分析/呈现/数据源/MVC/EventBus 原理 | `docs/technical-principles.md` |

### 数据源与调研

| 文档 | 内容 | 路径 |
|------|------|------|
| WorkBuddy 文件结构 | workbuddy.db schema / 空间任务 / JSONL 格式 / 用户名查找 | `docs/workbuddy-file-structure.md` |

### 产品与需求

| 文档 | 内容 | 路径 |
|------|------|------|
| PRD | 功能需求 F1-F5 + 非功能需求 + 后续迭代 | `docs/prd.md` |

### 架构方法论

| 文档 | 内容 | 路径 |
|------|------|------|
| 架构审视知识库 | 10 维度审查清单 + MVP 权衡准则 | `docs/architecture-review-knowledge-base.md` |
| 架构设计提示词 | 通用架构设计 prompt（已泛化） | `docs/architecture-design-prompt.md` |

---

## 五、当前状态

### 运行状态
- ✅ 后台服务运行中：`http://127.0.0.1:8765`（uvicorn）
- ✅ 导师观察台可访问：`http://127.0.0.1:8765/mentor/`
- ✅ hook 已注册到 `~/.workbuddy/settings.json`
- ✅ 112 个测试全绿

### Git 历史（最近 10 条）
```
docs: MVC 架构文档全面更新
refactor: MVC 分层架构 — Service 层 + 领域模型 + EventBus
refactor: 整体架构清理 — 消除所有旧逻辑残留
docs: 文档全面重写 + 旧会话 prompt 补录
docs: 修正 Space/Task 概念并补充 UI 分组规则
fix: 前端缓存版本号 bump v4 + 学员名改为'学员 1'
docs: 更新 README 反映 workbuddy.db 重构 + 导师观察台
refactor: 数据读取层改用 workbuddy.db 替代 sessions.json
fix(mentor): 对话列表与 WorkBuddy 一致 + 标题实时读取
fix(mentor): 学员名展示、对话列表对齐 WorkBuddy、时间线字段映射
```

### 关键配置
- `config.json`：student_id="student-1", student_name="学员 1"
  - （"王佳梁 Michael" 在本地文件未找到，Jerry 是 Michael 的儿子非用户本人）
- LLM：腾讯云 TokenHub + deepseek-v3-0324
- 凭证：`~/.claude/api-vault.env`（用 `set -a; source; set +a` 加载）

---

## 六、已知问题与待优化

### 架构层面（下一步 review 重点）

| # | 问题 | 严重度 | 位置 | 建议 |
|---|------|--------|------|------|
| 1 | **多学员支持缺口** | 高 | 全局 | 数据层已就绪，缺认证/students元数据表/WS过滤/user_id映射 |
| 2 | **service.py 仍有 import 残留** | 中 | service.py:34 | `from .transcript import parse_transcript` 应走端口 |
| 3 | **mentor/timeline.py 与 store.get_timeline 重复** | 中 | mentor/timeline.py | 两套时间线聚合逻辑，应统一 |
| 4 | **floating_native.py 未实现 FloatingWindow 端口** | 中 | floating_native.py | 当前浮标未实现 ports.FloatingWindow 接口 |
| 5 | **hook.py 硬编码 WorkBuddy 协议** | 中 | hook.py:74 | 应通过 EventCollector 端口抽象 |
| 6 | **WS 无认证** | 中 | service.py WS 端点 | 移动端/远程访问需加 token |
| 7 | **config.json 在 .gitignore** | 低 | .gitignore | 合理（含路径），但 config.example.json 需同步更新 |

### 多学员支持详细分析

| 缺口 | 优先级 | 改造内容 |
|------|--------|---------|
| 认证缺失 | P0 | /report 加 token 认证；hook 携带 token 上报 |
| 无学员元数据表 | P1 | 新增 students 表（student_id, display_name, workbuddy_user_id, token, status） |
| WS 不按学员过滤 | P1 | 导师 WS 连接时指定关注学员，服务端过滤后推送 |
| user_id 未打通 | P2 | 注册时绑定 workbuddy_user_id ↔ student_id |

### 可替换性评估

| 场景 | 状态 | 说明 |
|------|------|------|
| 新交互（飞书推送） | ✅ 已支持 | EventBus 加 subscriber 即可 |
| Agent 替换（Cursor） | ✅ 端口已就绪 | 实现适配器，config 改 framework |
| OS 替换（Windows） | ⚠️ 接口已定义 | WinFloatingWindow + WinNotifier 待写 |
| 前端替换（移动端） | ⚠️ HTTP/WS 已有 | 需加 auth + TLS + 推送出口 |

---

## 七、下一步任务（给接手智能体）

### 1. 整体 review 现有架构

阅读以下文档后，审查架构存在的问题：
- `docs/architecture.md` — 技术架构
- `docs/technical-principles.md` — 技术原理
- `docs/workbuddy-file-structure.md` — WorkBuddy 数据源
- 本文档第六节"已知问题与待优化"

重点关注：
- 第六节列出的 7 个架构问题是否准确，是否有遗漏
- Service 层是否真正做到了依赖端口而非具体实现
- 端口-适配器的抽象粒度是否合适（是否有过度设计或不足）
- 多学员支持的改造路径是否合理

### 2. 优化架构并落实开发

审查后：
- 对发现的问题提出优化方案
- 与用户确认后落实到代码
- 同步更新所有文档（architecture / technical-principles / prd / README）
- 确保测试通过（当前 112 个）

### 3. 注意事项

- **不要删除 .workbuddy 目录**（存储项目数据，非临时缓存）
- **config.json 在 .gitignore 中**，修改后不会提交，需手动同步 config.example.json
- **workbuddy.db 只读**（mode=ro），copilot 代码绝不写入 WorkBuddy 文件
- **凭证加载**：`~/.claude/api-vault.env` 需用 `set -a; source; set +a` 导出给子进程
- **Python 环境**：用项目内 venv（`source venv/bin/activate`），不要用系统 Python
- **服务启动**：`uvicorn copilot.service:app --host 127.0.0.1 --port 8765`
- **测试**：`source venv/bin/activate && python -m pytest tests/ -v`

---

## 八、关键文件速查

| 文件 | 作用 | 优先阅读 |
|------|------|---------|
| `copilot/ports.py` | 端口定义 + 工厂函数 | ⭐ 核心 |
| `copilot/services.py` | Service 层（业务逻辑） | ⭐ 核心 |
| `copilot/models.py` | 领域模型 dataclass | ⭐ 核心 |
| `copilot/service.py` | Controller 层（路由） | |
| `copilot/store.py` | CopilotRepo（copilot.db 读写） | |
| `copilot/wb_db.py` | workbuddy.db 只读访问 | |
| `copilot/eventbus.py` | 事件总线 | |
| `copilot/hook.py` | 事件采集（独立进程） | |
| `copilot/floating_native.py` | 浮标 UI（PyObjC） | |
| `copilot/mentor/routes.py` | 导师 API 路由 | |
| `copilot/transcript.py` | JSONL 解析 + TranscriptParser 适配器 | |
| `copilot/llm.py` | LLM 封装 + 分析 prompt | |
| `copilot/wb_sessions.py` | 会话读取 + SessionRepository 适配器 | |
| `copilot/notifier_macos.py` | macOS 通知适配器 | |

---

## 九、用户偏好与习惯

- 偏好直接实用的解决方案，遇到障碍时主动安装新工具或缩减范围
- 习惯让 AI 直接执行命令、搭建测试环境
- 偏好全自动闭环工作流推进端到端开发
- 沟通直接简洁，习惯用请求式语气
- 偏好结构化输出（表格、列表）
- 偏好迭代式设计，先定方向再补细节
- 中文沟通
- "王佳梁 Michael" 是用户本人（本地文件只找到 "Michael"，"Jerry" 是他儿子）
