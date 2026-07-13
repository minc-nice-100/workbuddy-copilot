#!/usr/bin/env python3
"""WorkBuddy DB 读取 Demo

验证能否正确读取 ~/.workbuddy/workbuddy.db 的内容。
不依赖 copilot 包，独立运行。
"""
from __future__ import annotations

import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"
PROJECT_CWD = str(Path(__file__).resolve().parents[1])


def ts_to_str(ms: int) -> str:
    """毫秒时间戳 → 本地时间字符串"""
    if not ms:
        return ""
    dt = datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def demo_sessions_table():
    """验证 1: 读取 sessions 表"""
    print("=" * 70)
    print("验证 1: workbuddy.db sessions 表")
    print("=" * 70)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # 总数
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    not_deleted = conn.execute("SELECT COUNT(*) FROM sessions WHERE deleted_at IS NULL").fetchone()[0]
    print(f"总会话数: {total}（未删除: {not_deleted}）")

    # 当前项目的会话
    print(f"\n当前项目 ({PROJECT_CWD}) 的会话:")
    rows = conn.execute(
        """SELECT id, title, custom_title, status, mode,
                  created_at, last_activity_at, deleted_at, permission_mode
           FROM sessions
           WHERE cwd = ?
           ORDER BY last_activity_at DESC""",
        (PROJECT_CWD,),
    ).fetchall()

    for r in rows:
        deleted = " [已删除]" if r["deleted_at"] else ""
        title = r["custom_title"] or r["title"] or "(无标题)"
        print(f"  • {r['id'][:12]}  {title}  [{r['status']}/{r['mode']}]{deleted}")
        print(f"    创建: {ts_to_str(r['created_at'])}  最后活动: {ts_to_str(r['last_activity_at'])}")

    print(f"\n→ 当前项目共 {len(rows)} 个会话（含已删除）")
    active = [r for r in rows if not r["deleted_at"]]
    print(f"→ 未删除: {len(active)} 个")

    conn.close()
    return len(active)


def demo_workspaces_table():
    """验证 2: 读取 workspaces 表"""
    print("\n" + "=" * 70)
    print("验证 2: workbuddy.db workspaces 表")
    print("=" * 70)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT path, last_opened_at FROM workspaces ORDER BY last_opened_at DESC LIMIT 10"
    ).fetchall()

    print(f"最近打开的 {len(rows)} 个工作区:")
    for r in rows:
        print(f"  • {r['path']}  (最后打开: {ts_to_str(r['last_opened_at'])})")

    conn.close()
    return len(rows)


def demo_user_name():
    """验证 3: 从 memory 文件提取用户名"""
    print("\n" + "=" * 70)
    print("验证 3: 用户名提取（memory 文件）")
    print("=" * 70)

    # 先从 sessions.json 或 DB 拿 userId
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    user_id = conn.execute("SELECT DISTINCT user_id FROM sessions LIMIT 1").fetchone()[0]
    conn.close()
    print(f"userId (from DB): {user_id}")

    memory_file = Path.home() / ".workbuddy" / "memory" / f"{user_id}_memory.md"
    if not memory_file.exists():
        print(f"✗ memory 文件不存在: {memory_file}")
        return None

    content = memory_file.read_text(encoding="utf-8")

    # 尝试提取用户名
    # 方法 1: 找 "用户是XXX" 或 "用户XXX"
    match = re.search(r"用户(?:是|名为|叫)?([^\s，。,\.]{2,10})", content)
    name = match.group(1) if match else None

    # 方法 2: 找 RAW_JSON 里的字段
    raw_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw_name = None
    if raw_match:
        try:
            raw = json.loads(raw_match.group(1))
            raw_name = raw.get("userName") or raw.get("name")
        except json.JSONDecodeError:
            pass

    print(f"memory 文件大小: {len(content)} 字符")
    print(f"方法1 (正则 '用户是XXX'): {name}")
    print(f"方法2 (RAW_JSON userName): {raw_name}")

    # 输出 memory 文件前 200 字符看结构
    print(f"\nmemory 文件前 200 字符:")
    print(content[:200])

    return name or raw_name


def demo_compare_with_sessions_json():
    """验证 4: 对比 workbuddy.db 和 app/sessions.json"""
    print("\n" + "=" * 70)
    print("验证 4: workbuddy.db vs app/sessions.json 对比")
    print("=" * 70)

    # DB 数据
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    db_rows = conn.execute(
        """SELECT id, title, cwd FROM sessions
           WHERE deleted_at IS NULL AND cwd = ?
           ORDER BY last_activity_at DESC""",
        (PROJECT_CWD,),
    ).fetchall()
    conn.close()

    # sessions.json 数据
    sj_path = Path.home() / ".workbuddy" / "app" / "sessions.json"
    sj_data = json.loads(sj_path.read_text(encoding="utf-8"))
    sj_sessions = [s for s in sj_data["sessions"] if s["workDir"] == PROJECT_CWD]

    print(f"{'会话 ID':<14} {'DB 标题':<30} {'sessions.json':<20} {'一致?'}")
    print("-" * 80)

    db_map = {r["id"]: r["title"] for r in db_rows}
    sj_map = {s["conversationId"]: "(无标题字段)" for s in sj_sessions}

    all_ids = set(db_map.keys()) | set(sj_map.keys())
    for sid in sorted(all_ids, key=lambda x: list(db_map.keys()).index(x) if x in db_map else 999):
        db_title = db_map.get(sid, "(DB无)")
        sj_title = sj_map.get(sid, "(sj无)")
        match = "✓" if (sid in db_map and sid in sj_map) else ("DB独有" if sid in db_map else "sj独有")
        print(f"{sid[:12]}  {db_title[:28]:<30} {sj_title:<20} {match}")

    print(f"\nDB 未删除会话: {len(db_rows)} 个")
    print(f"sessions.json 会话: {len(sj_sessions)} 个")
    print(f"→ DB 有而 sessions.json 无: {len(set(db_map) - set(sj_map))}")
    print(f"→ sessions.json 有而 DB 无: {len(set(sj_map) - set(db_map))}")


def demo_read_only_safety():
    """验证 5: 只读模式安全性"""
    print("\n" + "=" * 70)
    print("验证 5: 只读模式安全性测试")
    print("=" * 70)

    # 用 URI 只读模式打开
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    # 尝试写入（应该失败）
    try:
        conn.execute("INSERT INTO sessions (id, cwd, user_id, status, created_at, updated_at) VALUES ('test', 'test', 'test', 'test', 0, 0)")
        print("✗ 只读模式下写入成功！（不应该发生）")
        success = False
    except sqlite3.OperationalError as e:
        print(f"✓ 只读模式拦截写入: {e}")
        success = True
    finally:
        conn.close()

    return success


if __name__ == "__main__":
    print(f"WorkBuddy DB 路径: {DB_PATH}")
    print(f"文件存在: {DB_PATH.exists()}")
    print(f"文件大小: {DB_PATH.stat().st_size / 1024:.1f} KB")
    print()

    # 运行所有验证
    session_count = demo_sessions_table()
    workspace_count = demo_workspaces_table()
    user_name = demo_user_name()
    demo_compare_with_sessions_json()
    read_only_ok = demo_read_only_safety()

    # 总结
    print("\n" + "=" * 70)
    print("Demo 总结")
    print("=" * 70)
    print(f"✓ sessions 表读取: 当前项目 {session_count} 个未删除会话")
    print(f"✓ workspaces 表读取: {workspace_count} 个工作区")
    print(f"{'✓' if user_name else '✗'} 用户名提取: {user_name or '(未找到)'}")
    print(f"✓ 只读模式安全: {read_only_ok}")
    print()
    print("结论: workbuddy.db 可作为 Copilot 的权威数据源，替代 app/sessions.json")
