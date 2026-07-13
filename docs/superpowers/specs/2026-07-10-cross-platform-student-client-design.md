---
title: WorkBuddy Copilot 跨平台学员客户端设计
date: 2026-07-10
status: active
audience: both
tags: [architecture, student-client, cross-platform, testing]
---

# WorkBuddy Copilot 跨平台学员客户端设计

## 摘要

本设计把只能在 macOS 运行的学员端拆成“共享 Student Core + 窄平台适配器”。保留现有 PyObjC NSPanel；Windows 本期完成 WorkBuddy 数据读取、Hook 注册、安装和无头 Agent，正式 Windows 浮标 UI 后置。

重构同时修复 Hook 阻塞、上传/诊断状态假绿、平台依赖泄漏和测试边界失真。

## 现状与依据

- 当前 macOS 工作树实测 `264 passed, 1 warning`，但关键修改未全部进入当前提交。
- `floating_native.py` 把 PyObjC UI、HTTP/WS、上传、WorkBuddy 读取和状态持久化混在一起。
- Hook 在 Stop 时读整篇 transcript 并同步发 HTTP，不符合 fire-and-forget。
- 内容传输和 LLM 诊断状态混合，可产生失败却显示 done 的假绿。
- Windows 非 ASCII 用户名可触发 WorkBuddy configDir fallback，不能只依赖 `Path.home()`。

详细证据：

- [跨平台测试策略审计](../../research/2026-07-10-cross-platform-test-strategy.md)
- [Windows 文件系统与集成入口核验](../../research/2026-07-10-tencent-workbuddy-windows-filesystem.md)
- [现有目标架构](../../target-architecture.md)
- [现有测试方案 v2](../../test-plan-v2.md)

## 范围

### 本期范围

1. 建立不依赖 AppKit/Win32 的 Student Core。
2. 保留并改造 macOS PyObjC 浮标，不重写 NSPanel。
3. 建立 Windows WorkBuddy 数据、Hook、安装和无头 Agent 适配。
4. Hook 改成有界尾读 + 本地 spool，网络和全文上传交给常驻 Agent。
5. 拆分内容传输与 LLM 诊断状态。
6. 建立三系统分层测试矩阵和 Windows W0/W1 真机门。
7. 把上传编排收窄到具体 Service 边界。

### 非目标

- 不实现正式 Windows 浮标 UI。
- 不用跨平台 GUI 重写双端 UI。
- 不建学员账号、设备注册、每学员 token 或 StudentPrincipal。
- 继续使用共享 student token + 客户端 `student_id`，这是用户明确接受的风险。
- 不恢复泛化 `ports.py`，不全量重写 Store/SQLite。
- Windows 真机事实缺失时不猜 cwd 压缩算法、Hook Python 命令或登录自启。

## 方案决策

| 方案 | 收益 | 代价 | 决策 |
|------|------|------|------|
| 共享 Student Core + 平台适配器 | 保留 macOS UI，共享状态机和上传 | 迁移期新旧路径短期并存 | **采用** |
| 跨平台 GUI 重写 | UI 表面统一 | 丢失 NSPanel，过早锁定 Windows UI | 拒绝 |
| 两套独立客户端 | Windows 可独立开发 | 状态、断线、去重和上传易漂移 | 拒绝 |

决策记录见 [ADR-001](../../../specs/decisions/001-shared-student-core-platform-adapters.md)。

## 目标架构

```text
WorkBuddy -> bounded Hook -> local spool
    |                         |
    +-> local DB/transcript   v
                       shared Student Core
                       - contracts/state
                       - HTTP/WSS transport
                       - spool/retry
                       - upload orchestration
                         |             |
                         v             v
                    macOS adapters  Windows adapters
                    PyObjC + data   data + install + headless
                         |             |
                         +-- HTTPS/WSS-+
                                |
                                v
                        central server -> mentor web UI
```

### Student Core

Student Core 不得 import AppKit、Win32、`fcntl` 或猜 WorkBuddy 路径。它负责 WS 断线重连/补拉、消息去重、spool 消费、会话同步、上传编排和 UI 无关 view model。

### 平台适配器

```text
WorkBuddyData
  probe() -> capabilities/version/config_dir
  list_sessions() -> normalized sessions
  read_transcript(session_id) -> transcript or typed failure
  detect_active_session() -> session_id | unknown

StudentView
  render(view_model)
  notify(event)
  dispatch_to_ui_thread(action)
```

- macOS 保留 PyObjC 绘制、NSPanel、拖动/点击和 UI 主线程调度。
- Windows 本期实现 configDir、SQLite/JSONL、Hook 配置和无头 Agent。
- 共享层把路径视为 opaque value，不猜分隔符、盘符、大小写或 Unicode 归一化。

### 服务端边界

- `UploadRequestService` 管理上传请求、内容传输、诊断、重试和状态聚合。
- Controller 只负责验证与编解码，不直接调 LLM 或编排 Store 私有方法。
- Store 保留 SQLite，仅收窄 upload/analysis 持久化。
- EventBus、WSRegistry 和单 worker 约束保持不变。

## 关键数据流

### Hook 实时路径

1. WorkBuddy 向 Hook stdin 传入事件、session_id、cwd 和 transcript_path。
2. Hook 最多读配置上限的 transcript 尾部，不读整篇。
3. Hook 用临时文件 + 原子更名把小事件写入安装器注入的 spool 目录。
4. Hook 不访问网络，异常降级并始终返回 0。
5. Student Core 消费 spool，只在服务端 2xx 确认后删除本地条目。
6. Stop 后的完整对话由 Student Core 通过 WorkBuddyData 异步读取、过滤和上传。

Hook 真子进程必须在 2 秒硬截止内退出，网络延迟不得影响 Hook。

### 会话与 transcript

- 适配器从真实 configDir 读 `workbuddy.db`，使用只读 SQLite URI 并容忍 WAL/短暂锁竞争。
- 批量 transcript 通过已验证映射、目录元数据或 JSONL session_id 建立，禁止猜 Windows cwd 规则。
- `detect_active_session()` 找不到可靠信号时返回 `unknown`，不把最新 activity 宣称为当前会话。

### 全量上传

1. 导师创建 upload request，服务端先落库再通过 WS 投递。
2. Student Core 领取请求，枚举会话并发送进度。
3. 服务端以 SHA 幂等存储，已存内容不重传。
4. 诊断失败时保留 stored 内容，允许只重试 analysis。
5. 导师台展示传输/诊断的状态、错误和重试入口。

### 导师消息

- 服务端保持先落库、再推送。
- Student Core 持久化并去重后回执 `received`。
- macOS UI 真正渲染后可回执 `displayed`。
- Windows 无头 Agent 本期只回执 `received`，不伪造 `displayed`。

## 状态与失败语义

| 轴 | 合法状态 | 重试语义 |
|----|----------|-------------|
| 内容传输 | `pending -> running -> stored` 或 `failed` | `stored` 可复用；`failed` 可重试 |
| LLM 诊断 | `not_requested -> pending -> running -> done` 或 `failed` | `failed` 可不重传内容直接重试 |

服务端是状态权威源，客户端只上报进度/失败，持久层必须拒绝非法状态回退。

适配器不得用空值掩盖失败，至少区分 `not_installed`、`unsupported_version`、`schema_mismatch`、`busy`、`permission_denied`、`corrupt`、`temporarily_unavailable` 和 `unknown_active_session`。

日志禁止记录 token、对话正文或未脱敏用户目录。

## 测试设计

| 层级 | 验证内容 | Linux | macOS | Windows |
|------|----------|:---:|:---:|:---:|
| Contract | REST/WS、Hook envelope、双轴状态、适配器输出 | 必跑 | 必跑 | 必跑 |
| Unit | 状态机、去重、退避、队列、路径无关逻辑 | 必跑 | 必跑 | 必跑 |
| Integration | 临时 Store + 假 LLM；脱敏 WorkBuddy fixture | server/core | Mac fixture | W0 后 Win fixture |
| Component | 真 uvicorn/WS、真浏览器、无头 Agent | 必跑 | 必跑 | W1 后必跑 |
| Real-machine | WorkBuddy、安装升级、休眠唤醒、原生 UI | Linux server | Mac 发布门 | Windows W0/W1 |

防假绿规则：

- route-mock Playwright 用例作为 component test，不单独证明 E2E。
- 关键 lane 缺依赖时失败，不允许 skip 变绿。
- 每测试隔离 HOME、USERPROFILE、DB、spool 和端口，禁止接触真实用户数据。
- 网络默认只允许 in-process 或 loopback，真 LLM/公网仅在发布冒烟使用。
- 时间、退避和重试使用 fake clock，禁止 `sleep` 决定正确性。
- 每条关键红线登记 breaker，必须先见红。
- 服务端 FS 红线覆盖完整 server package + import graph + forbidden-HOME runtime sentinel。
- Windows fixture 只能由真机脱敏产生；W0 前显示 blocked。

v2 业务判定全部保留。新建 v3 后纳入平台 lane、真 component/E2E、breaker、W0/W1 和双轴状态。v3 激活后取代 v2 权威地位，v2 保留为历史来源。

## Windows 真机门

### W0：事实发现

实现 Windows WorkBuddy-dependent 路径前，用脱敏只读探测固化 WorkBuddy 版本/configDir、Hook 位置/Git Bash/Python 命令、DB schema/WAL/锁、transcript 映射、当前会话信号和登录自启证据。

W0 产物是脱敏 fixture、manifest、版本和探测日志。

### W1：适配器验收

- 从 Windows 真实临时副本读 sessions/workspaces/transcript。
- 幂等注册、升级、验证和恢复 Hook。
- 真子进程 Hook 在 deadline 内写 spool 并退出。
- 无头 Agent 消费 spool、loopback 上传、断线续传和接收导师命令。
- 验证中文用户名、文件锁、休眠唤醒和登录自启。

## 迁移顺序

1. **Phase 0：基线与 v3 测试门**。保留当前工作树；固化 WorkBuddy 相关未提交基线；创建 v3、三系统 import/collect 门、marker 和 critical-skip 规则。
2. **Phase 1：修复已核实假绿**。修正 LLM failure、同 SHA 诊断重试、导师台状态闭环、P0-6 与旧 WS 归档。
3. **Phase 2：抽取 Student Core 与依赖隔离**。抽取 contracts、coordinator、transport、spool、state 和 upload orchestration；拆分 server/core/macos/windows 依赖。
4. **Phase 3：上传 Service 与 Hook spool**。建立 UploadRequestService 和双轴状态；Hook 切 spool；Student Core 接管网络与全文。
5. **Phase 4：macOS 切换**。PyObjC 仅保留 UI；Student Core 接管 WS/API/命令/上传；执行自动回归和真机冒烟。
6. **Phase 5：Windows W0/W1**。先执行脱敏探测，再实现 WorkBuddyData、Hook/安装和无头 Agent。
7. **Phase 6：系统 E2E 与灰度**。Hook 真子进程 -> Student Agent -> 真服务/SQLite/WS -> 真导师浏览器。

## 验收标准

1. Student Core 在 Linux/macOS/Windows 均可 import/collect，不需 PyObjC/Win32。
2. macOS 浮标原有功能、NSPanel 视觉和 WorkBuddy 互动无回归。
3. Hook 不访问网络、不读整篇、在 2 秒硬截止内始终返回 0。
4. spool 事件在服务端确认前不删，断网/重启后可恢复。
5. 内容传输与 LLM 诊断状态独立，同 SHA 可只重跑诊断。
6. 导师台真实展示状态、错误原因和重试结果。
7. 服务端 FS 红线有完整扫描、runtime sentinel 和已见红 breaker。
8. 真 component/E2E 不 mock 被测服务/WS，关键 lane 无 skip 假绿。
9. Windows W0 事实进入 fixture/manifest，W1 前不声称 Windows rollout 完成。
10. v2 原业务红线全部保留，v3 命令和结果可独立复现。

## 回滚与文档

- Student Core 切换前先用契约测试验证新旧行为。
- DB 只做增量迁移，旧字段在过渡期保留读兼容。
- Hook 安装器改 settings 前备份，升级/卸载只移除 Copilot 自有块。
- 旧 `floating_native` 和归档代码在新链路通过前保留。
- 实施同步新建 `docs/test-plan-v3.md`，更新 `docs/target-architecture.md`、`docs/prd.md`、`README.md` 和 `docs/workbuddy-file-structure.md`。
- `AGENTS.md` 只在代码、测试和文档对齐后更新，且必须先展示待修改 diff。

## 质量与停止条件

- 每个逻辑单元先写失败用例并见红，再做最小实现。
- 每阶段执行 P0 + 已通过 P1/component 回归，结果追加 `docs/dev-log.md`。
- 数据模型、服务化、Hook/Student Core 和 Windows 适配阶段各自触发独立 code-review。
- 继承项目停止条件：单项失败≥5 次、振荡≥2 次、总修复≥15 次或 P0 连续 3 轮不过时停止。

## 已批准决策

- 选择共享 Student Core + 平台适配器。
- Windows 本期做数据/Hook/安装/无头 Agent，正式 UI 后置。
- Hook 使用本地 spool，网络与全文移至 Student Core。
- 内容传输与诊断状态拆分。
- 保留共享 student token + 客户端 `student_id`，不建学员身份系统。
- 用户要求不设额外常规确认门，直接开发、测试与交付；只在停止条件、超范围或必须依赖 Windows 外部真机时报告。
