#!/usr/bin/env bash
# TradingAgents 快捷运行脚本
# 用法:
#   ./run.sh NVDA 2026-10-30                      # 默认分析师
#   ./run.sh 600519.SS 2026-10-30 market,fundamentals
#
# 前置：在 .env 里填好 API Key（本脚本会自动加载）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

TICKER="${1:-}"
DATE="${2:-$(date +%F)}"
ANALYSTS="${3:-market,news,fundamentals}"

if [[ -z "$TICKER" ]]; then
  echo "用法: ./run.sh <代码> [日期 YYYY-MM-DD] [分析师 market,sentiment,news,fundamentals]"
  exit 1
fi

# .env 存在则加载（pip 的 python-dotenv 只读 cwd，这里显式 source 更稳）
if [[ -f .env ]]; then
  set -a; . .env; set +a
fi

# 默认值：不设置则用 .env / 交互式提问
: "${TRADINGAGENTS_OUTPUT_LANGUAGE:=Chinese}"
: "${TRADINGAGENTS_MAX_DEBATE_ROUNDS:=1}"
: "${TRADINGAGENTS_MAX_RISK_ROUNDS:=1}"
: "${TRADINGAGENTS_NO_UPDATE_CHECK:=1}"
export TRADINGAGENTS_OUTPUT_LANGUAGE TRADINGAGENTS_MAX_DEBATE_ROUNDS \
       TRADINGAGENTS_MAX_RISK_ROUNDS TRADINGAGENTS_NO_UPDATE_CHECK

# magpie 网关的会话归属：每次 run 在 usage 视图里按 "标的@日期" 分组
export TRADINGAGENTS_CONVERSATION_ID="tradingagents-${TICKER}@${DATE}"

echo ">>> 分析 $TICKER @ $DATE，分析师: $ANALYSTS"
.venv/bin/tradingagents \
  --ticker "$TICKER" \
  --date "$DATE" \
  --analysts "$ANALYSTS" \
  --save --html --no-show

echo ">>> 报告目录: ${TRADINGAGENTS_RESULTS_DIR:-$HOME/.tradingagents/logs}/reports/"
