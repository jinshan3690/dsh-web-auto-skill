"""审计日志：同时写文件与数据库"""
import time

from web_auto_skill.config import STORAGE_ROOT

LOG_FILE = STORAGE_ROOT / "audit.log"


def audit_log(event_type: str, content: str, decision_reason: str = ""):
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] [{event_type}] {content} | {decision_reason}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    # 同时落库，供 Web 后台查询
    try:
        from web_auto_skill.utils.db import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log(event_type, content, decision_reason) VALUES (?,?,?)",
                (event_type, content, decision_reason),
            )
    except Exception:
        pass
