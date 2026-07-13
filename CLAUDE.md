# WorkBuddy Copilot — 项目级规则

> 上层 `~/projects/CLAUDE.md`（RIPER-5）与全局规则仍生效；本文件为项目特定约定。

## 当前工作：目标架构重建（feat/target-arch-rebuild）

- 架构设计：[docs/target-architecture.md](docs/target-architecture.md)
- 复用地图：[docs/reuse-map.md](docs/reuse-map.md)
- 实施计划：[.claude/memory-bank/plans/target-arch-rebuild.md](.claude/memory-bank/plans/target-arch-rebuild.md)（STATUS: APPROVED，Phase 0-4）
- 测试方案：[docs/test-plan-v2.md](docs/test-plan-v2.md)（**本次重建的权威测试方案**；旧 docs/test-plan.md 为重建前导师模块的记录，已被 v2 取代）

## 闭环测试规则（由 closed-loop-test 部署）

### 状态
- 闭环测试：已启用
- 测试方案：docs/test-plan-v2.md（**判定标准已锁定**）
- 过程日志：docs/dev-log.md
- P1 前端验证方案：Playwright（已安装；DevTools MCP 亦可用）
- 编码方式：**codex 子代理**（codex CLI + tcd 可用，支持 worktree 并行 Mode D）。Claude Code 主 Agent **不写业务代码**，只负责准备/部署/P0-P1 验证/监控/集成。

### 编码纪律
1. 每完成一个逻辑单元（一个功能/路由/命令/表），运行对应 P0+P1 验证（见 test-plan-v2.md，命令 `$PY=venv/bin/python`）。
2. P0：test-plan-v2 P0-1~P0-6 逐项执行（含"无残留单机红线"grep、单 worker 断言）。
3. P1：按标注选工具——[后端]用 pytest/Python requests；[前端]用 Playwright；[端到端]先前端操作再验后端；反向通道项按 P1-11~14。
4. 每完成 3 个功能路径，重跑全部 P0 + 已通过 P1（回归）。
5. 按 Phase 0→4 顺序推进（Phase 0 真bug 可与 Phase 1 部分并行）；每阶段独立可回滚。

### 失败处理与停止条件
- FAIL → 读输出 → 分析 → 只改业务代码 → 重验；每次失败记入 docs/dev-log.md（测试项/原因/修复/尝试次数）。
- 停止：单项失败≥5 次 / 振荡(修A破B)≥2 次 / 总修复≥15 次 / P0 连续 3 轮不过 → 停止并报告。

### 验证纪律（锁判定标准，不锁命令）
- test-plan-v2.md 的**判定标准**不允许修改（除非用户批准）；**命令**可按环境调整，调整记入 dev-log。
- 每次修复只改业务代码，不放宽判据。

### 防假绿铁律（每条自动化用例必须满足）
- **见过红（负控）**：宣布通过前先证明"功能被打断时它会变红"。从没红过的用例无效。判据：「这个功能现在彻底坏了，这条用例会红吗？」答不出 yes 不算通过。
- **禁止 mock 掉被测对象**：Service 集成测试用**真临时 Store + 假 LLM（固定 dict）**真实驱动 handle_stop（见 test-plan P1-5），禁止 patch 掉 analysis_svc 再断言"调了它"。
- **fixture 有内容 + 断言内容**：transcript/LLM 用确定性输入，断言输出内容而非"非空/没报错"。
- **验红线**：P0-6 必须真能抓到残留的 `workbuddy.db`/`parent.parent`/`iter_recent_transcripts`（服务端）。
- **单 worker**：P0-5 必须证明多 worker 被拒。

### 关键红线（本项目特有）
- 服务器**绝不**读学员机 `~/.workbuddy/workbuddy.db` / JSONL / 任何本地 FS；一切学员状态经 hook 上报入 copilot.db（唯一权威源）。
- hook 保持 **stdlib-only 零依赖 + fire-and-forget**：只上传 transcript 尾部原始字节，解析在服务端；异常降级始终 return 0，绝不阻塞 WorkBuddy。
- 归档现有代码到 `legacy/`（不删），高价值资产（floating_native/transcript）**改造非重写**（见 reuse-map）。
- session_id 用单主键（已核实全局唯一）；关联表带 student_id 作隔离。

### 过程日志
每次验证后追加 docs/dev-log.md：实际命令、输出、判定、失败分析、修复、命令调整原因。
若存在 docs/monitor-notes.md，每 Round 前读 `状态: UNREAD` 的建议并标记 READ。

### 阶段内 Review 与交付
- 架构相关阶段（数据模型 Phase 1、服务化 Phase 2、反向通道 Phase 3）完成后触发 code-review，不等最后集中审。
- P0+P1 全过 → dev-log 生成验证摘要 → code-review → 交付。

## 端口说明
- 闭环 P0/P1 用 FastAPI TestClient（进程内，不占端口）。
- 真机 E2E（P3）需绑定 8765；当前有运行中的旧服务占用，跑真机 E2E 前先停旧服务。

## Agent skills

### Issue tracker

Local markdown under `.scratch/` (GitHub Issues disabled on this repo). See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the canonical label vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo — one `CONTEXT.md` + `docs/adr/` at the root. See `docs/agents/domain.md`.
