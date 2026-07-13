#!/usr/bin/env bash
# 安装依赖 + 创建配置 + 注册 hook
# 用法: ./install.sh
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "==> 1. 选择 Python 环境"
PYTHON=${PYTHON:-python3}
if ! command -v $PYTHON >/dev/null 2>&1; then
  echo "找不到 python3，请先安装。"
  exit 1
fi
echo "使用: $($PYTHON --version)"

echo ""
echo "==> 2. 创建虚拟环境 venv/"
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip

echo ""
echo "==> 3. 安装依赖"
pip install -r requirements.txt
# 浮标 WS 客户端依赖
pip install websockets

echo ""
echo "==> 4. 创建 config.json"
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "已创建 config.json，请编辑填写 student_id / student_name"
else
  echo "config.json 已存在，跳过"
fi

echo ""
echo "==> 5. 注册 hook 到 WorkBuddy"
WB_SETTINGS="$HOME/.workbuddy/settings.json"
mkdir -p "$HOME/.workbuddy"

# Hook events stay local until the Student Core agent receives them.  Allow an
# explicit path for managed installations, otherwise keep it under WorkBuddy.
COPILOT_SPOOL_DIR="${COPILOT_SPOOL_DIR:-$HOME/.workbuddy/copilot/spool}"
export COPILOT_SPOOL_DIR

# 把 hook 脚本软链到 .workbuddy/copilot/ 方便 hook 命令定位
mkdir -p "$HOME/.workbuddy/copilot"
mkdir -p "$COPILOT_SPOOL_DIR"
ln -sf "$PROJECT_DIR/copilot/hook.py" "$HOME/.workbuddy/copilot/hook.py"

# 合并 hook 配置进 settings.json（使用 python 处理 JSON 合并）
$PYTHON <<'PYEOF'
import json, os, shlex, socket
from pathlib import Path

settings_path = Path.home() / ".workbuddy" / "settings.json"
settings = {}
if settings_path.exists():
    settings = json.loads(settings_path.read_text())

cfg_path = Path.cwd() / "config.json"
cfg = {}
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
        pass

student_id = (
    os.environ.get("COPILOT_STUDENT_ID")
    or cfg.get("student_id")
    or f"student-{socket.gethostname()}"
)
env_parts = [
    f"COPILOT_STUDENT_ID={shlex.quote(str(student_id))}",
    f"COPILOT_CONFIG={shlex.quote(str(cfg_path.resolve()))}",
    f"COPILOT_SPOOL_DIR={shlex.quote(str(Path(os.environ['COPILOT_SPOOL_DIR']).expanduser().resolve()))}",
]

hook_cmd = " ".join(
    env_parts + ["python3", "\"$HOME/.workbuddy/copilot/hook.py\"", "||", "true"]
)

hooks_block = {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 2}]}],
    "Stop": [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 2}]}],
}

existing = settings.get("hooks", {})
for event, blocks in hooks_block.items():
    if event not in existing:
        existing[event] = blocks
    else:
        # Replace a previous Copilot command in place so upgrades cannot leave
        # the old synchronous/networking hook behind.  Preserve user hooks.
        replacement = blocks[0]["hooks"][0]
        replaced = False
        for block in existing[event]:
            for hook in block.get("hooks", []):
                if "copilot/hook.py" in hook.get("command", ""):
                    hook.update(replacement)
                    replaced = True
        if not replaced:
            existing[event].extend(blocks)

settings["hooks"] = existing
settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
print(f"已更新 {settings_path}")
PYEOF

echo ""
echo "==> 6. 设置 DeepSeek API Key"
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "未检测到 DEEPSEEK_API_KEY 环境变量。"
  echo "请手动 export DEEPSEEK_API_KEY=sk-xxx 后再启动服务。"
  echo "（不设置也能跑通链路，会返回降级结果）"
fi

echo ""
echo "✅ 安装完成！"
echo ""
echo "启动方式："
echo "  1. 启动分析服务:  ./start_service.sh"
echo "  2. 启动菜单栏浮标: ./start_menubar.sh"
echo "  3. 在 WorkBuddy 的 /hooks 面板确认 hook 已启用"
echo ""
echo "提示：首次需要给 Python 解释器授予辅助功能权限（系统设置 > 隐私与安全 > 辅助功能）"
