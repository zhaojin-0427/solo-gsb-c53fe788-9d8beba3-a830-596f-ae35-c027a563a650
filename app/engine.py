"""航标灯质可辨性分析引擎。

对任意两盏灯 A、B，穷举各自独立的起始相位 (i, j)（i 取遍 A 整串、
j 取遍 B 整串，共 La*Lb 种组合，全程不抽样、不略过任何一种），
计算前 n 位（n = 1..W）的汉明距离 d_{i,j}(n)。

穷举实现说明
------------
两位差序列满足平移性质::

    m_{i,j}(t) = m_{i+1, j+1}(t-1)

即“两灯同时提前 1 位”只是把差异序列整体平移一位。按
(i - j) mod g（g = gcd(La, Lb)）可把全部 La*Lb 个相位组合划分为
g 个等价类，每类恰有 L = lcm(La, Lb) 个成员，同类成员的差异序列
彼此是循环左移一位的关系（L 为其基本周期）。第 q 类用
``(i, j) = (s mod La, (s-q) mod Lb), s = 0..L-1`` 完整列出其 L 个
成员，g 类合计 g*L = La*Lb 个组合，覆盖所有独立相位，无一遗漏。

实现上把 g 个类、L 个位移、W 个时刻一次性张成 (g, L, W) 张量
（元素总数恰为 La*Lb*W，与逐相位朴素枚举一一对应），再用 NumPy
向量化前缀和一次性求出每个组合、每个窗口长度的汉明距离。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# 硬性参数边界（与 schemas 中的校验保持一致）
MIN_LIGHTS, MAX_LIGHTS = 2, 20
MIN_PATTERN_LEN, MAX_PATTERN_LEN = 2, 80
MIN_W, MAX_W = 1, 400
MIN_K, MAX_K = 0, 3
SECONDS_PER_BIT = 0.1


def analyze(patterns: list[str], w: int, k: int) -> dict[str, Any]:
    """对全部灯对执行穷举相位分析。

    参数
    ----
    patterns: 每盏灯的循环 0/1 串（长度 2~80，同时含 0、1）。
    w: 观测窗口上限（1~400）。
    k: 每段最多误判位数（0~3），可辨阈值为最小距离 > 2k。

    返回
    ----
    见 README「分析结果数据结构」。
    """
    m = len(patterns)
    if not (MIN_LIGHTS <= m <= MAX_LIGHTS):
        raise ValueError("灯的数量必须在 2~20 之间")
    if not (MIN_W <= w <= MAX_W):
        raise ValueError("W 必须在 1~400 之间")
    if not (MIN_K <= k <= MAX_K):
        raise ValueError("k 必须在 0~3 之间")

    arrs = [np.frombuffer(p.encode("ascii"), dtype=np.uint8) - ord("0") for p in patterns]
    for idx, p in enumerate(patterns):
        if not (MIN_PATTERN_LEN <= len(p) <= MAX_PATTERN_LEN) or any(c not in "01" for c in p):
            raise ValueError(f"灯 {idx + 1} 的灯串非法")
        if not (arrs[idx].any() and (~arrs[idx].astype(bool)).any()):
            raise ValueError(f"灯 {idx + 1} 的灯串必须同时含 0 和 1")

    threshold = 2 * k

    # bottleneck[n-1] = 所有灯对在窗口 n 下的最小距离的最小值
    # （全局最易混淆灯对的最近距离曲线）
    bottleneck = np.full(w, np.iinfo(np.int64).max, dtype=np.int64)

    pairs_raw: list[dict[str, Any]] = []

    for a in range(m):
        pa = arrs[a]
        la = pa.size
        for b in range(a + 1, m):
            pb = arrs[b]
            lb = pb.size
            g = math.gcd(la, lb)
            period = la // g * lb  # lcm(la, lb)

            # 相位组合 (i,j) 按 i-j (mod g) 分成 g 个等价类 q=0..g-1，
            # 每类用位移 s=0..L-1 参数化为 (i=s mod La, j=(s-q) mod Lb)，
            # 合计 g*L = La*Lb 个组合，恰好覆盖全部独立相位。
            qvec = np.arange(g, dtype=np.int64)[:, None]       # (g,1)
            uu = np.arange(period, dtype=np.int64)[None, :]    # (1,L)
            i_idx = (uu % la)                                  # (1,L)
            j_idx = (uu - qvec) % lb                           # (g,L)
            # 各类的基本逐位差异 mm_all[q, u]（u=0..L-1）
            mm_all = (pa[i_idx] != pb[j_idx]).astype(np.int8)  # (g,L)

            # 位移 s 下第 t 位差异 = mm_all[q, (t+s) mod L]
            s = np.arange(period, dtype=np.int64)
            t = np.arange(w, dtype=np.int64)
            rot_idx = (t[None, :] + s[:, None]) % period       # (L,W)
            mismatch = mm_all[:, rot_idx]                      # (g,L,W)
            # 元素总数 g*L*W = La*Lb*W，与逐相位朴素枚举完全一致（无抽样）

            # dist[q, s, n] = 该类、该位移下前 n 位汉明距离
            dist = np.zeros((g, period, w + 1), dtype=np.int32)
            np.cumsum(mismatch, axis=2, dtype=np.int32, out=dist[:, :, 1:])

            flat = dist[:, :, 1:].reshape(g * period, w)       # (La*Lb, W)
            # 每个窗口长度 n 下，穷举全部相位得到的最小距离与对应相位
            dmin = flat.min(axis=0)  # shape (W,)
            # argmin 取首个最小值：相位按 q 主序、s 次序线性展开，确定可复现
            ph_best = flat.argmin(axis=0)  # shape (W,)

            bottleneck = np.minimum(bottleneck, dmin)
            pairs_raw.append(
                {
                    "a": a,
                    "b": b,
                    "la": la,
                    "lb": lb,
                    "g": g,
                    "period": period,
                    "dmin": dmin,
                    "ph_best": ph_best,
                    "confused_at_w": bool(dmin[w - 1] <= threshold),
                }
            )

    if int(bottleneck[w - 1]) > threshold:
        # 最短可辨窗口：dmin 单调不减（前 n 位距离随 n 只增不减），
        # 找到第一个全局最小距离 > 2k 的 n 即为最短
        ok = np.nonzero(bottleneck > threshold)[0]
        n_star = int(ok[0]) + 1
        shortest = n_star
        solvable = True
    else:
        n_star = None
        shortest = None
        solvable = False

    # 第二遍：在“决定性窗口”下重放逐位见证。
    # 可辨时决定性窗口 = 最短窗口 n*（该对在 n* 下最近的相位）；
    # 无解时决定性窗口 = W（仍混淆灯对在 W 下的最近相位）。
    pairs: list[dict[str, Any]] = []
    for raw in pairs_raw:
        a, b = raw["a"], raw["b"]
        pa, pb = arrs[a], arrs[b]
        la, lb = raw["la"], raw["lb"]
        g, period = raw["g"], raw["period"]
        dmin, ph_best = raw["dmin"], raw["ph_best"]

        if solvable:
            # 该对首个 dmin > 阈值的窗口（不大于 n*）
            first_ok = int(np.nonzero(dmin > threshold)[0][0])
            win = first_ok + 1
        else:
            win = w
        ph = int(ph_best[win - 1])
        q0, s0 = divmod(ph, period)
        i0 = int(s0 % la)
        j0 = int((s0 - q0) % lb)

        # 直接重放该见证相位下前 win 位（再次穷举结果的解码，不做近似）
        mm = np.array(
            [pa[(tt + i0) % la] != pb[(tt + j0) % lb] for tt in range(win)],
            dtype=np.int8,
        )
        diff_positions = [int(pos) for pos in np.nonzero(mm)[0].tolist()]
        bits_a = "".join(str(int(pa[(tt + i0) % la])) for tt in range(win))
        bits_b = "".join(str(int(pb[(tt + j0) % lb])) for tt in range(win))
        mark = "".join("^" if tt in set(diff_positions) else " " for tt in range(win))

        pairs.append(
            {
                "a": a,
                "b": b,
                "len_a": la,
                "len_b": lb,
                "phase_pairs_total": la * lb,
                "phase_pairs_enumerated": la * lb,
                "phase_equivalence_classes": int(g),
                "dmin": [int(x) for x in dmin.tolist()],
                "witness_window": win,
                "witness": {
                    "phase_a": i0,
                    "phase_b": j0,
                    "distance": int(dmin[win - 1]),
                    "distance_at_w": int(dmin[w - 1]),
                    "diff_positions": diff_positions,
                    "diff_count": len(diff_positions),
                    "bits_a": bits_a,
                    "bits_b": bits_b,
                    "diff_mark": mark,
                },
                "confused_at_w": raw["confused_at_w"],
            }
        )

    return {
        "w": w,
        "k": k,
        "threshold": threshold,
        "threshold_rule": "min_distance > 2*k",
        "seconds_per_bit": SECONDS_PER_BIT,
        "light_count": m,
        "pair_count": len(pairs),
        "solvable": solvable,
        "shortest_window": shortest,
        "shortest_seconds": (shortest * SECONDS_PER_BIT if shortest is not None else None),
        "bottleneck": [int(x) for x in bottleneck.tolist()],
        "pairs": pairs,
        "exhaustive": True,
        "exhaustive_note": (
            "对每对灯穷举 La*Lb 个独立起始相位组合（按 gcd 等价类无遗漏枚举），"
            "不抽样、不略过任何相位"
        ),
    }


def invert(snapshot: list[dict[str, str]], observation: str, e: int, w: int) -> dict[str, Any]:
    """观测反演：穷举快照内每盏灯的全部循环相位，按前缀筛除候选。

    参数
    ----
    snapshot: 冻结快照 [{"name": ..., "pattern": ...}, ...]。
    observation: 实际观测 0/1 串，长度 n（1 ≤ n ≤ W）。
    e: 误判位数上限（0~3）；保留汉明距离 ≤ e 的（灯, 相位）。
    w: 快照的观测窗口上限 W（仅随结果记录）。

    候选更新规则
    ------------
    相位定义与 ``analyze`` 一致：相位 φ 下第 t 位理想值为
    ``pattern[(t + φ) mod L]``。逐位读入观测串（按前缀更新）：
    某相位在前 t 位的累计汉明距离一旦 > e，该相位立即出局，且因距离
    只增不减不可能复活。穷举规模为 sum(Light 串长) 个相位 × n 位，
    全部枚举，不抽样。

    输出三种结局
    ------------
    - unique：恰剩一个（灯, 相位），给灯名、相位与纠正后的理想序列；
    - ambiguous：多个候选，给每灯候选数、一条见证与“最早无法排除的位置”；
    - none：无候选，说明原因并给全相位穷举下的最近匹配。
    """
    m = len(snapshot)
    n = len(observation)
    if not (MIN_K <= e <= MAX_K):
        raise ValueError("e 必须在 0~3 之间")
    if not (1 <= n <= MAX_W):
        raise ValueError(f"观测串长度须为 1~{MAX_W} 位")
    if n > w:
        raise ValueError(f"观测串长度 {n} 超过快照的观测窗口上限 W={w}")
    if any(c not in "01" for c in observation):
        raise ValueError("观测串只能由字符 0、1 组成")

    obs = np.frombuffer(observation.encode("ascii"), dtype=np.uint8) - ord("0")

    total_phases = 0
    prefix_survivors = np.zeros(n, dtype=np.int64)  # 前 t=1..n 位后全表存活数
    lights_out: list[dict[str, Any]] = []

    for idx, item in enumerate(snapshot):
        name, pattern = item["name"], item["pattern"]
        pa = np.frombuffer(pattern.encode("ascii"), dtype=np.uint8) - ord("0")
        length = int(pa.size)

        # ideal_mat[phi, t] = 相位 phi 下第 t 位理想值；phi 取遍 0..L-1
        ph = np.arange(length, dtype=np.int64)[:, None]
        tt = np.arange(n, dtype=np.int64)[None, :]
        ideal_mat = pa[(tt + ph) % length]                # (L, n)
        mismatch = (ideal_mat != obs[None, :]).astype(np.int8)
        dist = np.cumsum(mismatch, axis=1, dtype=np.int32)  # (L, n) 前缀距离
        alive = dist <= e                                # 等价于逐位前缀筛选
        prefix_survivors += alive.sum(axis=0)
        total_phases += length

        candidates: list[dict[str, Any]] = []
        for phi in np.nonzero(alive[:, -1])[0].tolist():
            candidates.append(
                {
                    "phase": int(phi),
                    "distance": int(dist[phi, -1]),
                    "error_positions": [int(x) for x in np.nonzero(mismatch[phi])[0].tolist()],
                    "ideal": "".join("01"[int(x)] for x in ideal_mat[phi].tolist()),
                }
            )
        # 每灯一条见证：存活相位中距离最小、相位最小者
        witness = min(candidates, key=lambda c: (c["distance"], c["phase"]), default=None)
        # 该灯全部相位中的最近匹配（无候选结局时用于说明原因）
        d_end = dist[:, -1]
        bp = int(d_end.argmin())
        nearest = {
            "phase": bp,
            "distance": int(d_end[bp]),
            "error_positions": [int(x) for x in np.nonzero(mismatch[bp])[0].tolist()],
            "ideal": "".join("01"[int(x)] for x in ideal_mat[bp].tolist()),
        }
        lights_out.append(
            {
                "light_index": idx,
                "name": name,
                "pattern": pattern,
                "pattern_len": length,
                "total_phases": length,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "witness": witness,
                "nearest": nearest,
            }
        )

    # 前缀候选数序列：[初始总数, 第1位后, ..., 第n位后]
    counts = [int(x) for x in prefix_survivors.tolist()]
    seq = [total_phases, *counts]
    eliminated_at = [p for p in range(1, n + 1) if seq[p] < seq[p - 1]]
    candidate_count = counts[-1]

    if candidate_count == 1:
        outcome = "unique"
    elif candidate_count > 1:
        outcome = "ambiguous"
    else:
        outcome = "none"

    # 最早无法排除的位置：最后一次成功排除候选之后的第一个位（从该位起，
    # 读完整个观测串都不会再排除任何候选）。若观测串每一位都成功排除过、
    # 却仍未唯一，则为 n+1（观测串之外），表示需继续观测。
    # 注意：不简单取“第一个无排除的位”——中途可能暂时无排除、之后又恢复排除。
    # 仅 ambiguous 结局有意义。
    undecidable_position: int | None = None
    undecidable_beyond: bool = False
    if outcome == "ambiguous":
        last_drop = eliminated_at[-1] if eliminated_at else 0
        if last_drop < n:
            undecidable_position = last_drop + 1
            undecidable_beyond = False
        else:
            undecidable_position = n + 1
            undecidable_beyond = True

    unique: dict[str, Any] | None = None
    nearest_overall: dict[str, Any] | None = None
    reason: str | None = None

    if outcome == "unique":
        owner = next(lt for lt in lights_out if lt["candidate_count"] == 1)
        only = owner["candidates"][0]
        unique = {
            "light_index": owner["light_index"],
            "name": owner["name"],
            "pattern": owner["pattern"],
            "pattern_len": owner["pattern_len"],
            **only,
        }
    elif outcome == "none":
        owner = min(lights_out, key=lambda lt: (lt["nearest"]["distance"], lt["light_index"]))
        nr = owner["nearest"]
        nearest_overall = {
            "light_index": owner["light_index"],
            "name": owner["name"],
            "pattern": owner["pattern"],
            "pattern_len": owner["pattern_len"],
            **nr,
        }
        reason = (
            f"快照中全部 {m} 盏灯、共 {total_phases} 个循环相位均已穷举："
            f"与 {n} 位观测串的汉明距离全部大于误判上限 e={e}；"
            f"最近匹配为灯「{owner['name']}」相位 {nr['phase']}，"
            f"距离 {nr['distance']}（超出允许误判 {nr['distance'] - e} 位）。"
        )

    return {
        "w": w,
        "n": n,
        "n_seconds": round(n * SECONDS_PER_BIT, 4),
        "e": e,
        "observation": observation,
        "seconds_per_bit": SECONDS_PER_BIT,
        "light_count": m,
        "total_phases": total_phases,
        "candidate_count": candidate_count,
        "prefix_counts": counts,
        "eliminated_at": eliminated_at,
        "undecidable_position": undecidable_position,
        "undecidable_beyond": undecidable_beyond,
        "outcome": outcome,
        "unique": unique,
        "lights": lights_out,
        "nearest": nearest_overall,
        "reason": reason,
        "exhaustive": True,
        "exhaustive_note": (
            "穷举快照内每盏灯的全部循环相位（合计 sum(串长) 个），"
            "按前缀累计汉明距离 ≤ e 逐步筛除，不抽样、不略过任何相位"
        ),
    }
