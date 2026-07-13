# WorkBuddy Copilot — 技术原理文档

> 版本：0.2.0
> 更新日期：2026-07-02

## 一、核心原理

WorkBuddy Copilot 通过 **WorkBuddy hooks 机制**实现只读旁路监控：不侵入 WorkBuddy 的对话流程，只在事件发生时接收通知，后台分析后将结果推送给浮标和导师台。

### 1.1 数据采集

WorkBuddy 支持 hooks 机制，在对话生命周期的关键节点触发外部脚本：

| 事件 | 触发时机 | Copilot 动作 |
|------|---------|-------------|
| `UserPromptSubmit` | 学员发送消息时 | 存 prompt 全文 → 发事件（不调 LLM） |
| `Stop` | AI 回复完成时 | LLM 分析 → 存 ai_summary + analysis → 发事件 |

Hook 脚本（`hook.py`）是独立进程，从 stdin 读 JSON，POST 到 Copilot 服务。**fire-and-forget** 模式，失败不影响 WorkBuddy。

### 1.2 数据分析

LLM 分析在 `Stop` 事件时触发（腾讯云 TokenHub + deepseek-v3-0324）：

1. 解析 transcript JSONL，提取最近 N 条消息
2. 构建 system prompt（学习状态分析 + 技术助教诊断 + AI 回答摘要）
3. 调用 LLM，解析 JSON 结果
4. 一次调用产出：学习诊断 + AI 回答摘要

### 1.3 数据呈现

两种呈现方式共享同一 Service 层：

- **浮标**（学员侧）：PyObjC NSPanel 跨 Space 显示，WS 实时推送
- **导师观察台**（导师侧）：浏览器三栏布局，WS 实时推送

## 二、数据源原理

### 2.1 workbuddy.db（权威数据源）

WorkBuddy 的核心数据存储在 `~/.workbuddy/workbuddy.db`（SQLite）：

- **sessions 表**：205 条会话，含 `title`/`custom_title`/`status`/`mode`/`deleted_at`/`cwd`
- **workspaces 表**：13 个用户显式打开的工作目录

Copilot 通过 `wb_db.py` 以 `mode=ro` 只读访问，**绝不写入**。

### 2.2 对话标题来源

标题直接从 `workbuddy.db.sessions.title` 读取（权威），不扫 JSONL 的 ai-title。

一个对话在 JSONL 里会有多条 `ai-title` 行（话题漂移时重新生成），DB 存的是最终值。

### 2.3 空间与任务分组

WorkBuddy UI 按以下规则分组：

- **空间**：sessions.cwd 在 workspaces 表中（用户显式打开的目录）
- **任务**：sessions.cwd 不在 workspaces 表中（WorkBuddy 自动生成的目录）

验证：空间 13 个、任务 54 个，与 UI 一致。

### 2.4 用户消息提取

JSONL 里 user message 的 text 包含 `<system-reminder>` 系统注入 + `<user_query>` 真实用户输入。提取真实 prompt 需正则：

```python
re.search(r'<user_query>(.*?)</user_query>', text, re.DOTALL)
```

## 三、MVC 架构原理

### 3.1 为什么需要分层

重构前的问题：
1. 业务逻辑混在路由处理函数里（`_handle_stop` 在 service.py 中）
2. 数据访问散落，全程 dict 传递，字段名漂移
3. 导师路由反向依赖 service 全局变量
4. WS 推送硬编码遍历两个客户端池

### 3.2 分层方案

```
View → Controller → Service → Model + Repository
```

| 层 | 职责 | 变更频率 |
|----|------|---------|
| View | 呈现 | 高（可替换为飞书/CLI） |
| Controller | HTTP 编解码 | 中（接口变更时改） |
| Service | 业务编排 | 低（业务流程稳定） |
| Model | 领域模型 + 数据访问 | 低（表结构稳定时不变） |

**关键收益**：替换浮标为其他客户端时，Service 层零改动。

### 3.3 EventBus 解耦推送

```
Service 层 → bus.publish(payload)
WS 层 → bus.subscribe(callback)
```

未来增加飞书推送：

```python
async def feishu_subscriber(payload):
    if payload["type"] == "analysis" and payload["result"]["severity"] == "error":
        await send_feishu_message(payload)

bus.subscribe(feishu_subscriber)
```

Service 层零改动。

### 3.4 领域模型

用 dataclass 替代 dict，编译期捕获字段名错误：

```python
@dataclass
class Analysis:
    topic: str = ""
    understanding: str = "medium"
    diagnosis: str = ""
    suggestion: str = ""
    severity: str = "info"
    # ...
```

LLM 返回 dict → `AnalysisResult.from_dict(d)` → 模型实例 → `to_dict()` 入库。

## 四、数据生命周期

### 4.1 新对话（hooks 上线后）

```
学员发消息 → UserPromptSubmit hook → 存 prompts
AI 回复完成 → Stop hook → LLM 分析 → 存 ai_summaries + analyses
导师查看 → 三表 UNION 时间线 → prompt/ai_summary/analysis 交替
```

### 4.2 旧对话（hooks 上线前）

旧会话只有 analysis（hooks 上线前的分析记录）。prompt 和 ai_summary 通过从 JSONL 补录：

- **prompt**：提取 `<user_query>` 标签内容
- **ai_summary**：按每轮 assistant 回复生成 50-300 字摘要，简单回复可一两句话

### 4.3 已删除会话

`workbuddy.db.sessions.deleted_at` 非空 = 已删除。`wb_db.list_sessions(include_deleted=False)` 默认排除。

## 五、测试策略

112 个测试覆盖：

| 测试文件 | 覆盖范围 |
|---------|---------|
| test_wb_db.py | workbuddy.db 只读访问 + 降级逻辑 |
| test_store_mentor.py | copilot.db 4 表 CRUD + 三表 UNION |
| test_service_routing.py | /report 事件分流 + EventBus 接缝 |
| test_mentor_api.py | 导师 API（mock Service 层） |
| test_mentor_timeline.py | 时间线聚合纯函数 |
| test_transcript.py | JSONL 解析 + user_query 提取 |
| test_store.py / test_config.py / test_hook.py / test_llm.py | 基础模块 |
