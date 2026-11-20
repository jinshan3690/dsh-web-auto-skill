#!/usr/bin/env bash
# 启动脚本（macOS / Linux）
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "创建虚拟环境..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -e .
playwright install chromium

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "已生成 .env —— 不填 API Key 也能用（走本地规则规划器），按需再配。"
fi

# 语义召回模型（22MB，可选；下载失败不影响使用，会自动降级）
if ! python -c "from src.llm.embed_onnx import model_ready; raise SystemExit(0 if model_ready() else 1)" 2>/dev/null; then
  echo "可选：python -m src embed-setup  （下载 22MB 中文向量模型，提升 Skill 召回精度）"
fi

python -m src init
echo "启动 Web API：http://127.0.0.1:8000/docs"
python -m src web
