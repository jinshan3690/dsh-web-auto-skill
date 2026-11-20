"""本地规则规划器（不依赖任何模型）

作用：在没有 API Key、也没有上层 Agent 提供 steps 的情况下，
根据「用户指令 + 页面契约」生成可执行步骤，让冷启动仍然可用。

能力边界（诚实说明）：
- 适合表单填写、查询、保存这类结构清晰的页面
- 依赖控件的 name / label / placeholder / 关联标签文本
- 复杂交互（多级弹窗、动态校验、跨页流程）仍需模型或外部规划
"""
import re

# 字段语义字典：key -> 触发词
FIELD_DICT: dict[str, list[str]] = {
    "applicant": ["申请人", "申请者", "填表人", "姓名", "name", "applicant"],
    "reason": ["事由", "原因", "用途", "reason", "purpose"],
    "amount": ["金额", "费用", "价格", "amount", "money"],
    "date": ["日期", "时间", "date", "time"],
    "phone": ["电话", "手机", "phone", "mobile", "tel"],
    "email": ["邮箱", "邮件", "email", "mail"],
    "remark": ["备注", "说明", "描述", "remark", "note", "comment"],
    "title": ["标题", "名称", "title"],
    "category": ["类型", "类别", "种类", "category", "type"],
    "department": ["部门", "科室", "department", "dept"],
    "id_no": ["身份证", "证件", "工号", "id"],
    "address": ["地址", "address"],
}

# 常见中文姓氏，用于识别「张三」这类人名
_SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"

_FIELD_LABEL = {
    "applicant": "申请人",
    "reason": "事由",
    "amount": "金额",
    "date": "日期",
    "phone": "电话",
    "email": "邮箱",
    "remark": "备注",
    "title": "标题",
    "category": "类型",
    "department": "部门",
    "id_no": "证件号",
    "address": "地址",
}

_QUERY_WORDS = ["查询", "搜索", "查找", "检索", "筛选"]
_SAVE_WORDS = ["保存", "草稿", "暂存"]
_SUBMIT_WORDS = ["提交", "申报", "上报"]


class PlanningError(Exception):
    pass


def _control_text(c: dict) -> str:
    parts = [
        c.get("name", ""), c.get("label", ""), c.get("nearby_label", ""),
        c.get("selector", ""), c.get("placeholder", ""),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _match_field(control_text: str) -> tuple[str, int] | None:
    """返回 (field_key, 命中词长度)，取最长命中优先"""
    best = None
    for key, words in FIELD_DICT.items():
        for w in words:
            if w.lower() in control_text:
                cand = (key, len(w))
                if best is None or cand[1] > best[1]:
                    best = cand
    return best


def _extract_value(cmd: str, field_key: str, control: dict) -> str | None:
    """从指令中抽取该字段的值"""
    label_words = FIELD_DICT.get(field_key, [])
    joined = "|".join(re.escape(w) for w in label_words)

    # 1) 「字段：值」或「字段 值」模式
    pattern = (
        "(?:" + joined + ")"
        r"\s*[:：是为]?\s*"
        r"([^\s，,。；;、]{1,30})"
    )
    m = re.search(pattern, cmd, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        if val and val not in ("的", "填"):
            return val

    # 2) 金额 / 数字
    if field_key == "amount":
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb)?", cmd)
        if m:
            return m.group(1)

    # 3) 日期
    if field_key == "date":
        m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", cmd)
        if m:
            return m.group(1)
        m = re.search(r"(\d{1,2}月\d{1,2}日)", cmd)
        if m:
            return m.group(1)

    # 4) 中文人名
    if field_key == "applicant":
        for name in re.findall(r"[\u4e00-\u9fa5]{2,4}", cmd):
            if name[0] in _SURNAMES and name not in ("申请人", "填写", "报销", "表单"):
                return name

    # 5) 手机号 / 邮箱
    if field_key == "phone":
        m = re.search(r"(1[3-9]\d{9})", cmd)
        if m:
            return m.group(1)
    if field_key == "email":
        m = re.search(r"([\w.+-]+@[\w-]+\.\w+)", cmd)
        if m:
            return m.group(1)

    # 6) select：匹配选项
    options = control.get("options") or []
    for opt in options:
        if opt and opt in cmd:
            return opt

    return None


def _find_button(controls: list[dict], words: list[str]) -> dict | None:
    for c in controls:
        if c.get("tag") != "button":
            continue
        text = f"{c.get('label', '')} {c.get('nearby_label', '')}".lower()
        for w in words:
            if w in text:
                return c
    return None


def plan_locally(user_cmd: str, contract: dict) -> list[dict]:
    """规则规划：返回 steps 列表，规划不出时抛 PlanningError"""
    controls = contract.get("controls", [])
    if not controls:
        raise PlanningError("页面没有可用控件，无法本地规划")

    steps: list[dict] = []
    used_fields: set[str] = set()

    # 1) 填字段
    for c in controls:
        if c.get("tag") not in ("input", "textarea", "select"):
            continue
        if c.get("type") in ("submit", "button", "hidden"):
            continue

        matched = _match_field(_control_text(c))
        if not matched:
            continue
        field_key = matched[0]
        if field_key in used_fields:
            continue

        value = _extract_value(user_cmd, field_key, c)

        if c.get("tag") == "select":
            if value and any(value == o for o in (c.get("options") or [])):
                steps.append({
                    "action": "select", "target": c["selector"],
                    "value": value, "desc": f"选择{_FIELD_LABEL.get(field_key, field_key)}",
                })
                used_fields.add(field_key)
            continue

        if not value:
            continue

        steps.append({
            "action": "fill", "target": c["selector"], "value": value,
            "desc": f"填写{_FIELD_LABEL.get(field_key, field_key)}",
            "field_key": field_key,
        })
        used_fields.add(field_key)

    # 2) 动作按钮
    if any(w in user_cmd for w in _QUERY_WORDS):
        btn = _find_button(controls, _QUERY_WORDS)
        if btn:
            steps.append({"action": "click", "target": btn["selector"], "desc": "点击查询"})

    if any(w in user_cmd for w in _SAVE_WORDS):
        btn = _find_button(controls, _SAVE_WORDS)
        if btn:
            steps.append({"action": "click", "target": btn["selector"], "desc": "点击保存"})

    if any(w in user_cmd for w in _SUBMIT_WORDS):
        btn = _find_button(controls, _SUBMIT_WORDS)
        if btn:
            steps.append({"action": "click", "target": btn["selector"], "desc": "点击提交"})

    if not steps:
        raise PlanningError(
            "本地规划失败：指令中没有可映射到页面控件的字段或动作，"
            "请配置模型、或由调用方传入 steps"
        )

    return steps
