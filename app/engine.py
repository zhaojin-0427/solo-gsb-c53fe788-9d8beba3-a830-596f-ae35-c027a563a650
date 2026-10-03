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

import itertools
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


# ============================ 灯串修订建议搜索 ============================

def _pair_min_distance_at_w(pa: np.ndarray, pb: np.ndarray, w: int) -> tuple[int, int, int]:
    """穷举两灯全部独立相位，返回 (W 位最小汉明距离, 最近相位 i, 最近相位 j)。

    与 ``analyze`` 的 gcd 等价类枚举完全等价（无抽样）：相位组合按
    (i-j) mod g 分 g 类，每类 L=lcm(La,Lb) 个成员，g*L=La*Lb 覆盖全部
    La*Lb 个独立相位；修订搜索只需要 W 位这一个窗口长度的最小距离。
    """
    la, lb = int(pa.size), int(pb.size)
    g = math.gcd(la, lb)
    period = la // g * lb
    qvec = np.arange(g, dtype=np.int64)[:, None]
    uu = np.arange(period, dtype=np.int64)[None, :]
    i_idx = uu % la
    j_idx = (uu - qvec) % lb
    mm_all = (pa[i_idx] != pb[j_idx]).astype(np.int16)        # (g,L) 一个完整周期
    s = np.arange(period, dtype=np.int64)
    t = np.arange(w, dtype=np.int64)
    # rot_idx 显式列出 t=0..W-1 的全部周期位置（W 大于周期时含整周重复），
    # 故直接对 W 维求和即为 W 位汉明距离，无需另加整周期项
    rot_idx = (t[None, :] + s[:, None]) % period              # (L,W)
    mismatch = mm_all[:, rot_idx]                             # (g,L,W)
    dist = mismatch.sum(axis=2)                               # (g,L)
    flat = dist.reshape(g * period)
    ph = int(flat.argmin())
    q0, s0 = divmod(ph, period)
    i0 = int(s0 % la)
    j0 = int((s0 - q0) % lb)
    return int(flat[ph]), i0, j0


def _pair_witness(pa: np.ndarray, pb: np.ndarray, w: int) -> dict[str, Any]:
    """W 位下最近相位组合的逐位见证（最紧灯对展示用）。"""
    la, lb = int(pa.size), int(pb.size)
    d, i0, j0 = _pair_min_distance_at_w(pa, pb, w)
    mm = np.array(
        [pa[(tt + i0) % la] != pb[(tt + j0) % lb] for tt in range(w)],
        dtype=np.int8,
    )
    diff_positions = [int(p) for p in np.nonzero(mm)[0].tolist()]
    bits_a = "".join(str(int(pa[(tt + i0) % la])) for tt in range(w))
    bits_b = "".join(str(int(pb[(tt + j0) % lb])) for tt in range(w))
    mark = "".join("^" if tt in set(diff_positions) else " " for tt in range(w))
    return {
        "phase_a": i0,
        "phase_b": j0,
        "distance": d,
        "diff_positions": diff_positions,
        "diff_count": len(diff_positions),
        "bits_a": bits_a,
        "bits_b": bits_b,
        "diff_mark": mark,
    }


def revise(
    snapshot: list[dict[str, str]],
    w: int,
    k: int,
    light_index: int,
    max_flips: int,
) -> dict[str, Any]:
    """灯串修订建议搜索。

    在不可辨冻结快照上，只允许翻转指定一盏灯的灯串 1~``max_flips`` 位
    （翻转 = 0↔1），搜索使全部灯对在 W 位、穷举全部独立相位后的最小
    汉明距离均严格大于 2k 的修订方案。

    搜索与裁决
    ----------
    - 候选按翻转数升序（1 位、再 2 位）穷举；同翻转数内按“翻转位置从 0
      起的位置序列字典序”升序（2 位即 (p1,p2) 的字典序）。
    - 裁决键：(翻转数最少, 可行候选 W 位全局最小距离最大, 翻转位置序列
      字典序最小)。因此**不是**返回首个可行方案，而是穷举完同一翻转数
      的全部候选后取最优；1 位存在可行解时不再搜索 2 位。
    - 判定覆盖全部灯对：未改灯对（两盏灯都不是被修订灯）的 W 位最小
      距离只算一次并复用；被修订灯与其余每盏灯的灯对逐候选重算（被改
      灯串不同，无法复用），任一灯对距离 ≤ 2k 即对该候选精确剪枝出局。
    - 翻转后灯串仍须同时含 0、1（与录入约束一致）。

    返回
    ----
    见 README「灯串修订建议」。无解时 ``found=False``，不附带方案。
    """
    m = len(snapshot)
    if not (MIN_LIGHTS <= m <= MAX_LIGHTS):
        raise ValueError("灯的数量必须在 2~20 之间")
    if not (MIN_W <= w <= MAX_W):
        raise ValueError("W 必须在 1~400 之间")
    if not (MIN_K <= k <= MAX_K):
        raise ValueError("k 必须在 0~3 之间")
    if not (0 <= light_index < m):
        raise ValueError("指定的灯序号超出快照范围")
    if max_flips not in (1, 2):
        raise ValueError("最多翻转位数只能为 1 或 2")

    patterns = [item["pattern"] for item in snapshot]
    for idx, p in enumerate(patterns):
        if not (MIN_PATTERN_LEN <= len(p) <= MAX_PATTERN_LEN) or any(c not in "01" for c in p):
            raise ValueError(f"灯 {idx + 1} 的灯串非法")
    arrs = [np.frombuffer(p.encode("ascii"), dtype=np.uint8) - ord("0") for p in patterns]

    threshold = 2 * k
    target = light_index
    original = patterns[target]
    L = len(original)

    # 未改灯对判定（复用）：两盏灯都不是被修订灯的灯对，W 位最小距离
    # 与候选无关，只穷举一次。原快照不可辨时其中必有 ≤ 2k 者——那种情况
    # 只改一盏灯无法消除，搜索将必定无解（仍完整走完搜索逻辑以给出统计）。
    unchanged_floor = np.iinfo(np.int64).max
    unchanged_pairs: list[dict[str, Any]] = []
    for a in range(m):
        if a == target:
            continue
        for b in range(a + 1, m):
            if b == target:
                continue
            d, ia, ib = _pair_min_distance_at_w(arrs[a], arrs[b], w)
            unchanged_floor = min(unchanged_floor, d)
            unchanged_pairs.append({"a": a, "b": b, "distance": d, "phase_a": ia, "phase_b": ib})

    other_idx = [i for i in range(m) if i != target]

    def evaluate(cand_bits: np.ndarray) -> tuple[bool, int, dict[str, Any] | None]:
        """评估一个候选灯串：返回 (是否可行, 全局最小距离, 最紧灯对见证)。

        逐对计算被修订灯与其余灯的 W 位最小距离；任一灯对 ≤ 2k 立即判
        不可行（精确剪枝，不遗漏——距离只取决于该灯对，与其他灯对无关）。
        """
        global_min = int(unchanged_floor)
        if global_min <= threshold:
            return False, global_min, None
        tightest: dict[str, Any] | None = None
        tight_d = global_min
        # 未改灯对中的最紧者，作为见证初值（取距离恰为全局最小的第一对）
        for up in unchanged_pairs:
            if tightest is None or up["distance"] < tightest["distance"]:
                tightest = {
                    "a": up["a"], "b": up["b"],
                    "distance": up["distance"],
                    "phase_a": up["phase_a"], "phase_b": up["phase_b"],
                }
        for j in other_idx:
            d, pj, pi = _pair_min_distance_at_w(cand_bits, arrs[j], w)
            # 相位顺序按 (被修订灯相位, 另一灯相位) 归一化记录
            if target < j:
                wit = {"a": target, "b": j, "distance": d, "phase_a": pj, "phase_b": pi}
            else:
                wit = {"a": j, "b": target, "distance": d, "phase_a": pi, "phase_b": pj}
            if d < tight_d:
                tight_d = d
                tightest = wit
            if d <= threshold:
                return False, min(global_min, d), None
            global_min = min(global_min, d)
        return True, global_min, tightest

    def full_witness(tight: dict[str, Any]) -> dict[str, Any]:
        a, b = tight["a"], tight["b"]
        pa = cand_cache[a]
        pb = cand_cache[b]
        wn = _pair_witness(pa, pb, w)
        return {
            "a": a,
            "b": b,
            "name_a": snapshot[a]["name"],
            "name_b": snapshot[b]["name"],
            **wn,
        }

    # 候选灯串缓存（供最紧灯对见证重放）：评估时被修订灯用候选，其余用快照
    cand_cache: dict[int, np.ndarray] = {i: arrs[i] for i in range(m)}

    best: dict[str, Any] | None = None
    best_key: tuple[int, int, tuple[int, ...]] | None = None
    evaluated = 0
    feasible_count = 0

    # 翻转集合按翻转数、位置序列字典序生成（itertools.combinations 天然
    # 按位置升序元组的字典序产出）。
    for nflips in range(1, max_flips + 1):
        if best is not None:
            break  # 更少翻转数已有可行解，不再搜索更多翻转
        for positions in itertools.combinations(range(L), nflips):
            cand = arrs[target].copy()
            for p in positions:
                cand[p] ^= 1
            # 修订后灯串仍须同时含 0、1
            if not (cand.any() and (~cand.astype(bool)).any()):
                continue
            evaluated += 1
            cand_cache[target] = cand
            feasible, gmin, tight = evaluate(cand)
            if not feasible:
                continue
            feasible_count += 1
            key = (nflips, -gmin, positions)
            # 裁决：翻转数最少（外层已保证）→ 全局最小距离最大 →
            # 翻转位置序列字典序最小
            if best_key is None or key < best_key:
                best_key = key
                witness = full_witness(tight)
                new_pattern = "".join("01"[int(x)] for x in cand.tolist())
                best = {
                    "light_index": target,
                    "light_name": snapshot[target]["name"],
                    "nflips": nflips,
                    "flip_positions": list(positions),          # 0 基
                    "flip_positions_1based": [p + 1 for p in positions],
                    "pattern_before": original,
                    "pattern_after": new_pattern,
                    "global_min_distance": gmin,
                    "tightest_pair": witness,
                }

    unchanged_bad = [
        {"a": up["a"], "b": up["b"], "name_a": snapshot[up["a"]]["name"],
         "name_b": snapshot[up["b"]]["name"], "distance": up["distance"]}
        for up in unchanged_pairs if up["distance"] <= threshold
    ]

    if best is None:
        reason = (
            f"在快照 W={w}、k={k}（阈值 2k={threshold}）下，只翻转灯"
            f"「{snapshot[target]['name']}」至多 {max_flips} 位"
            "（穷举全部 1" + ("、2" if max_flips == 2 else "") +
            " 位翻转组合，且修订后灯串仍同时含 0、1）后，"
            "不存在使全部灯对 W 位最小汉明距离均 > 2k 的方案。"
        )
        if unchanged_bad:
            names = "、".join(
                f"{x['name_a']}↔{x['name_b']}（{x['distance']}）" for x in unchanged_bad
            )
            reason += f" 其中未被修订的灯对 {names} 的 W 位最小距离本就 ≤ 2k，只改一盏灯无法消除。"
        return {
            "found": False,
            "w": w,
            "k": k,
            "threshold": threshold,
            "light_index": target,
            "light_name": snapshot[target]["name"],
            "max_flips": max_flips,
            "pattern_before": original,
            "candidates_evaluated": evaluated,
            "feasible_count": 0,
            "unchanged_blocking_pairs": unchanged_bad,
            "reason": reason,
            "exhaustive": True,
        }

    return {
        "found": True,
        "w": w,
        "k": k,
        "threshold": threshold,
        "light_index": target,
        "light_name": snapshot[target]["name"],
        "max_flips": max_flips,
        "candidates_evaluated": evaluated,
        "feasible_count": feasible_count,
        "unchanged_blocking_pairs": unchanged_bad,
        "revision": best,
        "selection_rule": (
            "翻转数最少 → 可行方案 W 位全局最小汉明距离最大 → "
            "翻转位置序列（从 0 起）字典序最小；穷举同翻转数全部候选后裁决，"
            "非首个可行方案"
        ),
        "exhaustive": True,
        "exhaustive_note": (
            "每个候选均穷举全部灯对的全部独立起始相位（La*Lb 个）求 W 位"
            "最小汉明距离；未改灯对判定只计算一次并复用，逐对计算中一旦"
            "出现 ≤ 2k 立即精确剪枝"
        ),
    }
