"""风险分级：low / high / critical

判定依据（按可信度排序）：
1. 步骤描述（desc）—— 规划器表达的操作意图
2. 目标选择器（target）—— `#approve` / `button[name=submit]` 这类命名本身带语义
3. 页面契约里该控件自己的文案（label / nearby_label）

为什么不能只看 desc：外部传入的 steps（`/task/execute`、`--plan-file`）可以不写 desc，
那时 desc 是空字符串，关键词匹配必然判成 low —— 审批按钮也照样点下去。
所以 desc 缺失时对 click 直接抬到 high，并且 target 与控件文案始终并入判定文本。
"""

CRITICAL_KEYWORDS = [
    # 中文业务动作
    "审批通过", "审批同意", "驳回", "拒绝申请", "审核通过", "审核同意",
    "放款", "付款", "转账", "打款", "退款确认", "删除账号", "注销",
    "确认签署", "确认发布", "强制提交", "复核通过", "终审",
    # 英文字段/控件命名
    "approve", "reject", "refuse", "delete-account", "delete_account",
    "payment", "transfer", "signoff", "sign-off",
]

HIGH_KEYWORDS = [
    "提交", "保存", "删除", "上传", "导入", "覆盖", "重置", "撤销",
    "submit", "save", "delete", "remove", "upload", "import", "overwrite",
    "reset", "revoke",
]


def _classify(text: str) -> str:
    s = (text or "").lower()
    for kw in CRITICAL_KEYWORDS:
        if kw in s:
            return "critical"
    for kw in HIGH_KEYWORDS:
        if kw in s:
            return "high"
    return "low"


def labels_for_target(target: str, controls: list[dict] | None) -> list[str]:
    """从页面契约里找出目标控件自身的文案（label / nearby_label / name）。

    契约里的 selector 与步骤 target 同源，通常等值；为兼容外部手写 steps
    （可能写 `#saveDraft` 而契约给的是 `button[id=saveDraft]`），再做一层
    宽松匹配：互相包含即认为指向同一控件。
    """
    if not target or not controls:
        return []
    key = target.strip()
    out: list[str] = []
    for c in controls:
        sel = (c.get("selector") or "").strip()
        if not sel:
            continue
        if sel == key or (len(sel) > 3 and sel in key) or (len(key) > 3 and key in sel):
            for field in ("label", "nearby_label", "name"):
                val = (c.get(field) or "").strip()
                if val and val not in out:
                    out.append(val)
    return out


def detect_step_risk(step: dict, controls: list[dict] | None = None) -> str:
    """按整条步骤判定风险：desc + target + 契约控件文案"""
    action = (step.get("action") or "").lower()
    desc = (step.get("desc") or "").strip()
    target = (step.get("target") or "").strip()
    text = " ".join(filter(None, [desc, target, *labels_for_target(target, controls)]))

    level = _classify(text)

    # 意图不可知的点击：非幂等、可能是提交/审批，一律按 high 处理（走人工确认）
    if level == "low" and action == "click" and len(desc) < 2:
        return "high"
    return level


def detect_risk(desc: str, target: str = "", controls: list[dict] | None = None) -> str:
    """兼容旧签名：只给描述文本也能判定"""
    return detect_step_risk({"action": "", "desc": desc, "target": target}, controls)
