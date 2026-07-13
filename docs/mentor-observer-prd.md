# 导师观察台模块 PRD

## 项目信息
- **项目名称**: WorkBuddy Copilot 导师观察台模块
- **技术栈**: Python 3.13 + FastAPI + SQLite + 原生 HTML/JS
- **项目类型**: 已有项目增量模块

## 背景
现有 WorkBuddy Copilot 系统：hook 采集学员对话 → FastAPI 服务 LLM 分析 → NSPanel 浮标呈现给学员。
需新增导师观察台：导师在浏览器查看学员学习情况，学员提示词原样展示，AI 回答用 LLM 摘要展示。

## 功能列表

### F1: 数据层扩展（prompts + ai_summaries 分表）
- 新建 `prompts` 表：学员提示词全文（原样不截断），含 id/session_id/seq/student_id/created_at/content
- 新建 `ai_summaries` 表：AI 回答客观摘要，含 id/prompt_id(外键)/session_id/student_id/content/created_at
- store.py 新增对应 CRUD 方法（单独建函数不混现有方法——留接缝）
- **验收标准**：prompts 表能存全文（10万字不截断）；ai_summaries 外键关联 prompt_id；旧库迁移不破坏现有数据

### F2: LLM 分析扩展（加 ai_reply_summary 字段）
- llm.py SYSTEM_PROMPT 新增"AI 回答摘要"任务层，schema 加 `ai_reply_summary` 字段
- 一次 LLM 调用同时产出诊断 + 摘要（不增成本）
- UserPromptSubmit 事件时 ai_reply_summary 留空（AI 还没回答）
- Stop 事件时产出完整诊断 + 摘要
- **验收标准**：Stop 事件分析结果含 ai_reply_summary 字段（<=150字客观摘要）；UserPromptSubmit 事件该字段为空字符串

### F3: /report 按事件分流落库
- UserPromptSubmit 事件 → 存 prompts 表（全文）→ 推送导师
- Stop 事件 → 调 LLM 分析 → 存 ai_summaries（摘要）+ analyses（诊断）→ 推送导师
- service.py 引入 `_notify_all(event)` 函数集中广播调用（留接缝：未来改 bus.publish 一行替换）
- **验收标准**：UserPromptSubmit 只存 prompts 不调 LLM；Stop 触发 LLM 并存 summaries+analyses

### F4: 导师 API + WS（独立命名空间）
- 新建 `copilot/mentor/` 子包：routes.py + ws.py + timeline.py
- GET `/api/mentor/students` — 学员列表 + 状态概览
- GET `/api/mentor/sessions/{session_id}/timeline` — 核心接口：三表(prompts+ai_summaries+analyses) UNION 聚合，按时间返回交替时间线，type=prompt/ai_summary/analysis
- WS `/ws/mentor` — 独立客户端池，导师收全量学员事件（与浮标 /ws 隔离）
- **验收标准**：timeline 接口返回按时间排序的混合事件列表；/ws/mentor 与 /ws 物理隔离

### F5: 导师前端 UI
- 新建 `copilot/static/mentor/` 目录：index.html + app.js + style.css
- 三栏布局：左学员列表（状态灯）· 中对话列表 · 右时间线（蓝条=原样提问/紫条=AI摘要/橙条=学习诊断）
- FastAPI StaticFiles serve，纯静态无构建工具
- WS 实时接收事件，fetch 拉取历史
- **验收标准**：浏览器访问 http://127.0.0.1:8765/mentor/ 可见三栏布局；时间线条目按类型着色；WS 连接实时推送

## 依赖关系
- F1（数据层）无依赖，最高优先级
- F2（LLM 扩展）无依赖，可与 F1 并行
- F3（事件分流）依赖 F1 + F2
- F4（导师 API）依赖 F1 + F3
- F5（前端）依赖 F4

## 非功能需求（MVP 取舍）
- 降级：LLM 失败不崩，analysis 字段为空；WS 断连浮标显示历史数据
- 边界：空对话跳过分析；超长提示词 LLM 输入截断
- 可观测性：复用现有 /health，导师 API 加日志
- 数据生命周期：store.py 预留 cleanup 接口
