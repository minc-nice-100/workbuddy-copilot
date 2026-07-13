# 设计修订：导师触发·客户端全量对话上传

> 日期：2026-07-03
> 修订：2026-07-06
> 状态：已实现基础链路；公网 MVP 前需补离线补拉、状态闭环、失败提示
> 方法：3 方案 → 判官 + 可行性/隐私规模/过度设计 三专家评审（真实数据实测：205 会话 / 207 JSONL / 49.3MB）

## 一、需求
导师在导师台点一个"同步全部对话"按钮 → 学员机把**本地全部对话的完整内容**上传 → 导师台看到所有对话完整内容（当前 84 会话里只有 10 条有内容，74 条灰显"未上传"）。不依赖 hook，直接读本地文件系统。

2026-07-06 需求修订：
- 学员和导师均通过公网服务器连接，不依赖局域网。
- 同步不要求严格 exactly-once 或进程崩溃后自动完整恢复。
- 但必须容错并提示用户：离线不静默丢命令，失败不静默灰显，导师可看到状态并重试。

## 二、关键实测事实（决定设计）
| 事实 | 数据 | 影响 |
|------|------|------|
| JSONL 体积构成 | 总 49.3MB：**message 行仅 4.6MB(9%)**，function_call/result **40.7MB(83%)** | 客户端只传 message 行 → **11× 压缩** + 工具输出(文件内容/密钥)最高危 PII **留本地不外泄** |
| 路径编码规则 | `encode(cwd)=/→-、去前导-`，**205/205 命中**、0 失配 | 客户端定位 `<sid>.jsonl` 确定可行 |
| user_query 标签覆盖 | 仅 **156/205** 有 `<user_query>`；49 个原生会话是明文 input_text | 提取**必须带回退**（无标签取整段），否则漏 49 会话 |
| seq 语义 | 现 `seq_in_session=len(prompts)` 是 **hook 插入计数,非 JSONL 轮序** | 判死"复用 ai_summaries 按 seq 合并"，必须独立 messages 表 |
| 现有能力 | 反向通道(WSRegistry mentor→float)、wb_sync 只读上报骨架、transcript.parse_text、delete_student 级联删 | 全部可复用，不建常驻进程 |

## 三、推荐架构（杂交 B 骨架 + A 剥噪 + C 单会话原子）

```
导师台[同步全部对话] → POST /api/mentor/students/{sid}/request-upload
   → 服务端发 mentor_command(非广播,只定向 floats[student_id]) 经反向通道
   → 学员浮标 floating_native 收到 → 起后台线程调 copilot/wb_upload.py
      → 枚举本地 workbuddy.db 205 会话(权威) → GET /api/transcripts/known 取服务端已有(按 sha)
      → 只对缺失/变更会话：读 <sid>.jsonl → 【客户端只行过滤 type=='message'】(4.6MB) → 逐会话 POST
   → POST /api/student/sessions/{sid}/transcript：服务端 parse_text 提取每轮(user_query+回退 / AI全文)
      → 整会话 DELETE-then-INSERT 写 messages 表(幂等) + raw_transcripts 兜底
   → 时间线：有 messages 用 messages(每轮prompt+AI全文)，否则回退现三表 UNION；ai_summaries/analyses 作点评叠加层
```

**解析位置裁决**：客户端只做**稳定的行过滤**（`if type=='message'`，砍掉 83% 工具噪声/PII）；服务端 parse_text 做**易变的语义解析**（user_query 正则 + 轮次配对）——parser 集中在服务端，改一次覆盖全量 205 会话，无需向 205 台学员机推更新。红线守住：服务端只解析**已上传的内容**，永不读学员机 FS。

## 四、数据模型
**新增 `messages` 表**（内容层，与 hook 诊断层正交）：
```
messages(id, session_id, student_id, seq, role, text, source='bulk', content_sha256, created_at)
  唯一键(session_id, seq, role)；整会话 DELETE bulk 行再插 → 幂等自愈
```
- **prompts/ai_summaries/analyses 原封不动**（hook 的实时摘要+诊断作叠加层，按 session_id+时间就近叠加，不按 seq 匹配）。
- raw_transcripts 加 content_sha256 存整会话清洗原文兜底。
- **不复用 ai_summaries 存全文**（三专家一致否决：语义漂移 + seq 错位 bug）。
- 迁移走现有 _MIGRATIONS（加列）+ SCHEMA CREATE IF NOT EXISTS，旧库零迁移。
- 新增 `upload_requests(request_id, mentor_id, student_id, session_id?, status, error_message, created_at, updated_at)` 做审计 + 离线补拉队列（对齐公网 MVP 容错要求）。

**渲染（解决 point 1 AI 回复回归）**：某会话有 bulk messages → 每轮 AI 用 `role=assistant` 全文，前端**默认截断显示摘要、点击展开该轮完整回复**（CSS clamp + expand，每轮独立、不再"都一样"）；无 messages 回退现摘要。

## 五、三管线职责边界（不重叠）
| 管线 | 触发 | 传什么 | 写哪 | 解决 |
|------|------|--------|------|------|
| hook | 实时事件 | 活跃会话增量 | prompts+ai_summaries+**analyses(诊断)** | 深度(带 LLM 诊断) |
| wb_sync | 手动/定时 | 会话**元数据** | sessions 表 | 列表层灰显 |
| **upload(新)** | **导师触发** | 全部会话**内容** | **messages**+raw_transcripts | 内容层灰显 |

## 六、规模与隐私
- **逐会话独立 POST**（不是一个巨请求）→ 天然分片/断点续传/进度/不超时；首次约 84 个小请求（剥噪后每会话几十~几百 KB）。
- **增量**：GET /known 按 sha 跳过已有，"刷新"只重传变更会话。
- upload 端点超时放宽到 30-60s（现 _post_json 5s 对 MB 会话必超时）。
- 隐私（MVP 延后但标注）：全部对话原文含个人信息；工具输出(最高危 PII)客户端剥离不外泄；**TLS 为任何原始上传的硬前置**（现 http 仅内网）；保留期/删除复用 delete_student；可见范围鉴权对齐已拍板决策。

## 七、明确不做（避免过度设计）
不建常驻 daemon（复用浮标 WS 后台线程）· 不上传工具原始 blob · MVP 不做 gzip/指数退避/byte-offset 差量（剥噪后小 POST 不需要）· 服务端永不读学员机 FS · 不做复杂队列/死信/严格 exactly-once。

v2 调整：离线补拉不再延后，进入公网 MVP 当前范围；历史 LLM 诊断允许失败，但必须标记 failed/待重试并在导师台提示。

## 八、落地步骤（已完成基础链路，需补强）
1. store.py：加 messages + upload_requests 表 + 迁移；get_timeline_by_session 加"有 messages 优先"分支。
2. transcript.py：extract_user_query(带回退) + parse_turns(轮配对) + 单测（覆盖有/无标签、多 input、纯工具轮）。
3. service.py：POST /api/student/sessions/{sid}/transcript(解析入库) · GET /api/transcripts/known(sha manifest) · POST /api/mentor/students/{sid}/request-upload。
4. connections.py：handle_event 加 mentor_command 定向分支。
5. copilot/wb_upload.py（新，复用 wb_sync 路径编码/读库/POST 骨架）：枚举→行过滤→增量→逐会话上传。
6. floating_native.py：收 mentor_command → 后台线程调 wb_upload；重连后补拉 pending upload_requests，并回写 running/done/failed。
7. 导师台前端：加"同步全部对话"按钮 + 灰显卡片"加载完整内容"；每轮 AI 摘要默认+展开全文（修 point 1）。
8. 测试：user_query 抽取/幂等重传/hook+bulk 会话 XOR 渲染/路径编码(含中文)/大会话截断/离线补拉。

### v2 补强任务（代码前先写测试）
1. `upload_requests` 增加/迁移 `error_message`、`updated_at`，并提供状态更新方法。
2. 新增学员端补拉接口：`GET /api/student/upload-requests?student_id=&status=pending`。
3. 新增学员端状态回报接口：`POST /api/student/upload-requests/{request_id}/status`，支持 running/done/failed + 统计/错误。
4. 浮标启动、WS 重连、收到 mentor_command 三种时机都尝试补拉 pending request；按 request_id 幂等，避免重复并发上传。
5. 导师台展示 pending/running/done/failed；failed 显示错误和重试入口。
6. 历史 LLM 诊断失败不回滚 messages/raw_transcripts，记录失败状态或待诊断标记，导师能看到"内容已上传，诊断失败/可重试"。

## 九、用户已拍板的决策（2026-07-03）
1. ✅ **工具输出不传**：客户端只行过滤 `type=='message'` 上传对话文本（学员提问+AI回答），function_call_result 等工具输出**剥离不外泄**（11× 压缩、最高危 PII 留本地）。
2. ✅ **历史会话也补跑 LLM 诊断**：上传每个会话后，服务端除存 messages/raw_transcripts 外，**再对该会话跑 LLM 分析 → 存 analyses**（导师能看到历史会话的学习诊断，不只内容）。→ 管线变化见下。
3. ✅ **触发范围=单个学员**：导师选中某学员 → 同步该学员全部对话（先不做刷新全班）。
4. Point 1 AI 回复展示：改回"每轮摘要默认 + 点击展开该轮完整回复"（依赖上传带来的每轮全文）。
5. 隐私门：公网 MVP 必须 HTTPS/WSS + 双角色 token；更细的账号体系、per-student token、留存和脱敏仍后延。

### 决策 2 的管线影响（上传含 LLM 分析）
服务端 POST /api/student/sessions/{sid}/transcript 收到某会话内容后：
1. 解析每轮 → 存 messages(source=bulk) + raw_transcripts。
2. **对该会话跑 LLM 分析**（复用 llm.analyze，喂该会话最近 N 轮）→ 存 analyses（source 标 batch 或按现有结构），供时间线"学习诊断"卡显示。
3. LLM 分析走 **BackgroundTask + asyncio.Semaphore 限并发**（74 会话 × LLM，避免打爆 single-worker + 控成本）；LLM 未启用则只存内容不诊断（降级不崩）。
4. 幂等：重传同会话先删该会话 bulk messages/该批 analyses 再重建（content_sha256 跳过未变会话，避免重复跑 LLM 烧钱）。
5. 容错修订：同 sha 但上次诊断失败时，不能永久跳过诊断；应允许导师重试诊断或在 UI 标记"内容已同步，诊断失败"。
> 注：这让 upload 管线从"纯内容"变成"内容+诊断"，成本上升（首次 74 次 LLM），但用 sha 增量 + 限并发控制；后续"刷新"只对变更会话跑。公网 MVP 不要求严格恢复，但要求失败可见、可重试。
