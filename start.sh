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
  echo "已生成 .env，请填入 OPENAI_API_KEY 后重新运行"
  exit 1
fi

python -m src init
echo "启动 Web API：http://127.0.0.1:8000/docs"
python -m src web
