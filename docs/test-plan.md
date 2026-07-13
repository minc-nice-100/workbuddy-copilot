# 测试计划 — 导师观察台模块

> 以下命令均从仓库根目录运行；使用虚拟环境时，将 `python` 替换为对应环境的解释器。

## P0 生存测试

- [ ] Python 依赖可导入
  Criteria: `python -c "import copilot.service"` 退出码=0
  Command: `python -c "import copilot.service"`

- [ ] 全量现有测试通过
  Criteria: exit code = 0
  Command: `python -m pytest tests/ -v --ignore=tests/test_mentor_frontend.py`

- [ ] FastAPI app 可启动
  Criteria: import 不报错
  Command: `python -c "from copilot.service import app; print(type(app))"`

## P1 核心功能 E2E

- [ ] [backend] store 新表 CRUD
  Criteria: prompts + ai_summaries 增查正常，timeline 聚合正确
  Command: `python -m pytest tests/test_store_mentor.py -v`

- [ ] [backend] LLM schema 新字段
  Criteria: ai_reply_summary 解析正确，向后兼容
  Command: `python -m pytest tests/test_llm_summary.py -v`

- [ ] [backend] /report 事件分流
  Criteria: UserPromptSubmit 存prompt不调LLM；Stop 触发LLM存summaries+analyses
  Command: `python -m pytest tests/test_service_routing.py -v`

- [ ] [backend] 导师 API
  Criteria: /api/mentor/students 和 timeline 接口正常，WS 隔离
  Command: `python -m pytest tests/test_mentor_api.py -v`

- [ ] [backend] timeline 聚合纯函数
  Criteria: merge_timeline 三表合并排序正确
  Command: `python -m pytest tests/test_mentor_timeline.py -v`

- [ ] [file] 前端静态文件
  Criteria: 三栏布局 + fetch + WS 结构正确
  Command: `python -m pytest tests/test_mentor_frontend.py -v`

## P1-E2E 场景用例

### 场景1：导师查看学员对话时间线
1. 学员在 WorkBuddy 发送提问 → hook 触发 UserPromptSubmit → 存 prompts
2. AI 回答完成 → hook 触发 Stop → LLM 分析 → 存 ai_summaries + analyses
3. 导师浏览器打开 /mentor/ → 看到学员列表
4. 点击学员 → 看到对话列表
5. 点击对话 → 看到时间线（蓝条提问/紫条摘要/橙条诊断）
验证：时间线按时间排序，三种类型交替出现

### 场景2：导师实时接收事件
1. 导师页面 WS 连接 /ws/mentor
2. 学员发新提问 → 导师页面实时出现蓝条
3. AI 回答完成 → 导师页面实时出现紫条+橙条
验证：WS 推送不串到浮标 /ws

## 追溯矩阵

| 场景/需求 | 测试文件 | 用例 | 类型 |
|----------|---------|------|------|
| PRD F1 store 新表 | tests/test_store_mentor.py | TestPromptsTable, TestAISummariesTable, TestTimelineAggregation, TestBackwardCompatibility | [feature] |
| PRD F2 LLM 摘要 | tests/test_llm_summary.py | TestParseJsonContent, TestFallback, TestSystemPrompt | [feature] |
| PRD F3 事件分流 | tests/test_service_routing.py | TestEventRouting, TestNotifyAllSeam, TestExistingEndpointsUnaffected | [feature] |
| PRD F4 导师 API | tests/test_mentor_api.py | TestMentorStudents, TestMentorTimeline, TestMentorWS | [feature] |
| PRD F4 timeline 聚合 | tests/test_mentor_timeline.py | TestMergeTimeline, TestFormatTimelineItem | [feature] |
| PRD F5 前端 | tests/test_mentor_frontend.py | TestFrontendFiles, TestFrontendStructure, TestFrontendServed | [feature] |
| 场景1 导师查看时间线 | tests/test_mentor_api.py + tests/test_mentor_timeline.py | 全部 | [scenario] |
| 场景2 实时事件 | tests/test_mentor_api.py::TestMentorWS | test_mentor_ws_pool_separate_from_floating | [scenario] |
