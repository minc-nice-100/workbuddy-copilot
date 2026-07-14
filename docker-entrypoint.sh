#!/usr/bin/env bash
# Docker 容器入口脚本：根据环境变量生成 config.json
# 参照 PostgreSQL 容器模式：以 copilot 用户运行，数据目录为 /var/lib/copilot/data
set -e

CONFIG_FILE="${COPILOT_CONFIG_FILE:-/app/config.json}"
DB_PATH="${COPILOT_DB_PATH:-/var/lib/copilot/data/copilot.db}"
DB_DIR="$(dirname "$DB_PATH")"

# 确保数据目录存在且权限正确
if [ ! -d "$DB_DIR" ]; then
    mkdir -p "$DB_DIR" 2>/dev/null || {
        echo "ERROR: 无法创建数据目录 $DB_DIR，请检查 volume 挂载权限" >&2
        exit 1
    }
fi
if [ ! -w "$DB_DIR" ]; then
    echo "WARNING: 数据目录 $DB_DIR 不可写，请确保挂载的 volume 属于 copilot 用户" >&2
fi

# 生成 config.json
cat > "$CONFIG_FILE" <<EOF
{
  "student_id": "${COPILOT_STUDENT_ID:-student-1}",
  "student_name": "${COPILOT_STUDENT_NAME:-学员 1}",
  "agent": {
    "framework": "workbuddy"
  },
  "service": {
    "host": "${COPILOT_HOST:-0.0.0.0}",
    "port": ${COPILOT_PORT:-8765},
    "public_base_url": "${COPILOT_PUBLIC_BASE_URL:-}",
    "analysis_max_concurrency": ${COPILOT_ANALYSIS_CONCURRENCY:-2}
  },
  "auth": {
    "mode": "${COPILOT_AUTH_MODE:-local}",
    "student_token": "${COPILOT_STUDENT_TOKEN:-}",
    "student_tokens": {},
    "mentor_token": "${COPILOT_MENTOR_TOKEN:-}",
    "token": "${COPILOT_TOKEN:-}"
  },
  "llm": {
    "provider": "${COPILOT_LLM_PROVIDER:-deepseek}",
    "api_base": "${COPILOT_LLM_API_BASE:-https://api.deepseek.com/v1}",
    "model": "${COPILOT_LLM_MODEL:-deepseek-chat}",
    "summary_model": "${COPILOT_LLM_SUMMARY_MODEL:-deepseek-v3-0324}",
    "api_key_env": "DEEPSEEK_API_KEY",
    "timeout": ${COPILOT_LLM_TIMEOUT:-30},
    "max_tokens": ${COPILOT_LLM_MAX_TOKENS:-600}
  },
  "analysis": {
    "recent_n": ${COPILOT_ANALYSIS_RECENT_N:-16},
    "min_interval_sec": ${COPILOT_ANALYSIS_MIN_INTERVAL:-8},
    "enable_llm": ${COPILOT_ANALYSIS_ENABLE_LLM:-true},
    "process_reminder_prompt": "${COPILOT_ANALYSIS_REMINDER_PROMPT:-如果学员出现反复试错、上下文描述不清、让 AI 直接写但自己没有验证、方向跑偏等低效模式，只在把握较高时轻提醒。提醒要少而准，不要频繁打断。}"
  },
  "menubar": {
    "icon_idle": "🎓",
    "icon_active": "✨",
    "icon_alert": "⚠️",
    "max_menu_items": 12,
    "notification_on_alert": true
  },
  "store": {
    "db_path": "${DB_PATH}"
  }
}
EOF

echo "config.json generated at $CONFIG_FILE (db_path=$DB_PATH, mode=${COPILOT_AUTH_MODE:-local})"

# 启动 uvicorn
exec uvicorn copilot.service:app \
    --host "${COPILOT_HOST:-0.0.0.0}" \
    --port "${COPILOT_PORT:-8765}" \
    --workers 1 \
    --log-level "${COPILOT_LOG_LEVEL:-info}"