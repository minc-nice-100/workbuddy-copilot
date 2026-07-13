---
title: WorkBuddy Copilot 跨平台学员客户端实施计划
date: 2026-07-10
status: active
audience: ai
tags: [implementation-plan, cross-platform, student-client, tdd]
---

# Cross-Platform Student Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留 macOS PyObjC 浮标的前提下，抽取可三系统测试的 Student Core，改造 Hook spool，修复上传状态假绿，并交付 Windows 数据/Hook/安装/无头 Agent 框架。

**Architecture:** Student Core 持有 WS/HTTP、spool、去重和上传编排；macOS/Windows 窄适配器只处理 WorkBuddy 路径、DB/JSONL、Hook/安装与 UI。服务端以 UploadRequestService 管理内容传输和 LLM 诊断双轴状态。

**Tech Stack:** Python 3.13, FastAPI, SQLite, asyncio/websockets, stdlib Hook, PyObjC macOS adapter, PowerShell Windows installer, pytest, Playwright.

---

## 执行约束

- 主 Agent 不写业务代码；每个 Task 由新鲜编码子代理实施，之后分别做规格审查与质量审查。
- 每个行为先写失败测试并保留见红证据，再做最小实现。
- 不修改或放宽 v2 原判据；v3 只做加强与分层。
- 不重置/覆盖现有工作树，不提交 `michael-portfolio/` 等无关文件。
- 只在 Windows W0 证据充足后声称 Windows WorkBuddy adapter 可 rollout。

## 目标文件边界

| 路径 | 单一职责 |
|------|----------|
| `copilot/student_core/models.py` | 平台无关的事件、会话、状态和 typed failure |
| `copilot/student_core/spool.py` | 原子入队、遍历、ack 删除和坏文件隔离 |
| `copilot/student_core/transport.py` | 学员 REST/WS 请求、认证头和网络错误映射 |
| `copilot/student_core/coordinator.py` | spool 消费、断线、去重、上传命令与进度编排 |
| `copilot/student_core/agent.py` | 无头运行时启动/停止和循环管理 |
| `copilot/student_platform/workbuddy.py` | configDir 探测、SQLite/JSONL 读取与标准化 |
| `copilot/student_platform/macos.py` | macOS 路径、Hook 命令与 PyObjC 桥接 |
| `copilot/student_platform/windows.py` | Windows 候选根、Git Bash Hook 和 W0 探测 |
| `copilot/upload_service.py` | upload request 双轴状态、重试和状态查询 |
| `copilot/hook.py` | stdlib-only 有界尾读与本地 spool 写入 |
| `copilot/floating_native.py` | 仅保留 macOS 渲染、输入和 UI 线程调度 |

### Task 0: 固化当前可复现基线

**Files:**
- Preserve: 当前已修改的 WorkBuddy 代码、测试和文档
- Exclude: `michael-portfolio/`

- [ ] **Step 1: 重跑当前全量测试**

Run: `venv/bin/python -m pytest -q`

Expected: `264 passed, 1 warning` 或更高，且 0 failed。

- [ ] **Step 2: 列出待固化文件并排除无关内容**

Run: `git status --short && git diff --check`

Expected: WorkBuddy 相关修改无 whitespace error；`michael-portfolio/` 不在待提交列表。

- [ ] **Step 3: 提交当前基线**

Run:

```bash
git add -u
git add AGENTS.md docs/mentor-ui-fixed.png pytest.ini tests/test_deploy_config.py tests/test_prompt_config.py tests/test_public_auth.py tests/test_server_redlines.py tests/test_upload_requests.py
git commit -m "chore: checkpoint public client baseline"
```

Expected: 单独基线提交，不包含无关组合。

### Task 1: 建立 test-plan-v3 与三系统导入门

**Files:**
- Create: `docs/test-plan-v3.md`
- Create: `tests/test_platform_imports.py`
- Create: `requirements-core.txt`
- Create: `requirements-server.txt`
- Create: `requirements-macos.txt`
- Create: `requirements-windows.txt`
- Modify: `requirements.txt`
- Modify: `pytest.ini`

- [ ] **Step 1: 先写平台隔离失败测试**

```python
def test_student_core_does_not_import_platform_modules():
    forbidden = {"AppKit", "Foundation", "objc", "fcntl"}
    imported = import_tree("copilot.student_core")
    assert forbidden.isdisjoint(imported)

def test_dependency_files_keep_pyobjc_out_of_core():
    assert "pyobjc" not in Path("requirements-core.txt").read_text().lower()
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/test_platform_imports.py -q`

Expected: FAIL，因为 Student Core 和分层依赖文件尚不存在。

- [ ] **Step 3: 建立最小包与依赖分层**

```text
requirements-core.txt: websockets
requirements-server.txt: fastapi, uvicorn[standard], httpx
requirements-macos.txt: -r core + pyobjc-core + pyobjc-framework-Cocoa
requirements-windows.txt: -r core
requirements.txt: -r server + -r macos
```

- [ ] **Step 4: 写 v3 可执行判据**

`docs/test-plan-v3.md` 必须包含 Contract/Unit/Integration/Component/Real-machine 矩阵、breaker、critical-skip=fail、W0/W1、所有 v2 业务红线映射与确切命令。

- [ ] **Step 5: 验证并提交**

Run: `venv/bin/python -m pytest tests/test_platform_imports.py -q && git diff --check`

Expected: PASS。

Commit: `test: establish cross-platform test lanes`

### Task 2: 修正 LLM 失败与同 SHA 诊断重试

**Files:**
- Modify: `copilot/llm.py`
- Modify: `copilot/store.py`
- Modify: `copilot/service.py`
- Modify: `copilot/wb_upload.py`
- Modify: `tests/test_llm.py`
- Modify: `tests/test_transcript_upload_api.py`
- Modify: `tests/test_wb_upload.py`

- [ ] **Step 1: 先写生产 LLM 错误不得伪装 done 的测试**

```python
async def test_bulk_analysis_marks_failed_when_llm_returns_degraded_result():
    result = AnalysisOutcome.failed("timeout")
    await run_uploaded_analysis(fake_llm=result)
    assert store.get_raw_transcript("sess-1")["analysis_status"] == "failed"

def test_manifest_includes_analysis_status_for_same_sha_retry():
    assert manifest["sess-1"] == {"sha": "abc", "analysis_status": "failed"}
```

- [ ] **Step 2: 运行目标测试见红**

Run: `venv/bin/python -m pytest tests/test_llm.py tests/test_transcript_upload_api.py tests/test_wb_upload.py -q`

Expected: FAIL，因为现在 fallback 与成功无结构化区分，manifest 只返 SHA。

- [ ] **Step 3: 实现结构化结果与 manifest**

```python
@dataclass(frozen=True)
class AnalysisOutcome:
    ok: bool
    value: dict[str, Any]
    error: str = ""

def should_upload(local_sha: str, remote: dict[str, str]) -> bool:
    return remote.get("sha") != local_sha or remote.get("analysis_status") == "failed"
```

- [ ] **Step 4: 验证同 SHA 只重跑诊断**

Run: `venv/bin/python -m pytest tests/test_llm.py tests/test_transcript_upload_api.py tests/test_wb_upload.py -q`

Expected: PASS，且已存内容不重复写 messages/raw。

- [ ] **Step 5: 全量回归并提交**

Run: `venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `fix: make transcript analysis failures retryable`

### Task 3: 建立 UploadRequestService 和双轴状态

**Files:**
- Create: `copilot/upload_service.py`
- Create: `tests/test_upload_service.py`
- Modify: `copilot/app_context.py`
- Modify: `copilot/store.py`
- Modify: `copilot/service.py`
- Modify: `tests/test_upload_requests.py`

- [ ] **Step 1: 写合法/非法转换的失败测试**

```python
def test_transfer_and_analysis_states_are_independent(store, service):
    rid = service.create("mentor", "student-a")
    service.mark_transfer(rid, "running")
    service.mark_transfer(rid, "stored")
    service.mark_analysis(rid, "failed", error="timeout")
    row = store.get_upload_request(rid)
    assert row["transfer_status"] == "stored"
    assert row["analysis_status"] == "failed"

def test_terminal_transfer_state_cannot_move_backwards(service, rid):
    service.mark_transfer(rid, "stored")
    with pytest.raises(InvalidStateTransition):
        service.mark_transfer(rid, "running")
```

- [ ] **Step 2: 运行目标测试见红**

Run: `venv/bin/python -m pytest tests/test_upload_service.py tests/test_upload_requests.py -q`

Expected: FAIL，因为双轴字段和 Service 不存在。

- [ ] **Step 3: 增量迁移双轴字段并实现 Service**

```python
TRANSFER = {
    "pending": {"running", "failed"},
    "running": {"stored", "failed"},
    "failed": {"running"},
    "stored": set(),
}
ANALYSIS = {
    "not_requested": {"pending"},
    "pending": {"running", "failed"},
    "running": {"done", "failed"},
    "failed": {"pending"},
    "done": set(),
}
```

Controller 只调 `UploadRequestService`；旧 `status` 在过渡期保留读兼容。

- [ ] **Step 4: 验证迁移、状态机和旧 API 兼容**

Run: `venv/bin/python -m pytest tests/test_upload_service.py tests/test_upload_requests.py tests/test_transcript_upload_api.py -q`

Expected: PASS。

- [ ] **Step 5: 全量回归并提交**

Run: `venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `refactor: centralize upload request state transitions`

### Task 4: 导师台真实状态闭环

**Files:**
- Modify: `copilot/service.py`
- Modify: `copilot/static/mentor/app.js`
- Modify: `copilot/static/mentor/index.html`
- Modify: `copilot/static/mentor/style.css`
- Modify: `tests/test_upload_requests.py`
- Modify: `tests/test_mentor_frontend.py`
- Modify: `tests/e2e/test_mentor_ui.py`

- [ ] **Step 1: 写真状态查询、失败原因和重试的失败测试**

```python
def test_mentor_can_query_upload_request(client, request_id, mentor_headers):
    body = client.get(f"/api/mentor/upload-requests/{request_id}", headers=mentor_headers).json()
    assert body["transfer_status"] == "stored"
    assert body["analysis_status"] == "failed"
    assert body["analysis_error"] == "timeout"

def test_retry_analysis_keeps_stored_content(client, request_id, mentor_headers):
    response = client.post(f"/api/mentor/upload-requests/{request_id}/retry-analysis", headers=mentor_headers)
    assert response.status_code == 202
```

- [ ] **Step 2: 运行 API 与 Playwright 测试见红**

Run: `venv/bin/python -m pytest tests/test_upload_requests.py tests/test_mentor_frontend.py tests/e2e/test_mentor_ui.py -q`

Expected: FAIL，因为现在只有固定 4 秒提示。

- [ ] **Step 3: 实现查询/重试 API 和前端状态 reducer**

```javascript
state.uploadRequest = {
  requestId: '',
  transferStatus: 'pending',
  analysisStatus: 'not_requested',
  error: ''
};
```

前端使用 WS 事件优先更新，断线时用可取消轮询补充；移除固定延时伪完成。

- [ ] **Step 4: 验证 component 行为与错误 UI**

Run: `venv/bin/python -m pytest tests/test_upload_requests.py tests/test_mentor_frontend.py tests/e2e/test_mentor_ui.py -q`

Expected: PASS，失败诊断显示原因并可只重试诊断。

- [ ] **Step 5: 全量回归并提交**

Run: `venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `feat: show upload and analysis status in mentor UI`

### Task 5: 强化服务端 FS 红线并归档旧 WS

**Files:**
- Modify: `tests/test_server_redlines.py`
- Create: `tests/fixtures/bad_server_local_fs.py`
- Create: `tests/test_server_home_sentinel.py`
- Restore/Move: `copilot/mentor/ws.py` -> `legacy/copilot/mentor_ws.py`

- [ ] **Step 1: 创建必须被抓到的 breaker**

```python
# tests/fixtures/bad_server_local_fs.py
from pathlib import Path
BAD = Path.home() / ".workbuddy" / "workbuddy.db"
```

```python
def test_redline_scanner_catches_fixture_breaker():
    assert scan_server_file(BAD_FIXTURE)
```

- [ ] **Step 2: 运行新测试见红**

Run: `venv/bin/python -m pytest tests/test_server_redlines.py tests/test_server_home_sentinel.py -q`

Expected: FAIL，现在扫描器只覆盖四个硬编码文件。

- [ ] **Step 3: 实现全 server package 扫描与 runtime sentinel**

扫描所有服务端可达模块的 AST/import graph；在测试中把 HOME/USERPROFILE 指向会在被访问时报错的哨兵目录。

- [ ] **Step 4: 恢复旧 WS 到 legacy 并验证 runtime 不可导入**

Run: `venv/bin/python -m pytest tests/test_server_redlines.py -q`

Expected: breaker 被抓到，runtime 树无旧 WS，legacy 副本存在。

- [ ] **Step 5: 全量回归并提交**

Run: `venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `test: harden server local filesystem redlines`

### Task 6: 抽取 Student Core 契约、spool 和 transport

**Files:**
- Create: `copilot/student_core/__init__.py`
- Create: `copilot/student_core/models.py`
- Create: `copilot/student_core/spool.py`
- Create: `copilot/student_core/transport.py`
- Create: `tests/test_student_spool.py`
- Create: `tests/test_student_transport.py`

- [ ] **Step 1: 写原子入队、ack 和网络失败保留测试**

```python
def test_spool_entry_survives_until_ack(tmp_path):
    spool = EventSpool(tmp_path)
    event_id = spool.enqueue(HookEvent(event="Stop", session_id="s1", transcript_tail="x"))
    assert [e.event_id for e in spool.pending()] == [event_id]
    spool.ack(event_id)
    assert spool.pending() == []

def test_failed_post_does_not_ack(spool, transport):
    transport.post_hook.side_effect = TemporaryNetworkError("offline")
    assert consume_one(spool, transport) is False
    assert len(spool.pending()) == 1
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/test_student_spool.py tests/test_student_transport.py -q`

Expected: FAIL，新包不存在。

- [ ] **Step 3: 实现最小模型、spool 和 transport**

```python
@dataclass(frozen=True)
class HookEvent:
    event: str
    student_id: str
    session_id: str
    cwd: str
    transcript_tail: str
    transcript_path: str

@dataclass(frozen=True)
class SpoolEntry:
    event_id: str
    payload: HookEvent
```

spool 写入使用同目录临时文件 + `os.replace`；坏 JSON 移至 `quarantine/`；transport 只返可分类的成功/临时/永久错误。

- [ ] **Step 4: 运行 Student Core 与平台导入门**

Run: `venv/bin/python -m pytest tests/test_student_spool.py tests/test_student_transport.py tests/test_platform_imports.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

Commit: `feat: add platform-neutral student core primitives`

### Task 7: 把 Hook 切换为有界本地 spool

**Files:**
- Modify: `copilot/hook.py`
- Modify: `register_hook.py`
- Modify: `install.sh`
- Modify: `tests/test_hook.py`
- Create: `tests/test_hook_subprocess.py`

- [ ] **Step 1: 先写无网络、无全文、真子进程 deadline 测试**

```python
def test_stop_hook_writes_bounded_spool_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    assert hook.main() == 0
    event = read_only_spool_event(tmp_path)
    assert len(event["transcript_tail"].encode()) <= 256 * 1024
    assert "transcript_full" not in event

def test_hook_subprocess_exits_under_two_seconds():
    completed = subprocess.run(HOOK_CMD, input=BAD_STDIN, text=True, timeout=2)
    assert completed.returncode == 0
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/test_hook.py tests/test_hook_subprocess.py -q`

Expected: FAIL，当前 Hook 会读 full 并调网络。

- [ ] **Step 3: 实现 stdlib-only spool writer**

Hook 内不 import `copilot.student_core`；用内建 `json/os/tempfile/uuid`完成同协议写入。配置优先级：`COPILOT_SPOOL_DIR` -> config `student.spool_dir` -> script-local `spool/`。

- [ ] **Step 4: 更新注册/安装命令注入 spool 路径**

现有 Hook 合并策略保留；命令中不再需要 server URL/token，只需 student_id/config/spool。

- [ ] **Step 5: 验证、全量回归和提交**

Run: `venv/bin/python -m pytest tests/test_hook.py tests/test_hook_subprocess.py -q && venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `refactor: make WorkBuddy hook local and nonblocking`

### Task 8: 实现 Student Coordinator 和无头 Agent

**Files:**
- Create: `copilot/student_core/coordinator.py`
- Create: `copilot/student_core/agent.py`
- Create: `start_student_agent.py`
- Create: `tests/test_student_coordinator.py`
- Create: `tests/test_student_agent.py`

- [ ] **Step 1: 写 spool 消费、去重和上传命令失败测试**

```python
async def test_coordinator_acks_only_after_server_accepts(spool, transport):
    transport.post_hook.return_value = Accepted()
    await coordinator.flush_spool_once()
    assert spool.pending() == []

async def test_duplicate_command_runs_once(coordinator):
    await coordinator.handle_command({"request_id": "r1", "command": "upload_conversations"})
    await coordinator.handle_command({"request_id": "r1", "command": "upload_conversations"})
    assert coordinator.uploader.calls == ["r1"]
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/test_student_coordinator.py tests/test_student_agent.py -q`

Expected: FAIL，coordinator/agent 不存在。

- [ ] **Step 3: 实现最小循环与幂等状态**

```python
class StudentCoordinator:
    def __init__(self, spool, transport, uploader):
        self.spool = spool
        self.transport = transport
        self.uploader = uploader
        self.inflight: set[str] = set()

    async def flush_spool_once(self) -> int:
        accepted = 0
        for entry in self.spool.pending():
            if await self.transport.post_hook(entry.payload):
                self.spool.ack(entry.event_id)
                accepted += 1
        return accepted

    async def handle_upload_request(self, request_id: str, session_id: str | None) -> None:
        if request_id in self.inflight:
            return
        self.inflight.add(request_id)
        try:
            await self.uploader.upload(request_id=request_id, session_id=session_id)
        finally:
            self.inflight.discard(request_id)

class StudentAgent:
    def __init__(self, coordinator, sleeper):
        self.coordinator = coordinator
        self.sleeper = sleeper
        self.stopping = False

    async def run(self) -> None:
        while not self.stopping:
            await self.coordinator.flush_spool_once()
            await self.sleeper(1.0)

    async def stop(self) -> None:
        self.stopping = True
```

Agent 使用可注入 sleeper/clock，不在测试里真 sleep。

- [ ] **Step 4: 验证网络断开、重启恢复和去重**

Run: `venv/bin/python -m pytest tests/test_student_coordinator.py tests/test_student_agent.py -q`

Expected: PASS。

- [ ] **Step 5: 全量回归并提交**

Run: `venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `feat: add resilient headless student agent`

### Task 9: 抽取 WorkBuddyData 并改造 macOS 客户端

**Files:**
- Create: `copilot/student_platform/__init__.py`
- Create: `copilot/student_platform/workbuddy.py`
- Create: `copilot/student_platform/macos.py`
- Modify: `copilot/wb_sync.py`
- Modify: `copilot/wb_upload.py`
- Modify: `copilot/floating_native.py`
- Create: `tests/fixtures/workbuddy/macos/manifest.json`
- Create: `tests/test_workbuddy_adapter.py`
- Modify: `tests/test_wb_sync.py`
- Modify: `tests/test_wb_upload.py`
- Modify: `tests/test_floating_native_phase3.py`

- [ ] **Step 1: 写真 SQLite/FS fixture 和 typed failure 测试**

```python
def test_adapter_reads_sessions_and_workspaces_from_fixture(adapter):
    sessions = adapter.list_sessions()
    assert sessions[0].session_id == "session-space"
    assert sessions[0].group_type == "space"

def test_schema_mismatch_is_not_empty_success(adapter_with_missing_table):
    result = adapter_with_missing_table.probe()
    assert result.failure.code == "schema_mismatch"
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/test_workbuddy_adapter.py tests/test_wb_sync.py tests/test_wb_upload.py -q`

Expected: FAIL，统一 adapter 不存在，现有 wb_sync 无真 schema 测试。

- [ ] **Step 3: 实现共享读取和 macOS 适配**

`WorkBuddyDataAdapter` 接受已解析 config_dir，从 DB 枚举 session，通过目录/JSONL session_id 建 transcript 索引。`wb_sync`/`wb_upload` 变成保持 CLI 兼容的薄包装。

- [ ] **Step 4: 把 floating_native 的非 UI 调用切到 Coordinator**

保留 PyObjC 类/绘制方法；WS/上传/补拉/去重通过 coordinator 回调进 UI 主线程。

- [ ] **Step 5: 验证 macOS 契约和全量回归**

Run: `venv/bin/python -m pytest tests/test_workbuddy_adapter.py tests/test_wb_sync.py tests/test_wb_upload.py tests/test_floating_native_phase3.py -q && venv/bin/python -m pytest -q`

Expected: 0 failed。

Commit: `refactor: move mac student behavior into shared core`

### Task 10: Windows W0 探测、适配框架和安装器

**Files:**
- Create: `copilot/student_platform/windows.py`
- Create: `probe_windows_workbuddy.ps1`
- Create: `install_windows.ps1`
- Create: `tests/test_windows_adapter.py`
- Create: `tests/test_windows_install_contract.py`
- Create after real-machine evidence: `tests/fixtures/workbuddy/windows/manifest.json`

- [ ] **Step 1: 写已证实候选根、环境覆盖和 blocked 语义测试**

```python
def test_windows_config_dir_prefers_workbuddy_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBUDDY_CONFIG_DIR", str(tmp_path))
    assert probe_windows_config_dir().path == tmp_path

def test_missing_real_fixture_reports_blocked():
    result = WindowsWorkBuddyProbe().probe()
    assert result.status in {"ready", "blocked"}
    assert result.status != "supported_without_evidence"
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/test_windows_adapter.py tests/test_windows_install_contract.py -q`

Expected: FAIL，Windows 模块和 PowerShell 产物不存在。

- [ ] **Step 3: 实现不猜未知算法的框架**

候选根只使用 `WORKBUDDY_CONFIG_DIR`、官方默认根、ProgramData 已存在目录；transcript 通过 session_id 扫描元数据，不实现猜测 encode_cwd。PowerShell 安装器使用独立 venv/依赖、原子备份 settings，注册 Git Bash Hook 并启动 headless Agent。

- [ ] **Step 4: 执行可用的 Windows 真机 W0**

Run on Windows: `powershell -ExecutionPolicy Bypass -File .\probe_windows_workbuddy.ps1`

Expected: 仅输出脱敏元数据。如本轮无 Windows 机，保留 `blocked: real-machine evidence missing`，不伪报 rollout。

- [ ] **Step 5: 验证并提交**

Run: `venv/bin/python -m pytest tests/test_windows_adapter.py tests/test_windows_install_contract.py tests/test_platform_imports.py -q`

Expected: PASS，且未见证真机的部分显式 blocked。

Commit: `feat: add Windows WorkBuddy headless client framework`

### Task 11: 建立真 Student Agent -> Server -> Mentor UI E2E

**Files:**
- Create: `tests/e2e/test_student_agent_system.py`
- Modify: `tests/e2e/test_mentor_ui.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: 写真进程/真 socket 失败测试**

```python
def test_hook_agent_server_browser_round_trip(system):
    system.run_hook({"hook_event_name": "Stop", "session_id": "s1"})
    system.agent.flush_until_idle()
    expect(system.mentor_page.locator('[data-session-id="s1"]')).to_be_visible()

def test_offline_spool_recovers_after_agent_restart(system):
    system.stop_server()
    system.run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s2"})
    assert system.spool_count() == 1
    system.start_server()
    system.restart_agent()
    system.wait_for_spool_empty()
```

- [ ] **Step 2: 运行失败测试**

Run: `venv/bin/python -m pytest tests/e2e/test_student_agent_system.py -q`

Expected: FAIL，系统 fixture 和真无头 Agent 链路尚未接通。

- [ ] **Step 3: 实现临时 HOME/DB/端口 0 的系统 fixture**

fixture 启动真 uvicorn、真 WS、真 Student Agent 和真 Playwright 页面；只对 LLM 使用确定性 fake，不对被测 Service/WS 做 mock。

- [ ] **Step 4: 验证实时、断线、补拉和状态闭环**

Run: `venv/bin/python -m pytest tests/e2e/test_student_agent_system.py -q`

Expected: PASS，0 skip。

- [ ] **Step 5: 全量回归并提交**

Run: `venv/bin/python -m pytest -q`

Expected: 0 failed，关键 Playwright lane 0 skip。

Commit: `test: cover real student agent system flow`

### Task 12: 文档同步、阶段 Review 与最终验证

**Files:**
- Modify: `docs/target-architecture.md`
- Modify: `docs/prd.md`
- Modify: `README.md`
- Modify: `docs/workbuddy-file-structure.md`
- Modify: `docs/dev-log.md`
- Modify after diff approval: `AGENTS.md`

- [ ] **Step 1: 更新权威文档与历史标记**

`docs/test-plan-v3.md` 标为 active，v2 标为 historical/outdated 但不删除。README 移除 MVC、已归档文件和旧测试数量。Windows 文档明确 headless 和 W0/W1 状态。

- [ ] **Step 2: 生成 AGENTS.md 候选 diff，不直接更改**

Run: `git diff --no-index AGENTS.md /tmp/workbuddy-copilot-AGENTS.candidate.md`

Expected: 只更新 test-plan-v3 权威路径、Hook spool 红线和新阶段顺序。按项目配置规则展示 diff 后才落地。

- [ ] **Step 3: 对数据/服务、Student Core/Hook、Windows 适配分别执行独立 Review**

Review 只读输出：`[severity] file:line - issue`。高优问题修复后重跑对应测试并 re-review。

- [ ] **Step 4: 执行最终 P0/P1/component 验证**

Run:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m pytest tests/test_server_redlines.py -q
COPILOT_WORKERS=2 venv/bin/python -c "from copilot.app_context import assert_single_worker; assert_single_worker()"
git diff --check
```

Expected: 全量 0 failed；redline PASS；多 worker 命令必须非 0；diff check 无输出。

- [ ] **Step 5: 追加 dev-log 验证摘要并提交**

Commit: `docs: complete cross-platform client verification record`

## 计划自审映射

| 规格要求 | 覆盖 Task |
|----------|-----------|
| 三系统可导入/依赖隔离 | 1, 6, 10 |
| LLM/同 SHA 假绿 | 2 |
| 双轴状态与导师 UI | 3, 4 |
| 完整 FS 红线与 breaker | 5 |
| Hook 本地 spool/fire-and-forget | 6, 7, 8 |
| macOS 保留 PyObjC 且抽共享核心 | 8, 9 |
| Windows 数据/Hook/安装/无头 Agent | 8, 10 |
| 真 component/E2E | 11 |
| 文档、Review、回归与停止条件 | 12 |

## 执行方式

项目 `AGENTS.md` 已指定子代理编码，且用户已要求直接开发、测试和交付。因此采用 **Subagent-Driven** 执行，不再询问 Inline/Subagent 选择。
