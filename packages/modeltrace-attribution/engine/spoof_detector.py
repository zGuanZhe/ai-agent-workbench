# -*- coding: utf-8 -*-
"""
Anti-Spoofing & Programmatic Generator Detector (防对抗程序伪造与反代掺水检测器)

理论依据:
真实大模型在生成无语义随机数时，由自回归注意力机制与多项式概率采样生成，
其词序跳变具有高度的非线性与局部聚集特征，绝不会产生：
1. 算术等差序列或大段连续递增/递减；
2. 固定周期循环或重复块重放；
3. 退化重复（如仅在极少量数字间反复跳转）；
4. 线性同余发生器 (LCG) 或固定步长步进 (Stride Progression)；
5. 极端低方差的人工平铺分布。
若中间代理用 Python 代码伪造或拦截注入数字流，本模块实施多维特征核验并拦截假冒。
"""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional
import numpy as np

VALUE_MIN = 1
VALUE_MAX = 355
RANGE_SPAN = VALUE_MAX - VALUE_MIN + 1

class SpoofDetector:
    """防对抗与程序伪造检测器"""

    @classmethod
    def analyze_sequence(cls, numbers: List[int]) -> Dict[str, Any]:
        """对单条数字序列执行抗伪造检测"""
        valid_nums = [n for n in numbers if VALUE_MIN <= n <= VALUE_MAX]
        n = len(valid_nums)
        if n < 40:
            return {
                "is_spoofed": False,
                "confidence": 0.0,
                "verdict": "样本量不足，暂无法判定",
                "flags": ["INSUFFICIENT_SAMPLE"],
                "metrics": {"valid_count": n},
            }

        unique_nums = set(valid_nums)
        u_count = len(unique_nums)
        flags = []
        spoof_score = 0.0

        # ── 维度 1: 极端退化分布 (Degenerate Low Diversity) ──
        # 250 个数字中如果唯一样本数不足 20 个，几乎必然是固定轮询或模板占位
        # 250 个数字中如果唯一样本数不足 10 个，必然是固定极简占位伪造
        if n >= 100 and u_count <= 10:
            flags.append("DEGENERATE_LOW_DIVERSITY")
            spoof_score += 0.95

        # ── 维度 2: 单调递增/递减与连续步进 (Monotonic & Step Progression) ──
        diffs = [valid_nums[i + 1] - valid_nums[i] for i in range(n - 1)]
        strictly_increasing = sum(1 for d in diffs if d > 0)
        strictly_decreasing = sum(1 for d in diffs if d < 0)
        step_one = sum(1 for d in diffs if d == 1)
        step_neg_one = sum(1 for d in diffs if d == -1)

        inc_ratio = strictly_increasing / (n - 1)
        dec_ratio = strictly_decreasing / (n - 1)
        step_ratio = max(step_one, step_neg_one) / (n - 1)

        if inc_ratio > 0.85 or dec_ratio > 0.85:
            flags.append("MONOTONIC_SEQUENCE_SPOOF")
            spoof_score += 0.90

        if step_ratio > 0.30:
            flags.append("ARITHMETIC_STEP_SPOOF")
            spoof_score += 0.90

        # ── 维度 3: 固定周期循环检测 (Periodic Loop Spoof) ──
        # 检查是否存在步长为 2~12 的完全或高度匹配循环 (如 [1, 5, 8, 1, 5, 8...])
        for period in range(2, 13):
            if n >= period * 4:
                matches = sum(1 for i in range(n - period) if valid_nums[i] == valid_nums[i + period])
                if matches / (n - period) > 0.70:
                    flags.append(f"PERIODIC_CYCLE_P{period}")
                    spoof_score += 0.90
                    break

        # ── 维度 4: 固定步长同余步进 (Constant Stride / LCG Signature) ──
        # 检查模 355 后的步长绝对值分布是否异常集中在单一非零值
        mod_steps = [(valid_nums[i + 1] - valid_nums[i]) % RANGE_SPAN for i in range(n - 1)]
        from collections import Counter
        step_counts = Counter(mod_steps)
        top_step, top_freq = step_counts.most_common(1)[0]
        if top_freq / (n - 1) > 0.40 and top_step != 0:
            flags.append(f"CONSTANT_STRIDE_SPOOF_S{top_step}")
            spoof_score += 0.85

        # ── 维度 5: 严格相邻差值方差归零 (Zero Diff Variance) ──
        if len(set(diffs)) <= 2 and n >= 50:
            flags.append("RIGID_DIFFERENCE_STRUCTURE")
            spoof_score += 0.90

        is_spoofed = spoof_score >= 0.70
        confidence = min(1.0, round(spoof_score, 4))

        if is_spoofed:
            verdict = f"检测到程序伪造特征 ({', '.join(flags)}): 疑似第三方中转代理拦截并由 Python/脚本代码伪造返回"
        else:
            verdict = "自然大模型采样 (通过防伪造与特征检验)"

        return {
            "is_spoofed": is_spoofed,
            "confidence": confidence,
            "verdict": verdict,
            "flags": flags,
            "metrics": {
                "valid_count": n,
                "unique_count": u_count,
                "unique_ratio": round(u_count / n, 3),
                "step_ratio": round(step_ratio, 3),
                "inc_ratio": round(inc_ratio, 3),
                "dec_ratio": round(dec_ratio, 3),
            },
        }

    @classmethod
    def check_outputs(cls, outputs_numbers: List[List[int]]) -> Dict[str, Any]:
        """批量检查多组采样的综合伪造风险"""
        if not outputs_numbers:
            return {"is_spoofed": False, "confidence": 0.0, "verdict": "无样本", "flags": []}

        individual_reports = [cls.analyze_sequence(seq) for seq in outputs_numbers if seq]
        if not individual_reports:
            return {"is_spoofed": False, "confidence": 0.0, "verdict": "无有效数字", "flags": []}

        spoofed_count = sum(1 for r in individual_reports if r["is_spoofed"])
        max_conf = max(r["confidence"] for r in individual_reports)
        all_flags = list(set(flag for r in individual_reports for flag in r.get("flags", [])))

        is_spoofed = spoofed_count > 0 and max_conf >= 0.70

        if is_spoofed:
            verdict = f"疑似中间代理程序伪造/反代掺水注入 (最高置信度: {round(max_conf * 100, 1)}%)"
        else:
            verdict = "大模型真实血统原生响应 (通过防伪造审查)"

        return {
            "is_spoofed": is_spoofed,
            "confidence": max_conf,
            "spoofed_samples": spoofed_count,
            "total_samples": len(individual_reports),
            "verdict": verdict,
            "flags": all_flags,
            "individual_reports": individual_reports,
        }
