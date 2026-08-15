# -*- coding: utf-8 -*-
"""纯调度策略：选题排序、同网段判断。"""
import ipaddress

DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def select_next(challenges, skip, active_cids, blocked=None):
    """从候选题中选下一题：未完成、未 skip、未 active、未冷却；easy→medium→hard，同难度低分→高分。"""
    blocked = blocked or {}
    now = __import__("time").time()
    cands = [
        c for c in challenges
        if not c.get("is_completed")
        and c["unique_code"] not in skip
        and c["unique_code"] not in active_cids
        and (c["unique_code"] not in blocked or blocked[c["unique_code"]] < now)
    ]
    cands.sort(key=lambda c: (DIFF_ORDER.get(c.get("difficulty"), 9), c.get("total_score", 0)))
    return cands[0] if cands else None


def same_subnet(addr_a, addr_b):
    """同网段判断：IPv4 比 /24（前三段），IPv6 比 /64 前缀。空地址返回 False。"""
    def first_ip(addrs):
        for s in addrs or []:
            try:
                return ipaddress.ip_address(s.split(":")[0])
            except ValueError:
                continue
        return None

    ia, ib = first_ip(addr_a), first_ip(addr_b)
    if ia is None or ib is None or ia.version != ib.version:
        return False
    if ia.version == 4:
        return str(ia).split(".")[:3] == str(ib).split(".")[:3]
    return ia.packed[:8] == ib.packed[:8]
