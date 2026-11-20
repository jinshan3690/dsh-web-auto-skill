"""Web Auto Skill CLI 入口"""
import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

from web_auto_skill.audit.logger import audit_log
from web_auto_skill.browser.interceptor import confirm_high_risk
from web_auto_skill.browser.runner import inspect_contract, run_task_detailed
from web_auto_skill.config import ensure_dirs
from web_auto_skill.sample.sample_store import (
    contribute_sample,
    list_samples,
    semantic_search_samples,
)
from web_auto_skill.skill.inspector import inspect_all, list_deprecated
from web_auto_skill.skill.skill_store import (
    get_skill,
    remove_skill_family,
    rollback_skill,
    semantic_recall,
)
from web_auto_skill.utils.db import get_conn, init_db

app = typer.Typer(help="Web Auto Skill - 网页操作 Skill 引擎")
console = Console()


def _parse_params(params_json: str | None) -> dict | None:
    if not params_json:
        return None
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as e:
        console.print(f"[red]--params 不是合法 JSON: {e}[/red]")
        raise typer.Exit(1) from e
    if not isinstance(params, dict):
        console.print("[red]--params 必须是 JSON 对象，如 {\"applicant\":\"张三\"}[/red]")
        raise typer.Exit(1)
    return params


def _print_result(result: dict):
    ok = result.get("success")
    browser = result.get("browser_source") or ""
    console.print(
        f"trace_id={result.get('trace_id')}  success={ok}  "
        f"plan_source={result.get('plan_source')}{'  browser=' + browser if browser else ''}"
        f"  {result.get('duration_ms', 0)}ms"
    )
    if result.get("learned_skill"):
        console.print(f"[green]已学习: {result['learned_skill']}  ({result.get('learn_reason')})[/green]")
    elif result.get("learn_reason"):
        console.print(f"[dim]学习决策: {result['learn_reason']}[/dim]")
    if result.get("error"):
        console.print(f"[red]错误: {result['error']}[/red]")


@app.command("browser")
def cmd_browser(
    port: int = typer.Option(9222, "--port", help="调试端口"),
    profile: str = typer.Option(None, "--profile", help="专用数据目录（登录态存在这里）"),
    status: bool = typer.Option(False, "--status", help="只看状态，不拉起窗口"),
    stop: bool = typer.Option(False, "--stop", help="关掉引擎自动拉起的那个窗口"),
):
    """查看 / 准备可接管的浏览器（平时不用手动执行）

    跑任务时引擎自己会保证有这么个窗口：有可接管的就复用，没有就自动拉起。
    只有两种情况需要这个命令：
    - 想提前登录一次：`browser` 打开窗口，登完以后所有任务复用这份登录态
    - 想省内存：`browser --stop` 关掉常驻窗口（登录态还在 profile 里，下次自动恢复）
    """
    from web_auto_skill.browser import cdp as cdp_tool

    if stop:
        closed = cdp_tool.stop(f"http://127.0.0.1:{port}") or cdp_tool.stop()
        if closed:
            console.print("[green]已关闭引擎拉起的浏览器窗口[/green]")
        else:
            console.print("[yellow]没有找到引擎拉起的窗口[/yellow]"
                          "（手动用 --remote-debugging-port 开的浏览器不归这个命令管）")
        raise typer.Exit(0)

    url = f"http://127.0.0.1:{port}"
    alive = cdp_tool.is_alive(url)
    if status:
        console.print(f"{url}  {'[green]可接管[/green]' if alive else '[yellow]无响应[/yellow]'}")
        state = cdp_tool.state()
        if state.get("url"):
            console.print(f"[dim]引擎记录: {state['url']}  pid={state.get('pid')}  "
                          f"profile={state.get('profile')}[/dim]")
        raise typer.Exit(0 if alive else 1)

    if alive:
        console.print(f"[green]已有可接管的浏览器：{url}[/green]")
    else:
        try:
            cdp_tool.launch(port=port, profile=profile or "")
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e
        console.print(f"[green]已打开浏览器：{url}[/green]")

    console.print("在这个窗口登录一次，之后所有任务都会复用它的登录态（不必再加 --cdp）。"
                  "想关掉常驻窗口：`browser --stop`")


@app.command("init")
def cmd_init():
    """初始化数据库与目录"""
    ensure_dirs()
    init_db()
    console.print("[green]初始化完成[/green]")


@app.command("run")
def cmd_run(
    command: str = typer.Argument(..., help="自然语言任务指令"),
    page_key: str = typer.Argument(..., help="目标页面 URL"),
    role: str = typer.Option("default", "--role"),
    scene_tag: str = typer.Option("default", "--scene"),
    headless: bool = typer.Option(False, "--headless", help="是否无头模式"),
    learn: bool = typer.Option(True, "--learn/--no-learn", help="成功后是否自动学习"),
    plan_file: str = typer.Option(None, "--plan-file", help="外部规划 JSON 文件（不配置 LLM 时使用）"),
    params_json: str = typer.Option(None, "--params", help='参数字典，如 {"applicant":"张三"}'),
    cdp: str = typer.Option(
        None, "--cdp",
        help="接管指定的浏览器（可只写端口 9222）。默认自动复用/拉起一个可接管的窗口",
    ),
    fresh: bool = typer.Option(
        False, "--fresh",
        help="不复用浏览器：每次现场拉临时浏览器、跑完就关（登录态不留）",
    ),
):
    """执行一次网页任务"""
    ensure_dirs()
    init_db()

    plan_override = None
    if plan_file:
        with open(plan_file, encoding="utf-8") as f:
            plan_override = json.load(f)
        if not isinstance(plan_override, list):
            console.print("[red]plan 文件必须是 JSON 数组[/red]")
            raise typer.Exit(1)

    async def _confirm(desc: str) -> bool:
        return await asyncio.to_thread(confirm_high_risk, desc)

    params = _parse_params(params_json)

    async def _inner():
        return await run_task_detailed(
            command, page_key, role, scene_tag,
            headless=headless, confirm=_confirm, learn=learn,
            plan_override=plan_override, params=params, cdp_url=cdp,
            auto_cdp=False if fresh else None,
        )

    result = asyncio.run(_inner())
    _print_result(result)


@app.command("execute")
def cmd_execute(
    page_key: str = typer.Argument(..., help="目标页面 URL"),
    plan_file: str = typer.Option(..., "--plan-file", help="步骤 JSON 文件"),
    role: str = typer.Option("default", "--role"),
    scene_tag: str = typer.Option("default", "--scene"),
    headless: bool = typer.Option(False, "--headless"),
    learn: bool = typer.Option(True, "--learn/--no-learn"),
    params_json: str = typer.Option(None, "--params", help='参数字典，如 {"applicant":"张三"}'),
    cdp: str = typer.Option(
        None, "--cdp",
        help="接管指定的浏览器（可只写端口 9222）。默认自动复用/拉起一个可接管的窗口",
    ),
    fresh: bool = typer.Option(
        False, "--fresh",
        help="不复用浏览器：每次现场拉临时浏览器、跑完就关（登录态不留）",
    ),
):
    """纯执行：按外部给定的步骤运行，不需要 LLM"""
    ensure_dirs()
    init_db()
    with open(plan_file, encoding="utf-8") as f:
        plan = json.load(f)

    async def _confirm(desc: str) -> bool:
        return await asyncio.to_thread(confirm_high_risk, desc)

    params = _parse_params(params_json)

    async def _inner():
        return await run_task_detailed(
            "执行外部计划", page_key, role, scene_tag,
            headless=headless, confirm=_confirm, learn=learn,
            plan_override=plan, params=params, cdp_url=cdp,
            auto_cdp=False if fresh else None,
        )

    result = asyncio.run(_inner())
    _print_result(result)


@app.command("contract")
def cmd_contract(
    page_key: str = typer.Argument(..., help="目标页面 URL"),
    headless: bool = typer.Option(True, "--headless/--show"),
    cdp: str = typer.Option(
        None, "--cdp",
        help="接管指定的浏览器（可只写端口 9222）。默认自动复用/拉起一个可接管的窗口",
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="不复用浏览器，扫完就关",
    ),
):
    """只扫描页面契约与指纹，供外部 Agent 自行规划"""
    ensure_dirs()
    scanned = asyncio.run(inspect_contract(
        page_key, headless=headless, cdp_url=cdp,
        auto_cdp=False if fresh else None,
    ))
    console.print(f"fingerprint = {scanned['fingerprint']}")
    console.print_json(json.dumps(scanned["contract"], ensure_ascii=False))


@app.command("health")
def cmd_health():
    """查看服务与 LLM 状态"""
    from web_auto_skill.llm.embed import backend_detail
    from web_auto_skill.llm.provider import describe

    info = describe()
    info["embed"] = backend_detail()
    console.print_json(json.dumps(info, ensure_ascii=False))


@app.command("skill-list")
def cmd_skill_list(
    page_key: str = typer.Option(None, "--page"),
    role: str = typer.Option("default", "--role"),
    scene_tag: str = typer.Option("default", "--scene"),
):
    """列出可用 Skill"""
    table = Table(title="Skills")
    for col in ("skill_id", "version", "page_key", "fingerprint", "summary"):
        table.add_column(col)

    with get_conn() as conn:
        if page_key:
            rows = conn.execute(
                "SELECT payload FROM skills WHERE page_key=? AND role=? AND scene_tag=? AND is_deprecated=0",
                (page_key, role, scene_tag),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT payload FROM skills WHERE is_deprecated=0 ORDER BY created_at DESC"
            ).fetchall()

    for r in rows:
        sk = json.loads(r["payload"])
        table.add_row(
            sk.get("skill_id", ""), sk.get("version", ""), sk.get("page_key", ""),
            sk.get("contract_fingerprint", "")[:8], (sk.get("summary") or "")[:40],
        )
    console.print(table)


@app.command("skill-show")
def cmd_skill_show(skill_id: str, version: str = typer.Option(None, "--version")):
    """查看 Skill 详情"""
    sk = get_skill(skill_id, version)
    if not sk:
        console.print("[red]Skill 不存在[/red]")
        raise typer.Exit(1)
    console.print_json(json.dumps(sk, ensure_ascii=False))


@app.command("skill-search")
def cmd_skill_search(q: str, top_k: int = typer.Option(3, "--top-k")):
    """语义检索 Skill"""
    res = semantic_recall(q, top_k)
    if not res:
        console.print("没有可用 Skill")
        return
    for score, sk in res:
        console.print(f"{score:.3f}  {sk['skill_id']}@{sk.get('version')}  "
                      f"{sk.get('page_key')}  {(sk.get('summary') or '')[:40]}")


@app.command("skill-rollback")
def cmd_skill_rollback(skill_id: str, version: str):
    """回滚 Skill 到指定版本"""
    ok, msg = rollback_skill(skill_id, version)
    console.print(("[green]" if ok else "[red]") + msg)


@app.command("skill-remove")
def cmd_skill_remove(skill_id: str, yes: bool = typer.Option(False, "--yes", help="跳过确认")):
    """删除整条技能家族（所有版本）"""
    if not yes and not typer.confirm(f"删除 {skill_id} 的全部版本？", default=False):
        raise typer.Exit(0)
    n = remove_skill_family(skill_id)
    console.print(f"已删除 {n} 个版本" if n else "Skill 不存在")


@app.command("trace-list")
def cmd_trace_list(limit: int = typer.Option(20, "--limit")):
    """列出最近执行轨迹"""
    table = Table(title="Traces")
    for col in ("trace_id", "success", "page_key", "ms", "screenshot", "created_at"):
        table.add_column(col)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT trace_id, success, page_key, screenshot, duration_ms, created_at "
            "FROM traces ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    for r in rows:
        table.add_row(r["trace_id"], str(bool(r["success"])),
                      (r["page_key"] or "")[:40], str(r["duration_ms"] or 0),
                      r["screenshot"] or "", r["created_at"])
    console.print(table)


@app.command("trace-show")
def cmd_trace_show(trace_id: str):
    """查看轨迹详情"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, success, screenshot, duration_ms FROM traces WHERE trace_id=?",
            (trace_id,),
        ).fetchone()
    if not row:
        console.print("[red]trace 不存在[/red]")
        raise typer.Exit(1)
    console.print(f"success={bool(row['success'])}  {row['duration_ms'] or 0}ms  "
                  f"screenshot={row['screenshot']}")
    console.print_json(row["payload"])


@app.command("inspect")
def cmd_inspect(show_window: bool = typer.Option(False, "--show", help="是否弹出浏览器窗口")):
    """手动批量巡检（管理员工具，不自动调度）"""
    async def _inner():
        return await inspect_all(headless=not show_window)

    invalid = asyncio.run(_inner())
    if not invalid:
        console.print("[green]全部 Skill 指纹校验通过[/green]")
        return
    console.print(f"[yellow]失效 {len(invalid)} 条：[/yellow]")
    for item in invalid:
        console.print(item)


@app.command("inspect-deprecated")
def cmd_inspect_deprecated():
    """列出已失效 Skill"""
    for r in list_deprecated():
        console.print(f"{r['skill_id']}@{r['version']}  {r['page_key']}")


@app.command("sample-contribute")
def cmd_sample_contribute(skill_id: str):
    """把 Skill 脱敏后贡献到样本库"""
    sk = get_skill(skill_id)
    if not sk:
        console.print("[red]Skill 不存在[/red]")
        raise typer.Exit(1)
    sid = contribute_sample(sk)
    audit_log("sample_contribute", sid, f"来自 {skill_id}")
    console.print(f"sample_id={sid}")


@app.command("sample-search")
def cmd_sample_search(q: str, top_k: int = typer.Option(3, "--top-k")):
    """语义搜索样本"""
    for score, sm in semantic_search_samples(q, top_k):
        console.print(f"{score:.3f}  {sm.get('sample_id')}  {(sm.get('summary') or '')[:50]}")


@app.command("sample-list")
def cmd_sample_list(limit: int = typer.Option(50, "--limit")):
    """列出样本"""
    for r in list_samples(limit):
        console.print(r)


@app.command("web")
def cmd_web(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """启动 Web API 服务"""
    import uvicorn
    ensure_dirs()
    init_db()
    uvicorn.run("web_auto_skill.web.main:app", host=host, port=port, reload=reload)


@app.command("embed-setup")
def cmd_embed_setup(
    force: bool = typer.Option(False, "--force", help="已存在也重新下载"),
    endpoint: str = typer.Option(None, "--endpoint", help="模型源，默认 hf-mirror"),
):
    """下载本地 ONNX 中文向量模型（约 22MB，无需 PyTorch）"""
    from web_auto_skill.llm.embed_setup import download

    try:
        info = download(force=force, endpoint=endpoint)
    except Exception as e:
        console.print(f"[red]下载失败: {e}[/red]")
        console.print("可手动设置 HF_ENDPOINT 换源，或直接跳过（会降级为 n-gram 检索）")
        raise typer.Exit(1) from e
    console.print(info)

    from web_auto_skill.llm.embed import backend_detail

    console.print(backend_detail())


@app.command("audit")
def cmd_audit(limit: int = typer.Option(50, "--limit")):
    """查看审计日志"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT event_type, content, decision_reason, created_at FROM audit_log "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    for r in rows:
        console.print(f"{r['created_at']}  [{r['event_type']}]  {r['content']}  | {r['decision_reason']}")


if __name__ == "__main__":
    app()
