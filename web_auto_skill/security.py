"""HTTP 入参安全校验

Web Auto Skill 的接口本质是「让本机浏览器去访问一个 URL 并执行动作」，
一旦暴露到局域网/公网，就等于把内网访问能力和本地文件读取能力开放出去：

- `page_key` 任意填 → SSRF（打内网管理后台、云元数据 169.254.169.254）
- `page_key: file:///etc/passwd` → 读本地文件
- `/skill/import` 注入 steps → 下次执行时被 `run_task` 带出来

所以在 API 层（不是 CLI 层）做三件事：协议白名单、内网地址拒绝、共享 token。
CLI 是用户自己在本机跑，不受这些限制 —— 那是用户自己的机器。
"""
import ipaddress
import socket
from urllib.parse import urlparse

from web_auto_skill.config import ALLOW_FILE_URL, ALLOW_PRIVATE_NETWORK

_ALLOWED_SCHEMES = {"http", "https"}
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_hosts(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def validate_page_url(url: str) -> str:
    """校验 page_key，返回规范化后的 URL；不合法抛 ValueError（调用方转 400）"""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("page_key 不能为空")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()

    if scheme == "file":
        if not ALLOW_FILE_URL:
            raise ValueError("不允许 file:// 地址（需要本地调试请设置 ALLOW_FILE_URL=true）")
        return raw

    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"不支持的协议 {scheme or '(空)'}，只允许 http/https")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("page_key 缺少主机名")

    if ALLOW_PRIVATE_NETWORK:
        return raw

    if host.lower() in _LOOPBACK_NAMES:
        raise ValueError("不允许访问环回地址（需要访问本机服务请设置 ALLOW_PRIVATE_NETWORK=true）")

    # 字面量 IP 直接判断；域名解析后再判断，挡住 basic SSRF
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_private_ip(literal):
            raise ValueError("不允许访问内网/保留地址（请设置 ALLOW_PRIVATE_NETWORK=true）")
        return raw

    for ip_str in _resolve_hosts(host):
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_private_ip(ip):
            raise ValueError(f"{host} 解析到内网地址 {ip_str}，已拒绝")

    return raw
