"""执行轨迹录制"""
import json
import time
import uuid

from web_auto_skill.utils.db import get_conn


class TraceRecorder:
    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        self.steps: list[dict] = []
        self.started_at = time.time()
        self.meta = {
            "page_key": "",
            "role": "default",
            "scene_tag": "default",
            "fingerprint": "",
            "human_repair": 0,
            "ephemeral_task": 0,
            "skill_source": "",  # reused / replanned / external
        }

    def set_meta(self, key: str, value):
        self.meta[key] = value

    def add_step(self, step: dict):
        self.steps.append(step)

    def elapsed_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def save(self, success: bool, screenshot_path: str = "") -> str:
        payload = json.dumps(
            {"meta": self.meta, "steps": self.steps, "duration_ms": self.elapsed_ms()},
            ensure_ascii=False,
        )
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO traces(trace_id, payload, success, screenshot,
                                   page_key, role, scene_tag, fingerprint, duration_ms)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.trace_id, payload, int(success), screenshot_path,
                    self.meta["page_key"], self.meta["role"],
                    self.meta["scene_tag"], self.meta["fingerprint"],
                    self.elapsed_ms(),
                ),
            )
        return self.trace_id
