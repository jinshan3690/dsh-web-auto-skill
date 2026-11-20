"""SQLite 数据层 - POC 阶段本地存储"""
import sqlite3
from contextlib import contextmanager

from src.config import DB_PATH, ensure_dirs


@contextmanager
def get_conn():
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    ensure_dirs()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT,
            version TEXT,
            page_key TEXT,
            role TEXT,
            scene_tag TEXT,
            contract_fingerprint TEXT,
            payload TEXT,
            update_log TEXT,
            is_deprecated INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (skill_id, version)
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS traces (
            trace_id TEXT PRIMARY KEY,
            payload TEXT,
            success INTEGER,
            screenshot TEXT,
            page_key TEXT,
            role TEXT,
            scene_tag TEXT,
            fingerprint TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            sample_id TEXT PRIMARY KEY,
            skill_id TEXT,
            payload TEXT,
            score REAL DEFAULT 0,
            reviewed INTEGER DEFAULT 0,
            is_deprecated INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            content TEXT,
            decision_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_skill_page ON skills(page_key, role, scene_tag)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_skill_fp ON skills(contract_fingerprint)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_skill_dep ON skills(is_deprecated)")
