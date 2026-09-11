"""ChemCompute 网络配置与专用私有接口解析工具"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any

import psutil

logger = logging.getLogger(__name__)


def is_private_ip(ip_str: str) -> bool:
    """检查 IP 地址是否属于私有专用网段 (RFC 1918 / 本地环回)"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def get_private_interfaces() -> list[dict[str, Any]]:
    """
    检索系统所有网卡中配置的合法私有 IPv4 地址及网卡信息。
    排除公网 IP，确保内网集群通信安全。
    """
    candidates: list[dict[str, Any]] = []
    try:
        addrs = psutil.net_if_addrs()
        for iface_name, iface_addrs in addrs.items():
            for addr in iface_addrs:
                # 仅筛选 IPv4 地址
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if is_private_ip(ip) and not ip.startswith("127."):
                        candidates.append({
                            "interface": iface_name,
                            "ip": ip,
                            "netmask": addr.netmask,
                            "broadcast": addr.broadcast,
                        })
    except Exception as e:
        logger.warning("获取网络接口列表异常: %s", e)

    return candidates


def resolve_listen_host(
    requested_host: str | None = None,
    use_private_interface: bool = False,
    fallback_loopback: str = "127.0.0.1",
) -> str:
    """
    解析服务监听地址：
    1. 若显式指定 host (且非空)，使用指定地址
    2. 若请求绑定私有网络接口 (--private-interface)，自动探测可用的专用内网 IP
    3. 默认安全回退到本地环回 (127.0.0.1)
    """
    if requested_host and requested_host.strip():
        return requested_host.strip()

    if use_private_interface:
        private_ifaces = get_private_interfaces()
        if private_ifaces:
            selected_ip = private_ifaces[0]["ip"]
            logger.info("自动选定私有网络接口 IP: %s (网卡: %s)", selected_ip, private_ifaces[0]["interface"])
            return selected_ip
        else:
            logger.warning("未检测到可用的私有网络 IPv4 接口，回退至本地环回 %s", fallback_loopback)

    return fallback_loopback
