---
title: 腾讯 WorkBuddy Windows 学员端文件系统与集成入口核验
date: 2026-07-10
status: active
audience: both
tags: [research, windows, workbuddy]
---

# 腾讯 WorkBuddy Windows 学员端：文件系统与集成入口核验

> 日期：2026-07-10
> 范围：腾讯 WorkBuddy 桌面端（不是独立的 CodeBuddy IDE/CLI）Windows 学员机
> 结论口径：**已验证事实 / 合理推测 / 未知**严格分开。WorkBuddy 专属路径不按通用 Windows 习惯臆测。

## 1. 结论先行

1. **Windows 当前主数据根仍是“用户主目录下 `.workbuddy`”**，即通常的 `%USERPROFILE%\.workbuddy`；官方 Windows 5.1.2 安装包的 `product.json` 明确给出 `dataFolderName: ".workbuddy"`，主进程以 `os.homedir()` 拼接该目录，并把 Electron `userData` 改到 `<config>\app`。[官方版本下载页](https://www.codebuddy.cn/docs/workbuddy/Download-History)、[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)
2. **中文 Windows 用户名是一个真实例外**：5.1.2 包内有专门的 `win32-safe-env` 逻辑。若 `USERPROFILE` / `LOCALAPPDATA` / `APPDATA` 含非 ASCII，优先用 8.3 短路径；失败时可迁移到 `C:\ProgramData\WorkBuddy\users\<hash>\.workbuddy`（再降级 `C:\WorkBuddy-env\<hash>`），并设置 `WORKBUDDY_CONFIG_DIR`。所以 Copilot 不能只写死 `Path.home() / ".workbuddy"`。[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)
3. **当前权威会话索引仍是 `<config>\workbuddy.db`，转录仍是 `<config>\projects\<compressedCwd>\<sessionId>.jsonl`**；`sessions` / `workspaces` 的核心字段与 Mac 已验结构一致。数据库启用 WAL、`busy_timeout=5000`，因此读取时要容忍 `-wal` / `-shm` 与短暂锁竞争。[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)
4. **`settings.json` 的当前目标位置是 `<config>\settings.json`**；旧版 Windows 设置可能位于 `%APPDATA%\WorkBuddy\User\settings.json`，当前客户端带一次性迁移逻辑。不要把旧路径当成当前 hook 写入点。[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)
5. **Windows command hook 用 Git Bash，不用 cmd.exe/PowerShell**；格式仍是 `hooks -> event -> [{ matcher?, hooks: [{type:"command", command, timeout?}]}]`，stdin 含 `session_id`、`transcript_path`、`cwd`、`hook_event_name`。这使 POSIX 的 `|| true` 可用，但 `python3` 是否在 hook PATH 中仍须真机确认。[官方 Hook 参考](https://www.codebuddy.cn/docs/cli/hooks)、[官方 Hook 入门](https://www.codebuddy.cn/docs/cli/hooks-guide)
6. **官方安装包自带 PortableGit、Node、Python 压缩运行时**，但未找到公开承诺保证 `python3` 对用户自定义 hook 可直接发现；不能把“包内存在 Python”推导成“hook shell 的 PATH 一定有 python3”。[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)
7. **现有 `wb_upload.encode_cwd()` 在 Windows 必然不安全**：它只替换 `/`，没有处理 `C:` 与 `\`；而 WorkBuddy 的实际 `<compressedCwd>` Windows 算法未在公开文档中给出完整规范。最稳妥入口是直接使用 hook stdin 的 `transcript_path`，批量同步则需从真机 `projects` 目录/JSONL 元数据建立映射，不能自行猜编码。
8. **官方没有证明 WorkBuddy 默认开机/登录自启**；反而远程助理文档要求电脑“保持开机并运行 WorkBuddy”。rollout 必须把“登录后自动启动”视为独立部署项，在真机查注册表/启动目录/计划任务后再定。[官方助理说明](https://www.codebuddy.cn/docs/workbuddy/Claw)

## 2. 证据与版本边界

### 2.1 一手来源

| 来源 | 本报告用途 |
|---|---|
| [Windows 安装指南](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-Win-Guide) | Windows 10+、用户可选择安装路径、开始菜单/桌面快捷方式、客户端内更新 |
| [历史版本下载](https://www.codebuddy.cn/docs/workbuddy/Download-History) | 可复现的腾讯官方 Windows 包链接 |
| [WorkBuddy 更新日志](https://www.codebuddy.cn/docs/workbuddy/Changelog) | 当前公开最新为 5.2.3（2026-07-06）；Windows 路径、中文乱码、日志 EPERM 等确有专项修复 |
| [Hook 参考](https://www.codebuddy.cn/docs/cli/hooks) / [Hook 入门](https://www.codebuddy.cn/docs/cli/hooks-guide) | hook JSON 结构、stdin 字段、Windows 强制 Git Bash、退出码/超时语义 |
| [官方 5.1.2 Windows 包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe) | 对安装包、`app.asar`、内置 CLI 做只读静态检查 |

静态检查快照：`WorkBuddy 5.1.2`，安装包大小 509,443,024 bytes，SHA-256：

```text
2c865b3454439284baa139a60808f76c8c7d82a282dc795bd9cd54a24db09a76
```

安装包是 NSIS/Electron 包；解包可见 `WorkBuddy.exe`、`launcher.exe`、`resources/app.asar`、`resources/vendor/PortableGit.zip`、`node.zip`、`python.zip`。`app.asar/package.json` 标识 `@genie/workbuddy-desktop 5.1.2`，作者为 Tencent Technology (Shenzhen) Company Limited。

### 2.2 时效边界

公开更新日志显示 5.2.3 已发布，但历史下载页本次能稳定复现的最高包为 5.1.2。以下“包内实现”因此是 **5.1.2 已验证**，不是对未来版本的永久协议；真机需回传客户端版本。

## 3. 已验证事实

### 3.1 用户主目录与配置根

5.1.2 的默认规则：

```text
configDir = WORKBUDDY_CONFIG_DIR
         || os.homedir() + "\\.workbuddy"

Electron userData    = configDir + "\\app"
Electron sessionData = configDir + "\\app\\session"
Electron app logs    = configDir + "\\logs"
```

`WORKBUDDY_USER_DATA_DIR` 可单独覆盖 `app` 子目录。主进程还会把 `CODEBUDDY_CONFIG_DIR` 与 `WORKBUDDY_CONFIG_DIR` 统一为解析后的 `configDir`。[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)

#### 非 ASCII 用户名例外

包内实现明确检测 `USERPROFILE`、`LOCALAPPDATA`、`APPDATA` 的非 ASCII 字符：

- 先调用 Windows 8.3 短路径；
- 再尝试 `C:\ProgramData\WorkBuddy\users\<稳定 hash>`；
- 再尝试 `C:\WorkBuddy-env\<hash>`；
- 将原 `.workbuddy` 复制到新根（排除 `binaries`、缓存、日志），写 `.migrated-from` 标记；
- 通过 `WORKBUDDY_CONFIG_DIR` 让当前进程使用新位置。

这意味着在同一台机器上，普通 PowerShell 的 `$HOME` 可能与 WorkBuddy 进程看到的 home/configDir 不同。

### 3.2 当前文件布局

以下均相对“解析后的 configDir”，通常是 `%USERPROFILE%\.workbuddy`：

| 路径 | 状态 | 作用 |
|---|---|---|
| `workbuddy.db` | 已验证 | 当前统一 SQLite 数据库 |
| `workbuddy.db-wal`, `workbuddy.db-shm` | 已验证/运行时出现 | WAL 与共享内存；进程运行时可能存在 |
| `projects\<compressedCwd>\<sid>.jsonl` | 已验证 | 完整事件流/转录 |
| `projects\<compressedCwd>\<sid>.meta.json` | 已验证（可选） | 迁移/补充元数据 |
| `projects\<compressedCwd>\<sid>\subagents\*.jsonl` | 已验证（按会话出现） | 子代理转录 |
| `sessions\<pid>.json` | 已验证 | sidecar PID/心跳；不是对话历史 |
| `settings.json` | 已验证 | 当前用户设置与 hooks |
| `mcp.json`, `models.json` | 已验证 | MCP 与模型配置 |
| `app\`, `app\session\` | 已验证 | Electron user/session data |
| `logs\` | 已验证 | 当前客户端/CLI 日志主树；含 `startup`、MCP runtime 等子目录 |
| `binaries\python\...`, `binaries\node\...` | 已验证 | 托管运行时落点 |
| `.credentials.json`, `connectors\...` | 已验证 | 凭据/连接器，**探测时不得回传内容** |

旧版/迁移源（不是当前权威源）：

- `%APPDATA%\WorkBuddy\User\settings.json`
- `%APPDATA%\WorkBuddy\codebuddy-sessions.vscdb`
- `%LOCALAPPDATA%\WorkBuddyExtension\Data\...`
- `%APPDATA%\WorkBuddy\automations\automations.db`

[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)

### 3.3 SQLite 与工作空间/会话结构

5.1.2 创建的核心结构：

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  cwd TEXT NOT NULL,
  user_id TEXT NOT NULL,
  title TEXT,
  custom_title TEXT,
  status TEXT NOT NULL DEFAULT 'Completed',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  is_playground INTEGER NOT NULL DEFAULT 0,
  source_mode TEXT,
  is_background_automation INTEGER,
  mode TEXT,
  model TEXT,
  expert_id TEXT,
  expert_locale TEXT,
  expert_runtime_identity TEXT,
  expert_marketplace TEXT,
  permission_mode TEXT
  -- 增量迁移还会补 last_activity_at/use_sandbox_cli/project_id 等
);

CREATE TABLE workspaces (
  path TEXT PRIMARY KEY,
  last_opened_at INTEGER NOT NULL
);
```

还包括 `session_usage`、`automations`、`automation_runs`、`automation_runtime_state`、`migration_meta`。数据库打开参数：`journal_mode=WAL`、`busy_timeout=5000`、`wal_autocheckpoint=0`；应用自行做 checkpoint/损坏恢复，并可能产生 `.needs-recovery`、`.recovered`、`.corrupt-*` 文件。[官方 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)

路径入库前用 Node `path.normalize()` 统一为当前 OS 原生分隔符；实现刻意不做 Unicode NFC 改写，也不强制折叠大小写。因此 Windows DB 的 `cwd` 预计保存 `C:\...` 原生形式，调用方比较路径时不能只按 Mac `/` 处理。

### 3.4 JSONL / transcript 与 hook

包内 CLI 的 hook stdin 公共字段包含：

```json
{
  "session_id": "...",
  "transcript_path": "C:\\...\\.workbuddy\\projects\\...\\<sid>.jsonl",
  "cwd": "C:\\...",
  "permission_mode": "...",
  "hook_event_name": "Stop"
}
```

`UserPromptSubmit` 另有 `prompt`，`Stop` 另有停止上下文。官方文档确认 Windows command hook 强制用 Git Bash，找不到 Git Bash 时会提示安装 Git for Windows；也可用 `CODEBUDDY_CODE_GIT_BASH_PATH` 指定。hook 默认 60 秒超时，可在条目上设置 `timeout`。[官方 Hook 参考](https://www.codebuddy.cn/docs/cli/hooks)

当前设置文件的 hook 结构与项目现有写法一致：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "...", "timeout": 30}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "...", "timeout": 30}]}
    ]
  }
}
```

但公开文档也说明外部修改需要在 `/hooks` 面板审核后生效；自动写 JSON 不等于已启用。[官方 Hook 参考](https://www.codebuddy.cn/docs/cli/hooks)

### 3.5 CLI / 可执行文件与 shell

已验证包内容：

- 安装根：`WorkBuddy.exe`、`launcher.exe`；
- `resources/vendor/PortableGit.zip`、`node.zip`、`python.zip`；
- 包内 CLI 搜索系统 Python 的候选含 `%LOCALAPPDATA%\Programs\Python\Python3*\python.exe`、`%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe`；
- 托管 Python 目标为 `~\.workbuddy\binaries\python\versions\...` / `envs\default`。

官方 Windows 安装指南允许选择安装目录，故不能写死 `%LOCALAPPDATA%\Programs\WorkBuddy`；应优先读取正在运行进程的 `Path`、卸载注册表 `InstallLocation`，再查 `Get-Command`。[Windows 安装指南](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-Win-Guide)

### 3.6 编码、锁与 Windows 差异

- JSONL 由 CLI 以 UTF-8 追加；包内读取也显式使用 `utf8`。
- 更新日志记录过 Windows “终端输出乱码”“中文乱码”“PowerShell cmd 路径解析”“打开日志 zip 的 EPERM”等修复，说明编码/锁问题不应当作理论风险忽略。[更新日志](https://www.codebuddy.cn/docs/workbuddy/Changelog)
- SessionStore 对同一 session 在**单进程内**用 Promise map 串行追加；跨进程侧仍要面对 Windows 文件共享模式和实时追加。
- SQLite 使用 WAL 与 5 秒 busy timeout；Copilot 应只读连接并允许 `-wal/-shm` 存在，不复制单独的 `.db` 后误判为完整快照。
- `product.json` 声明 `win32MutexName: "workbuddy"`，客户端还有 Electron 单实例机制；这不是可供 Copilot 协调读写的公开锁协议。

## 4. 合理推测（尚未真机确认）

1. 普通 ASCII 用户名机器上，实际根大概率就是 `C:\Users\<USER>\.workbuddy`；中文用户名/新系统版本可能进入短路径或 ProgramData fallback。
2. `register_hook.py` 生成的 POSIX shell 语法（环境变量前缀、单引号、`|| true`）与 Git Bash 执行模型相容；但包含中文/空格的项目绝对路径、Git Bash 路径转换及 `python3` 发现仍需实际触发一次 hook。
3. 5.1.2 的主日志树是 `<config>\logs`；旧组件/迁移残留可能还在 `%APPDATA%\WorkBuddy` 或 `%LOCALAPPDATA%` 下。真机 UI 的“打开日志文件夹”结果应作为最终答案。
4. WorkBuddy 的 Windows `<compressedCwd>` 必然会规避 `:`、`\` 等文件名非法字符，但公开文档和本次可稳定引用的源码片段没有给出完整、承诺稳定的编码函数；不能据此写死算法。

## 5. 未知项

| 未知 | 是否阻塞 Windows rollout |
|---|---|
| 5.2.3 是否改过 configDir、DB schema、project dirname 编码 | **需实机确认，但不必等待才能继续做安装器设计** |
| Windows 实际 `<compressedCwd>` 规则（盘符、UNC、反斜杠、大小写） | **阻塞现有 `wb_upload` 批量路径拼接**；hook 实时上传不阻塞，因为 stdin 直接给 `transcript_path` |
| 自定义 hook 中 `python3` / `python` 的实际 PATH 与版本 | **阻塞 hook 安装脚本定稿** |
| 外部改写 `<config>\settings.json` 后 `/hooks` 审核/启用的准确交互 | **阻塞无人值守 rollout** |
| WorkBuddy 是否默认登录自启、安装器具体注册项 | **阻塞“重启后自动恢复”承诺** |
| 中文用户名机器最终使用 8.3 还是 ProgramData fallback | **阻塞所有只依赖 `Path.home()` 的路径发现** |
| 客户端运行时读取 JSONL/SQLite 的 Windows 共享锁表现 | 不阻塞原型；需用真机负控确认 hook 读尾不会报 sharing violation |

## 6. 对当前 Mac 假设的直接影响

| 当前实现 | Windows 判断 |
|---|---|
| `copilot/hook.py` 使用 stdin 的 `transcript_path` | 方向正确；避免自行编码 cwd。需验证 Windows 文件锁与后台启动方式。 |
| `wb_sync.DEFAULT_DB_PATH = Path.home()/".workbuddy"/"workbuddy.db"` | ASCII 用户通常可用；中文用户名 fallback 时会读错根。应由探测/配置解析真实 configDir。 |
| `wb_upload.DEFAULT_PROJECTS_DIR` 同上 | 同样受 fallback 影响。 |
| `wb_upload.encode_cwd(): replace("/", "-")` | Windows 不成立；`C:` 和 `\` 未处理。不要补一个猜测正则后交付。 |
| `register_hook.py` 固定 `Path.home()/".workbuddy"/"settings.json"` | fallback 时可能写到 WorkBuddy 不读取的位置。 |
| hook 命令 `python3 ... || true` | `|| true` 与 Git Bash 相容；`python3` 可发现性未知。 |
| `install.sh` | 不能作为 Windows 安装器：`venv/bin/activate`、`ln -sf`、bash 路径均为 Unix 流程。需单独 PowerShell/打包方案。 |

## 7. Windows 真机只读 PowerShell 探测清单

### 7.1 安全约束

- 先退出敏感屏幕共享；脚本**不上传任何文件**。
- 不回传 `settings.json`、`.credentials.json`、`mcp.json`、connector 文件的值。
- 不回传 JSONL `content`、prompt、回答、token、cookie。
- 回传前将用户名替换为 `<USER>`，ProgramData fallback hash 替换为 `<HASH>`；脚本已做基础替换，仍需人工复核。
- 只回传：客户端版本、候选根是否存在、文件/目录名、大小/时间、JSON 顶层 key、hook 事件名/类型、DB 表/列名、转录行的字段名与 `type` 集合。

### 7.2 一键探测（只读）

在普通 PowerShell（无需管理员）粘贴运行；输出保存位置由学员自己选择，回传前人工检查：

```powershell
$ErrorActionPreference = 'SilentlyContinue'

function Redact([string]$s) {
  if (-not $s) { return $s }
  if ($env:USERPROFILE) { $s = $s.Replace($env:USERPROFILE, '%USERPROFILE%') }
  $s = $s -replace '(C:\\ProgramData\\WorkBuddy\\users\\)[^\\]+', '$1<HASH>'
  return $s
}

$out = [ordered]@{}
$out.timestamp = (Get-Date).ToString('o')
$out.os = [ordered]@{
  caption = (Get-CimInstance Win32_OperatingSystem).Caption
  version = [Environment]::OSVersion.Version.ToString()
  ps = $PSVersionTable.PSVersion.ToString()
  codepage = (chcp)
  home = Redact $HOME
  userprofile = Redact $env:USERPROFILE
  workbuddy_config_env = Redact $env:WORKBUDDY_CONFIG_DIR
}

# 安装位置/版本：不猜默认安装目录
$procs = Get-Process WorkBuddy -ErrorAction SilentlyContinue
$out.running_processes = @($procs | ForEach-Object {
  [ordered]@{ version=$_.FileVersion; path=(Redact $_.Path); pid=$_.Id }
})
$uninstallRoots = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$out.installs = @($uninstallRoots | ForEach-Object { Get-ItemProperty $_ } |
  Where-Object { $_.DisplayName -match 'WorkBuddy' } |
  ForEach-Object { [ordered]@{name=$_.DisplayName; version=$_.DisplayVersion; location=(Redact $_.InstallLocation)} })

# 候选 config 根（只列元数据）
$roots = @()
if ($env:WORKBUDDY_CONFIG_DIR) { $roots += $env:WORKBUDDY_CONFIG_DIR }
$roots += (Join-Path $env:USERPROFILE '.workbuddy')
$roots += @(Get-ChildItem 'C:\ProgramData\WorkBuddy\users' -Directory | ForEach-Object { Join-Path $_.FullName '.workbuddy' })
$roots += @(Get-ChildItem 'C:\WorkBuddy-env' -Directory | ForEach-Object { Join-Path $_.FullName '.workbuddy' })
$roots = @($roots | Where-Object { $_ } | Select-Object -Unique)

$rootReports = @()
foreach ($root in $roots) {
  if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
  $r = [ordered]@{ path=(Redact $root) }
  $r.top = @(Get-ChildItem -LiteralPath $root -Force | ForEach-Object {
    [ordered]@{ name=$_.Name; kind=(if ($_.PSIsContainer) {'dir'} else {'file'}); bytes=(if ($_.PSIsContainer) {$null} else {$_.Length}); mtime=$_.LastWriteTimeUtc.ToString('o') }
  })

  $settings = Join-Path $root 'settings.json'
  if (Test-Path -LiteralPath $settings) {
    try {
      $sj = Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json
      $r.settings = [ordered]@{
        top_keys = @($sj.PSObject.Properties.Name)
        hook_events = @($sj.hooks.PSObject.Properties.Name)
        hook_shapes = @($sj.hooks.PSObject.Properties | ForEach-Object {
          [ordered]@{ event=$_.Name; blocks=@($_.Value).Count; types=@($_.Value.hooks.type | Select-Object -Unique); commands_present=(@($_.Value.hooks | Where-Object {$_.command}).Count) }
        })
      }
    } catch { $r.settings_error = $_.Exception.GetType().Name }
  }

  $db = Join-Path $root 'workbuddy.db'
  $r.db_files = @('workbuddy.db','workbuddy.db-wal','workbuddy.db-shm','workbuddy.db.needs-recovery' | ForEach-Object {
    $p = Join-Path $root $_; if (Test-Path -LiteralPath $p) { $i=Get-Item -LiteralPath $p; [ordered]@{name=$i.Name; bytes=$i.Length; mtime=$i.LastWriteTimeUtc.ToString('o')} }
  })

  # 若系统已有 sqlite3，只读返回表/列；没有则明确 missing
  $sqlite = Get-Command sqlite3 -ErrorAction SilentlyContinue
  if ($sqlite -and (Test-Path -LiteralPath $db)) {
    $r.sqlite = [ordered]@{
      tables = @(& $sqlite.Source -readonly $db '.tables')
      sessions_columns = @(& $sqlite.Source -readonly $db 'PRAGMA table_info(sessions);')
      workspaces_columns = @(& $sqlite.Source -readonly $db 'PRAGMA table_info(workspaces);')
      journal_mode = @(& $sqlite.Source -readonly $db 'PRAGMA journal_mode;')
    }
  } else { $r.sqlite = 'sqlite3_missing_or_db_missing' }

  # 只取文件名/父目录名/大小；最多抽一份前 30 行的字段名与 type，绝不输出 content
  $projects = Join-Path $root 'projects'
  if (Test-Path -LiteralPath $projects) {
    $files = @(Get-ChildItem -LiteralPath $projects -Filter '*.jsonl' -File -Recurse | Sort-Object LastWriteTimeUtc -Descending)
    $r.transcripts = [ordered]@{
      count = $files.Count
      samples = @($files | Select-Object -First 5 | ForEach-Object { [ordered]@{project_dir=$_.Directory.Name; file=$_.Name; bytes=$_.Length; mtime=$_.LastWriteTimeUtc.ToString('o')} })
    }
    $sample = $files | Select-Object -First 1
    if ($sample) {
      $types = @(); $keys = @(); $cwdShape = $null
      Get-Content -LiteralPath $sample.FullName -Encoding UTF8 -TotalCount 30 | ForEach-Object {
        try {
          $j = $_ | ConvertFrom-Json
          $types += $j.type
          $keys += $j.PSObject.Properties.Name
          if (-not $cwdShape -and $j.cwd) { $cwdShape = Redact ([string]$j.cwd) }
        } catch {}
      }
      $r.transcripts.first30_metadata = [ordered]@{ types=@($types | Where-Object {$_} | Select-Object -Unique); keys=@($keys | Select-Object -Unique); cwd_redacted=$cwdShape }
    }
  }
  $rootReports += $r
}
$out.config_roots = $rootReports

# 命令发现：只回传命令类型与脱敏路径
$out.commands = @('WorkBuddy','workbuddy','codebuddy','python3','python','py','bash','git','sqlite3' | ForEach-Object {
  $c = Get-Command $_ -ErrorAction SilentlyContinue | Select-Object -First 1
  [ordered]@{ name=$_; found=[bool]$c; type=$c.CommandType; path=(Redact $c.Source) }
})

# 登录启动证据：只列名称/脱敏路径，不执行、不修改
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$run = Get-ItemProperty $runKey
$out.hkcu_run = @($run.PSObject.Properties | Where-Object { $_.Name -match 'WorkBuddy' } | ForEach-Object { [ordered]@{name=$_.Name; value=(Redact ([string]$_.Value))} })
$startup = [Environment]::GetFolderPath('Startup')
$out.startup_files = @(Get-ChildItem -LiteralPath $startup | Where-Object {$_.Name -match 'WorkBuddy'} | ForEach-Object { [ordered]@{name=$_.Name; path=(Redact $_.FullName)} })
$out.scheduled_tasks = @(Get-ScheduledTask | Where-Object {$_.TaskName -match 'WorkBuddy' -or $_.TaskPath -match 'WorkBuddy'} | ForEach-Object { [ordered]@{name=$_.TaskName; path=$_.TaskPath; state=$_.State.ToString()} })

# 只读共享打开测试，不读正文
$out.share_open = @($roots | ForEach-Object {
  $db = Join-Path $_ 'workbuddy.db'
  if (Test-Path -LiteralPath $db) {
    try { $f=[IO.File]::Open($db,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete); $f.Close(); [ordered]@{path=(Redact $db); ok=$true} }
    catch { [ordered]@{path=(Redact $db); ok=$false; error=$_.Exception.GetType().Name} }
  }
})

$out | ConvertTo-Json -Depth 8
```

### 7.3 还需人工做的两个负控

1. **hook 红灯测试**：先注册一个只写入临时空标记文件、不读 transcript 的 `UserPromptSubmit` hook，确认提交消息会产生标记；再把命令中的解释器名改成必不存在名称，确认 `/hooks` 或日志明确报错。之后恢复。不要用真实 Copilot 上传作为首个验证。
2. **运行中只读测试**：保持 WorkBuddy 正在输出一段无敏感内容的测试会话，用 Copilot hook 只读取 `transcript_path` 最后 4 KiB 并计算 SHA-256，不输出正文；确认无 sharing violation、UTF-8 解码异常或半行 JSON 误判。

## 8. 推荐下一步

不必等待实机才能继续准备 Windows 安装器框架，但在声称“Windows 可 rollout”之前，必须拿到一台 Windows 真机的上述脱敏输出，至少锁定四项：**实际 configDir、project dirname 映射、hook 中 Python 命令、登录启动证据**。在此之前，实时 hook 可优先依赖 stdin `transcript_path`；批量上传不可复用当前 `encode_cwd()`。

## 参考来源

1. [腾讯 WorkBuddy Windows 系统安装指南](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-Win-Guide)
2. [腾讯 WorkBuddy 历史版本下载](https://www.codebuddy.cn/docs/workbuddy/Download-History)
3. [腾讯 WorkBuddy 更新日志](https://www.codebuddy.cn/docs/workbuddy/Changelog)
4. [腾讯官方 Hook 参考指南](https://www.codebuddy.cn/docs/cli/hooks)
5. [腾讯官方 Hooks 入门指南](https://www.codebuddy.cn/docs/cli/hooks-guide)
6. [腾讯 WorkBuddy 助理说明](https://www.codebuddy.cn/docs/workbuddy/Claw)
7. [腾讯官方 WorkBuddy 5.1.2 Windows 安装包](https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe)
