"""执行轨迹录制"""
import json
import uuid

from src.utils.db import get_conn


class TraceRecorder:
    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        self.steps: list[dict] = []
        self.meta = {
            "page_key": "",
            "role": "default",
            "scene_tag": "default",
            "fingerprint": "",
            "human_repair": 0,
            "ephemeral_task": 0,
            "skill_source": "",  # reused / replanned
        }

    def set_meta(self, key: str, value):
        self.meta[key] = value

    def add_step(self, step: dict):
        self.steps.append(step)

    def save(self, success: bool, screenshot_path: str = "") -> str:
        payload = json.dumps({"meta": self.meta, "steps": self.steps}, ensure_ascii=False)
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO traces(trace_id, payload, success, screenshot,
                                   page_key, role, scene_tag, fingerprint)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    self.trace_id, payload, int(success), screenshot_path,
                    self.meta["page_key"], self.meta["role"],
                    self.meta["scene_tag"], self.meta["fingerprint"],
                ),
            )
        return self.trace_id
