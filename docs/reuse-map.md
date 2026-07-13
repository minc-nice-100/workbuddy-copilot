# WorkBuddy Copilot — 代码复用地图（重建参考）

> 生成日期：2026-07-02 | 依据：6 组子代理逐模块扫描 × 目标架构（见 [target-architecture.md](target-architecture.md)）
> 用途：从头重建时，codex 子代理据此判断哪些直接搬、哪些改造、哪些重写、哪些弃用。
> 图例：**REUSE_ASIS**=几乎照搬 · **MODIFY**=改造后复用 · **REWRITE**=逻辑参考但重写 · **ARCHIVE**=弃用不带入

统计：**42 项** — 直接复用 7 · 修改 22 · 重写 3 · 归档 10。核心结论：**领域/解析/UI 绘制逻辑价值高可复用，单机假设/分层泄漏/投机抽象整片弃用**。

---

## 一、REUSE_ASIS — 直接复用（7）

| 文件 | 价值 | 说明 |
|------|------|------|
| copilot/config.py | high | 配置加载器，无单机假设。app_context 组合根仍用 `load_config()`。仅 config.json 的 `agent.framework` 键随 ports 删除作废（代码无需改）|
| copilot/eventbus.py | high | 进程内单向 pub/sub，正是目标 EventBus。路由逻辑属订阅者（WSRegistry），总线不动 |
| copilot/static/mentor/style.css | high | 三栏 grid + 状态点 + 时间线着色。仅**追加** mentor_message 气泡 + compose 输入框样式 |
| start_service.sh | high | 单 worker 启动 + api-vault.env 加载。**务必保持单 worker，不加 --workers>1** |
| tests/test_config.py | high | config 纯逻辑单测，契约稳定 |
| tests/test_llm_summary.py | high | LLM 摘要解析单测，纯逻辑 |
| copilot/mentor/__init__.py | none | 包标记，一行 docstring |

## 二、MODIFY — 改造后复用（22，含高价值资产）

### 后端核心

| 文件 | 价值 | 保留 | 必改 |
|------|------|------|------|
| copilot/store.py | high | _conn/_init_schema/_migrate、add_analysis/add_prompt/add_ai_summary、UNION 结构 | 删越层 import wb_sessions + parent.parent 猜根 + 失配返全部；会话查改读新 sessions 表；severity 拆最坏/最新；停 prompt 双存；加 5 新表+FK+CHECK；upsert students 前置 |
| copilot/services.py | high | handle_stop/handle_user_prompt_submit 编排骨架 + 三类 bus.publish payload | 删 transcript_parser/session_repo 端口依赖；session_title 改读 copilot.db；handle_stop 可 BackgroundTask 调；构造注入真 Store+假 LLM；新增 MessageService/RetentionService |
| copilot/transcript.py | high | _extract_text、Message/TranscriptSnapshot、to_text、逐行解析 | 新增 `parse_text(bytes/str)`（入参从路径→原始字节）；删 iter_recent_transcripts（扫本地 FS）+ WorkBuddyTranscriptParser 端口 |
| copilot/service.py | medium | ReportIn、5 查询路由编解码、WS accept/清理框架 | 删全局单例→app_context+Depends；删 parse_transcript 直调；/report→202+BackgroundTask；删 _IncludedRouter hack→标准 include_router；删直连 STORE；WS 池→WSRegistry；加 3 新路由 |
| copilot/llm.py | high | SYSTEM_PROMPT 四层模板、analyze httpx、_parse_json_content、_fallback | 基本照搬；BackgroundTask 化（签名基本不动）；consent 软门控（不硬拦）|
| copilot/models.py | medium | AnalysisResult、Conversation、TimelineEntry、Student | 删死 DTO：Analysis/Prompt/AISummary；新增 MentorMessage/Session |
| copilot/mentor/routes.py | medium | 三 GET 路由路径/契约、APIRouter 结构 | 删 `from .. import service` 反向依赖→Depends 注入；`__dict__` 换显式序列化；新增 POST /api/mentor/message、GET /api/student/messages |
| copilot/notifier_macos.py | medium | severity→sound 映射、subprocess 健壮写法 | 删 ports.Notifier 继承；osascript 改 `on run argv` 传参修注入。（若仅浮标 WS 推送，可能降级 ARCHIVE，待确认）|

### 学员机侧（唯一在学员机运行）

| 文件 | 价值 | 保留 | 必改 |
|------|------|------|------|
| copilot/hook.py | high | _post urllib fire-and-forget、main stdin 解析+降级 return 0 | 删硬编码作者绝对路径；改传 transcript **尾部原始字节**（非本地路径）；加上报 token；student_id 每机唯一（不用 default 兜底）|
| copilot/floating_native.py | high | **NSPanel 跨 Space 4 要素、CoreGraphics 绘制、拖动/点击判定、脉冲、WS 指数退避重连、卡片重建/着色** ——成熟踩坑资产 | 加 `mentor_message` 接收分支（渲染导师提示气泡，不改 AI）；WS 连接带 student_id+token；数据源换新 API |
| register_hook.py | high | settings.json 幂等合并/坏 JSON 重建 | hook_cmd 注入上报 token；student_id 每机唯一 |
| install.sh | medium | venv/依赖/软链/settings.json 幂等合并 | 删 menubar 引导；注入 token + 每机 student_id；对齐 LLM provider |
| start_menubar.sh | medium | venv 激活 + exec 模块 | 重命名 start_float.sh；传 student_id+token |

### 前端

| 文件 | 价值 | 保留 | 必改 |
|------|------|------|------|
| copilot/static/mentor/app.js | high | fetch+render 骨架、severityClass、formatTime、着色、重连脚手架 | 删 L100 DOM 反读→~15 行内存 state 单一数据源；innerHTML→textContent 修 XSS；重连带 last_seen_message_id 补拉；加导师发消息；wsPayloadToTimeline 加 mentor_message 分支 |
| copilot/static/mentor/index.html | high | 三栏语义骨架 | 加导师消息 compose 输入框+发送按钮 |

### 测试（改断言）

| 文件 | 必改 |
|------|------|
| tests/test_transcript.py (high) | parse_transcript(path)→parse_text(bytes)；输入从临时文件→字节串。**可复用价值最高的一份** |
| tests/test_store_mentor.py (high) | 保留全文不截断/外键/UNION 排序；加 NULL 对齐列断言 + sessions/mentor_messages/raw_transcripts 用例；弱化迁移用例 |
| tests/test_hook.py (high) | 断言从 transcript_path → 尾部字节 + 每机 student_id + token；加"解析异常降级仍 return 0"用例 |
| tests/test_llm.py (high) | TestAnalyze 按新签名（consent 软门控）；补"consent 拒绝软降级"用例 |
| tests/test_store.py (medium) | add_report 加 students upsert；add_analysis 按 severity 两语义；删 test_prompt_truncated；补 FK/CASCADE/analysis_pending |
| tests/test_mentor_frontend.py (medium) | 保留结构断言；补 state 单一源/textContent/last_seen 补拉/发消息断言 |
| copilot/__init__.py (low) | 改 docstring 为新拓扑；统一版本号 |

## 三、REWRITE — 逻辑参考但重写（3）

| 文件 | 目标 | 说明 |
|------|------|------|
| copilot/mentor/ws.py | 新增 connections.py (WSRegistry) | 现为单 list 导师池，无浮标池/无 student_id 寻址/无反向分支。参考连接生命周期 try/except 写法，重写为 floats=dict[sid,set]+mentors=set + 按 payload 路由 + gather/wait_for 超时剔除 |
| tests/test_mentor_api.py | Controller+DI+WSRegistry | patch 模块全局→dependency_overrides；WS 池断言改 set/dict；加 3 新路由测试 |
| tests/test_service_routing.py | Controller+Service+EventBus | /report 断言改 202；mock 改 dependency_overrides 注入假 Service；保留事件分流/EventBus 接缝语义；加 BackgroundTask 触发 LLM 验证 |

## 四、ARCHIVE — 弃用不带入（10）

| 文件 | 弃用原因 | 残留价值 |
|------|---------|---------|
| copilot/ports.py | 4 端口+2 工厂全是投机抽象（单实现）；AgentSessionRepository 固化"服务端读学员机 workbuddy.db"红线行为 | TranscriptSnapshot 字段形状（→parse_text 参考）|
| copilot/wb_db.py | 只读学员机 ~/.workbuddy/workbuddy.db，违反"服务器绝不读学员机 FS" | 字段命名/映射知识（供 hook payload 设计）|
| copilot/wb_sessions.py | 依赖 wb_db + 本地 sessions.json，单机假设 + 端口实现 | _parse_iso 小工具（如需单抄）|
| copilot/mentor/timeline.py | merge_timeline 死代码，聚合下沉 store.py SQL UNION | type/content/created_at 字段语义（写 UNION 参考）|
| copilot/floating.py | PyQt6 版浮标，已被 floating_native 取代 | Qt 跨 Space 踩坑备忘（原生实现已不需要）|
| copilot/menubar.py | rumps 菜单栏旧形态，功能弱于 floating_native | last_seen_ts 概念（前端补拉思路参考）|
| demo_wb_db.py | 探索脚本，集单机红线于一身 | workbuddy.db schema 历史参考 |
| sync_test.py | PyQt6 渲染调试脚本，依赖已弃用 floating.py | 无 |
| tests/test_mentor_timeline.py | 测试对象（merge_timeline）被删 | 意图已由 test_store_mentor 覆盖 |
| tests/test_wb_db.py | 建立在"服务端读学员机双源"上，与红线对立 | 替代测试在 test_store_mentor 重建 |

---

## 关键复用资产提示（给 codex）

1. **floating_native.py 是最高价值资产**：NSPanel 跨 Space 配置、CoreGraphics 绘制、拖动/点击判定、脉冲定时器、WS 指数退避重连——都是踩过坑的成熟代码，改造（加 mentor_message 接收分支 + student_id/token）远优于重写。
2. **transcript.py 解析逻辑**（_extract_text/parse 分流）最该保留，仅换喂入方式（路径→字节）。
3. **store.py 写入侧 + UNION 结构** 可搬，读取侧（会话查询）因单机假设需重写。
4. **红线**：任何 `~/.workbuddy/workbuddy.db`、`db_path.parent.parent`、`iter_recent_transcripts` 扫本地 FS 的代码一律不带入——服务器只消费 hook 上报，copilot.db 是唯一权威源。
