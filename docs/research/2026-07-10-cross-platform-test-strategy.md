---
title: Windows/macOS 双学员客户端测试策略审计
date: 2026-07-10
status: active
audience: both
tags: [research, testing, cross-platform]
---

# Windows/macOS 双学员客户端测试策略审计（候选 v3）

> 日期：2026-07-10
> 性质：只读审计 + 候选测试设计；**不修改**已锁定的 `docs/test-plan-v2.md`，不代表已批准架构变更。
> 范围：当前工作树、264 项 pytest、学员端本地数据/Hook/浮标、中心服务端与导师台。
> 证据口径：文中“事实”均来自当前工作树或本次命令实测；“建议/候选”是 v3 提案。Windows WorkBuddy 安装信息未知之处明确列为待实机验证，不猜路径。

## 1. 结论先行

1. **当前 macOS 基线是真绿，但不是跨平台绿。** 本次在 Darwin arm64 / Python 3.13.12 上重跑 `venv/bin/python -m pytest -q`，结果为 **264 passed, 1 warning, 13.19s**，与最近日志记录一致（`docs/dev-log.md:362-379`）。
2. **Windows CI 目前连收集/导入门都过不了。** 服务端组合根无条件 `import fcntl`（`copilot/app_context.py:8-16`），学员浮标无条件导入 PyObjC/AppKit（`copilot/floating_native.py:29-94`），运行依赖又无条件列出 PyObjC（`requirements.txt:1-6`）。这是已验证源码事实；因此不能把现有 264 项外推成 Windows/Linux 可运行。
3. **当前所谓导师台 E2E 实际是前端 component test。** 测试文件明确“不启后端”，所有 REST/WS 都由 Playwright route mock 接管（`tests/e2e/test_mentor_ui.py:2-18,110-172`）。它很好地验证真实 `app.js` 的渲染/XSS/颜色，但没有验证真实 FastAPI、鉴权、WebSocket、断线补拉或反向消息贯通。
4. **PyObjC 浮标自动化没有验证真实原生 UI。** 15 项测试主要用 `FakeApp`、替换 `AppHelper.callAfter`、替换线程/上传函数来调用类方法（`tests/test_floating_native_phase3.py:94-207,272-458`）；v2 自己也把 NSPanel 视觉与跨 Space 定义为真机人类残留（`docs/test-plan-v2.md:93-104,155-168`）。
5. **本地 WorkBuddy 数据读取缺少“真 schema fixture”集成测试。** `wb_sync` 直接假设 `~/.workbuddy/workbuddy.db`、`sessions/workspaces` 列结构（`copilot/wb_sync.py:24-34,68-111`），但 `tests/test_wb_sync.py` 的 4 项测试只测分组和 URL，未实际构造 SQLite schema 或调用 `read_sessions/read_workspaces`（`tests/test_wb_sync.py:1-67`）。现有文件结构结论来自一台 macOS 真机（`docs/workbuddy-file-structure.md:1-5,23-51`），不能外推 Windows。
6. **Windows 已从“明确不做”变成新目标，属于架构范围升级。** 目标架构仍把 Windows 适配器列为 MVP 不做（`docs/target-architecture.md:218-224`），且技术栈锁定 PyObjC NSPanel（`docs/target-architecture.md:259-260`）。实施前需要用户确认：Windows 是本期一等客户端，还是仅先建立可测试适配边界。

## 2. 当前测试真实进度

### 2.1 已有可信资产

| 资产 | 当前证据 | 审计判断 |
|---|---|---|
| 服务器领域/持久层 | 临时 SQLite Store、确定性假 LLM、TestClient 覆盖分析/隔离/消息/上传/恢复 | **高价值，保留**。符合 v2“真 Store + 假 LLM”红线（`docs/test-plan-v2.md:196-201`） |
| 解析与上传纯逻辑 | transcript 25 项、hook 12 项、wb_upload 5 项 | **可迁移到全 OS 的共享核心**，但需补大文件、锁、编码边界、真子进程 |
| WSRegistry | 定向、隔离、超时剔除均有行为测试 | **保留**，补真 socket/断连 E2E |
| 导师台 UI | 21 项 Playwright，断言真实 DOM、XSS、computed color | **保留并改名为 component**，不能充当系统 E2E |
| macOS 浮标编排 | 15 项覆盖 URL、主线程派发、去重、状态回写 | **保留为 presenter/controller 单测**，不等于 PyObjC component/真机 |

### 2.2 假绿与不可复现风险

| 风险 | 严重度 | 证据与影响 |
|---|---:|---|
| 跨平台收集失败 | Critical | `fcntl`、PyObjC/AppKit 均为无条件导入；`floating_native` 还直接耦合 `wb_sync/wb_upload`（`copilot/floating_native.py:26-37`） |
| “E2E”边界被 mock 掉 | Critical | 前端 API/WS 全部 route mock；FE-3/4/6/7/8 没有浏览器→真实服务→真实 WS 的自动化闭环 |
| WorkBuddy schema/path 单机样本化 | Critical | macOS 文档给出 `~/.workbuddy` 布局与 `/`→`-` 编码（`docs/workbuddy-file-structure.md:142-184`）；上传实现也只替换 `/`（`copilot/wb_upload.py:41-53`），Windows 事实未知 |
| 当前会话“自证正确” | High | 浮标把 `last_activity_at` 最新行当 active（`copilot/floating_native.py:845-875`）；测试仅用同一假设的 fake rows（`tests/test_floating_native_phase3.py:152-207`），没有真实 WorkBuddy 激活信号验证 |
| 红线扫描覆盖不全 | High | P0-6 只列举 4 个 server 文件（`tests/test_server_redlines.py:7-19`），字符串拼接负控却只扫描 student allowlist（`:53-67`）；新 server 模块或其他构造法可能漏检 |
| 依赖缺失可被 skip 掩盖 | High | Playwright 模块 `importorskip`，浏览器缺失也 `pytest.skip`（`tests/e2e/test_mentor_ui.py:29-32,77-87`）；CI 没有“关键 lane 禁止 skip”门 |
| Hook deadline 未真实验证 | High | Stop 时无上限读取整篇 transcript，再同步发 5s HTTP（`copilot/hook.py:92-106,127-166`）；现有测试只用小临时文件和 mock HTTP（`tests/test_hook.py:50-79,82-170`） |
| 安装器是 POSIX-only | Critical | `install.sh` 使用 bash、`venv/bin/activate`、软链和 shell env assignment（`install.sh:1-27,38-111`）；`register_hook.py` 生成 `VAR=... python3 ... || true`（`register_hook.py:44-57`），不能视为 Windows 安装方案 |
| 源码字符串断言冒充行为 | Medium | 部署测试只检查脚本文本含关键字（`tests/test_deploy_config.py:10-30`）；P1-16 token 也主要是源码结构检查，而非真实浏览器请求 |
| 时间不确定性 | Medium | v2 禁止靠 sleep 排 latest（`docs/test-plan-v2.md:198-200`），但现有 `tests/test_store.py:95-104` 仍 `sleep(0.01)` |
| 绿灯只存在于脏工作树 | High | 本次 `git status` 显示 31 个跟踪文件修改，`pytest.ini` 与 5 个关键测试文件未跟踪；264 不是当前提交可复现基线。`pytest.ini` 也仅有 `testpaths = tests`，无平台/层级 marker 或 skip policy（`pytest.ini:1-2`） |

## 3. 候选双客户端适配边界（需架构批准）

原则：服务端、协议、状态机、上传编排不感知操作系统；仅真实 OS 差异落到窄适配器。不要恢复已删除的泛化 ports；只为已经存在的差异建边界。

```text
shared student core
  ├─ contracts: Hook/HTTP/WS payload + normalized Session/Message
  ├─ coordinator: reconnect/dedupe/upload/status/current-session policy
  └─ transport: HTTP/WSS（平台无关）
       │
       ├─ WorkBuddyDataAdapter
       │    list_sessions / read_transcript / detect_active_session / probe_schema
       ├─ HookInstallAdapter
       │    locate_settings / build_command / atomic_merge / verify_invocation
       ├─ DesktopViewAdapter
       │    render(view_model) / emit(user_action) / run_on_ui_thread
       └─ ClientStateAdapter
            config/state directory + atomic persistence + permissions

macOS adapters: 已知 PyObjC + 已核实的本机 WorkBuddy fixture
Windows adapters: W0 实机发现门通过后再实现；UI 技术选型另行批准
```

边界约束：

- `student_core` 不得 import AppKit、Win32、`fcntl`，也不得猜 `~/.workbuddy`/Windows 路径。
- WorkBuddy 的路径、cwd 编码、SQLite schema、当前会话判据都是 `WorkBuddyDataAdapter` 的事实输入；共享层把路径当 opaque string，不自行替换斜杠。
- `DesktopViewAdapter` 只接收 view model；WS、去重、上传状态机不得藏在 NSPanel/未来 Windows UI 内。当前 `floating_native` 同时承担 UI、WS、文件读取、上传线程（`copilot/floating_native.py:536-595,1180-1203,1257-1361`），需要先抽共享编排测试缝。
- 中心服务端锁必须有跨平台实现或明确只在 Linux/macOS server lane 运行；但共享 server 单测至少应能在 Windows import/collect。

## 4. 候选 v3 分层测试矩阵

| 层 | 验证对象 | Linux CI | macOS CI | Windows CI | 发布判据 |
|---|---|---|---|---|---|
| Contract | Hook/REST/WS schema、角色 token、事件类型、状态迁移、adapter protocol | 必跑 | 必跑 | 必跑 | 0 fail、0 critical skip；golden 双向兼容 |
| Unit | parser、state reducer、dedupe、backoff、URL、path-opaque logic、fake clock | 必跑 | 必跑 | 必跑 | 全平台同结果 |
| Integration | 真临时 Store+假 LLM；真临时 WorkBuddy fixture DB/FS；adapter→payload | 必跑 server/core | 必跑 mac fixture | **W0 后**跑 Win fixture；W0 前明确 blocked | 不碰真实 HOME/USERPROFILE，不出公网 |
| Component | 真 uvicorn+真 WS；导师台 Playwright；OS adapter 进程/原生框架最小实例 | server+browser | 同左 + PyObjC adapter | 同左 + Windows adapter | 缺依赖失败，不允许 skip 变绿 |
| Hermetic E2E | hook 子进程→真实 loopback server→DB→WS→浏览器/无头 view | 必跑 | 必跑 mac 命令/路径 | W1 后必跑 Windows 命令/路径 | 真实进程、socket、auth、重连；仅 LLM fake |
| Real-machine | 实际部署/TLS | Linux 服务端 | WorkBuddy+NSPanel | WorkBuddy+Windows 浮标 | rollout 前平台分别签字；保留日志/截图/版本清单 |

建议 CI lanes：

1. `core-{linux,macos,windows}`：contract + unit + server import，PR 必过。
2. `integration-{linux,macos,windows}`：临时 DB/FS、客户端 adapter fixture，PR 必过；Windows WorkBuddy 相关在 W0 前显示 **BLOCKED**，不能记为 pass。
3. `component-linux`：真实 FastAPI/WS + Playwright；`component-macos/windows` 加各自 UI/安装适配器。
4. `release-real-macos/windows`：自托管真机或人工门，不与普通 CI 假绿混在一起。

## 5. Fixture、见红、失败注入与隔离

### 5.1 Fixture 规范

- `contracts/v1/`：Stop/UserPrompt、mentor_message、mentor_command、upload status、错误响应 golden JSON；断言内容和 schema，不只“非空”。
- `workbuddy/macos/<build>/`：脱敏合成 `workbuddy.db`、`settings.json`、JSONL、manifest（OS、WorkBuddy build、schema hash、采集日期）。
- `workbuddy/windows/<build>/`：**W0 实机采集并脱敏后才创建**；W0 前禁止复制 macOS 路径/schema 造“Windows fixture”。
- 路径矩阵：Unicode、空格、长路径、相对/绝对、只读、缺失、文件被替换；Windows 分隔符只在 W0 证实后进入 golden。
- 统一注入 `Clock`、ID generator、随机 jitter；禁止 `sleep` 排序。

### 5.2 负控（见红）

每个关键验收 ID 必须登记 breaker，先证明 breaker 会失败：

| 领域 | 必备 breaker |
|---|---|
| 服务端本地 FS 红线 | `tests/fixtures/bad_modules/` 放一个读取 WorkBuddy HOME 的坏模块，扫描器必须抓到；运行时把 HOME/USERPROFILE 指向 forbidden sentinel，任何 server 访问即失败 |
| 平台隔离 | 在 Windows lane 误 import AppKit、在 mac/Linux lane 误 import Windows UI，import gate 必红 |
| WorkBuddy schema | 缺表/缺列/新增列/locked DB/损坏 DB；adapter 返回明确 unsupported/degraded，不 silent empty |
| Hook | stdin 坏 JSON、transcript 消失/无权限/超大/UTF-8 截断、DNS/TLS/超时；进程须在 deadline 内 exit 0 |
| WS | duplicate/out-of-order、半开、超时、sleep/wake、断在“落库后 publish 前”；不得串学员/重复渲染 |
| Auth | student token 访问 mentor、空 public token、token 泄漏日志，均必须红 |
| UI | 移除主线程 dispatch、错误目标 student、状态不持久化、XSS、WS 断线，component/E2E 必红 |

### 5.3 端口、时间、网络、文件系统隔离

- 所有自动化用预绑定 socket/端口 `0`，把实际 URL 注入客户端；禁止占用 8765/18765。
- 单测/集成默认 deny outbound network，仅允许 in-process transport 或 loopback；真实 DeepSeek、公网域名放 release smoke。
- 用 fake clock/sleeper 驱动退避、节流、latest、超时；浏览器用事件/locator 条件，不用固定等待作为正确性判据。
- 每测独立 `HOME`、`USERPROFILE`、`APPDATA`、config、SQLite；服务端测试对真实用户目录设置 forbidden sentinel。
- TestClient 继续用于 controller integration；“E2E”必须是真 uvicorn + 真 socket，不能用 dependency override 替代被测链路。

## 6. Windows 待实机验证门（不猜路径）

### W0：事实发现门（实现 Windows adapter 前）

必须在真实安装的 Windows WorkBuddy 上记录并脱敏：

1. WorkBuddy 版本/build 与 Python 可用方式。
2. settings/hook 配置的真实位置、格式、命令解释器、超时和 stdin payload。
3. 会话 DB 的真实位置、`PRAGMA table_info`、锁/WAL 行为。
4. transcript 根目录、cwd→目录编码、Unicode/空格/盘符/UNC/长路径行为。
5. “当前激活会话”的可靠信号；若没有，不得把最新 activity 伪装成 active。
6. suspend/resume、网络切换、权限/杀软下的行为。

W0 通过物：sanitized fixture + manifest + 探测日志 + 用户确认。未通过时，Windows WorkBuddy-dependent 状态只能是 `BLOCKED: real-machine evidence missing`，不能 skip 后宣称支持。

### W1：适配器验收门

真实 Windows 临时副本上完成：读取 sessions/workspaces、过滤 transcript、构建 Hook 命令、幂等注册/升级、真实子进程 deadline、上传到 loopback 服务端。UI 框架选型需用户另批。

### W2：发布门

实际 WorkBuddy：输入→Hook 上报→导师台；导师消息→目标浮标；离线补拉；sleep/wake；当前会话切换；高 DPI/多屏/置顶；卸载/回滚不破坏原 settings。

## 7. v2 → 候选 v3 迁移映射

| 动作 | v2 项 | v3 处理 |
|---|---|---|
| 保留判据 | P0-1~3、P0-5、P0-7；P1-1~8、P1-11~15、P1-17~22；S1/S2/S4/S5 | 原业务判据全部保留，扩展为三 OS matrix；P1-5 继续真 Store+假 LLM，绝不 mock 被测 service |
| 改写运行门 | P0-4 | 从“一条全量 pytest”拆为 lane；关键 lane 缺依赖/skip 即失败，并发布各 OS 测试清单 |
| 强化（不放宽） | P0-6 | 扫描整个 server package + import graph + forbidden-HOME runtime sentinel + 坏模块负控；student allowlist 改成适配器目录 ownership |
| 改写为可执行行为 | P0-8、P1-9、P1-10、P1-16 | 不再只查脚本文本；分别执行 macOS/Windows 安装命令、Hook 子进程、真实浏览器带 token 请求 |
| 改写平台边界 | P1-23、FE-9、S6 | 共享 current-session policy + OS data adapter contract + fixture integration + W2 真机；未知时显示 unknown，不猜 active |
| 保留并降级命名 | FE-1/2/5、V-1/V-2 现有 route-mock 测试 | 继续作为 `mentor-ui-component`，不再叫 E2E；视觉负控保留 |
| 重建为真 E2E | FE-3/4/6/7/8 | 浏览器接真实服务/WS/临时 Store；真实学生无头 client 收发，验证鉴权、目标隔离、重连补拉、状态闭环 |
| 拆清契约 | S3 | 分开断言 tail（解析/LLM）与 full（归档）字段；真 Hook 子进程上传，服务端 forbidden-FS sentinel |
| 平台化 | P3 | 拆 Linux server、macOS client、Windows client 三份 release gate；记录版本、域名/TLS、真机证据 |
| 淘汰为验收依据 | 文件存在/关键字包含、固定端口、`sleep` 排序、route mock 的“E2E”标签、单纯“264 passed”总数 | 可保留为快速 lint/smoke，但不得单独证明功能完成 |
| 新增 | X-OS、X-CONTRACT、X-WB-SCHEMA、X-HOOK-PROC、X-HOME-DENY、X-REAL-WS、X-W0/W1/W2 | 补跨平台可安装/可收集、协议、真实数据适配、子进程、FS 红线、真 socket、Windows 实机门 |

现有关键红线不变：服务器绝不读学员机 FS；Hook stdlib-only、失败 exit 0；单 worker；双角色 token；owner 隔离；消息定向与幂等；真 Store+假 LLM；每项关键用例必须见过红。

## 8. 最优先 5 个测试缺口

1. **三 OS 安装/导入/收集门**：先解除 `fcntl`/PyObjC 对共享 suite 的收集阻断，建立 CI matrix 和 critical-skip=fail。
2. **WorkBuddyDataAdapter 真 fixture 集成**：macOS 先补真实脱敏 schema；Windows 严格走 W0，包含 active-session 事实验证。
3. **真实系统 E2E**：Hook 子进程/无头学员 client → 真 uvicorn/SQLite/WS → 真浏览器，覆盖 FE-3/4/6/7/8。
4. **平台客户端 component + 真机门**：macOS PyObjC 不再只测 FakeApp；Windows UI 技术选型后补 component，双端都测 sleep/wake/离线/多屏。
5. **防假绿基础设施**：P0-6 全包扫描+runtime sentinel、breaker 登记、端口 0、fake clock、deny network/real HOME、脏工作树与依赖锁可复现。

## 9. 待用户决策

1. **范围**：是否批准把 Windows 从“未来不做”升级为本期一等客户端？推荐：批准“共享核心 + W0/W1”，UI 实现待 W0 和 UI 技术选型后再批。
2. **Windows UI 技术选型**：原生、跨平台 GUI 或其他方案会影响打包与 component 测试；本报告不替用户选择。
3. **发布门强度**：是否要求所有 client 变更必须同时通过 macOS/Windows W2 真机；推荐是，server-only 变更可只要求三 OS core + Linux component，并周期跑双真机。
4. **真实机资源**：由谁提供 Windows WorkBuddy 机器/版本，以及能否设自托管 runner；没有它不能宣称 Windows 支持。
5. **v3 立项方式**：因 v2 判据锁定，建议保留 v2 原文，另建 v3 候选并经用户逐项批准；批准前只增加审计/测试，不改判据。

## 10. 本次审计边界

- 未改业务代码、未改 `docs/test-plan-v2.md`、未更新 research INDEX、未提交 git。
- 未访问或推测 Windows 用户目录；所有 Windows WorkBuddy 事实均留在 W0 门。
- 本报告基于当前未提交工作树；合并/提交后应重新生成一次 `collect-only` 清单和三 OS 基线。
