---
title: ADR-001 共享 Student Core 与平台适配器
date: 2026-07-10
status: accepted
audience: both
tags: [adr, architecture, cross-platform, student-client]
---

# ADR-001: 共享 Student Core 与平台适配器

## 背景

现有学员端把 PyObjC UI、HTTP/WS、上传、WorkBuddy 读取和状态持久化集中在 macOS 模块中。项目需保留 macOS 成熟浮标，同时使 Windows 能逐步实现数据、Hook、安装、无头 Agent 与未来 UI。

## 选项

| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 共享 Student Core + 平台适配器 | 保留 NSPanel；共享状态、协议、上传和测试 | 迁移期新旧路径并存 |
| B: 跨平台 GUI 全量重写 | 表面上一套 UI | 丢失 macOS 资产；过早锁定 Windows UI |
| C: Mac/Windows 两套独立客户端 | Windows 原型可独立推进 | 断线、去重、状态和上传重复且易漂移 |

## 决策

选择 A。Windows 本期仅完成 WorkBuddy 数据、Hook、安装和无头 Agent；正式 Windows 浮标 UI 待真机事实和单独 UI 设计后实施。macOS 继续使用 PyObjC NSPanel。

## 理由

- NSPanel 跨 Space、绘制、拖动和主线程调度已经真机验证，不应因 Windows 需求重写。
- WS、去重、上传、状态机和补拉是跨平台行为，必须共享以防双端漂移。
- Windows WorkBuddy 还有 configDir、cwd 映射和 Hook 运行时未知，平台差异必须封装。
- 可以先建三系统契约/单元测试，再由真机 fixture 驱动 Windows 适配。

## 后果

- 正面：macOS UI 保留，核心行为只实现一次，测试边界清晰。
- 负面：迁移期需短期维持新旧入口契约一致。
- 约束：Student Core 不得 import 平台 UI 或猜 WorkBuddy 路径；Windows 事实缺失时显示 blocked/unknown。
- 非目标：不建每学员身份系统，继续接受共享 student token + 客户端 `student_id`。
