"""风险分级：low / high / critical

原则：
- critical：审批、决策、资金、不可逆业务动作 —— 一律禁止 Agent 自动执行
- high：有副作用但可恢复 —— 需人工确认
- low：读取、填充、查询、翻页 —— 自动执行
"""

CRITICAL_KEYWORDS = [
    "审批通过", "审批同意", "驳回", "拒绝申请", "审核通过", "审核同意",
    "放款", "付款", "转账", "打款", "退款确认", "删除账号", "注销",
    "确认签署", "确认发布", "强制提交",
]

HIGH_KEYWORDS = [
    "提交", "保存", "删除", "上传", "导入", "覆盖", "重置", "撤销",
]

LOW_HINTS = ["查询", "搜索", "读取", "查看", "翻页", "下一页", "填写", "输入"]


def detect_risk(desc: str) -> str:
    """根据步骤描述判定风险等级"""
    s = (desc or "").lower()

    for kw in CRITICAL_KEYWORDS:
        if kw in s:
            return "critical"

    for kw in HIGH_KEYWORDS:
        if kw in s:
            return "high"

    return "low"
