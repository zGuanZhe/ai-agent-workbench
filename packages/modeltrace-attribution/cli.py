#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ModelTrace 命令行工具 (ModelTrace Attribution CLI)
提供模型基准信息核查、抗伪造检测、离线特征归因与在线并发探测
"""

from __future__ import annotations
import sys
import os
import json
import argparse
from pathlib import Path

# 保证控制台与编码正常
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CORE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = CORE_DIR / "engine"
DATA_DIR = CORE_DIR / "data"
BANK_FILE = DATA_DIR / "unified_bank.json"

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from fingerprint import load_bank, analyze_global_outputs, parse_numbers, count_numbers
from spoof_detector import SpoofDetector
from collector import ConcurrentCollector

def _load_input_text(input_val: str | None) -> str:
    """从参数、文件或管道标准输入中读取文本"""
    if input_val:
        p = Path(input_val)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
        return input_val
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""

def cmd_bank_info(args):
    if not BANK_FILE.exists():
        print(f"[x] 基准库文件缺失: {BANK_FILE}")
        sys.exit(1)

    bank = load_bank(BANK_FILE)
    models = bank.get("models", [])
    calib = bank.get("calibration", {})

    families = {}
    for m in models:
        fam = m.get("family_name") or m.get("family") or "Other"
        families.setdefault(fam, []).append(m)

    if args.json:
        print(json.dumps({
            "models_count": len(models),
            "families_count": len(families),
            "families": {k: [m["id"] for m in v] for k, v in families.items()},
            "models": [
                {
                    "id": m["id"],
                    "display_name": m.get("display_name"),
                    "family": m.get("family"),
                    "family_name": m.get("family_name"),
                    "response_count": m.get("response_count", 0),
                    "valid_numbers": m.get("valid_number_count", 0)
                }
                for m in models
            ],
            "calibration": calib
        }, ensure_ascii=False, indent=2))
        return

    print("========================================================================")
    print("  ModelTrace 官方基准库 (Unified Benchmark Bank)")
    print("========================================================================")
    print(f"[*] 基准模型总数: {len(models)} 款")
    print(f"[*] 覆盖家族数:   {len(families)} 族 ({', '.join(families.keys())})")
    print(f"[*] 交叉校验校准: Q1 beta={calib.get('1', {}).get('beta')}, Q3 beta={calib.get('3', {}).get('beta')}")
    print("------------------------------------------------------------------------")
    print(f"{'模型标识 (Model ID)':<32} {'家族 (Family)':<16} {'样本数':<8} {'有效数字数'}")
    print("------------------------------------------------------------------------")
    for fam_name, m_list in families.items():
        for m in m_list:
            mid = m.get("id", "")
            resp_cnt = str(m.get("response_count", 0))
            valid_nums = str(m.get("valid_number_count", 0))
            print(f"{mid:<32} {fam_name:<16} {resp_cnt:<8} {valid_nums}")
    print("========================================================================")

def cmd_check_spoof(args):
    text = _load_input_text(args.input)
    if not text.strip():
        print("[x] 错误: 未提供待核验文本或数字序列")
        sys.exit(1)

    numbers = parse_numbers(text)
    report = SpoofDetector.analyze_sequence(numbers)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("========================================================================")
    print("  ModelTrace 防对抗程序伪造与反代掺水检测报告")
    print("========================================================================")
    status_str = "[!] 拦截：检出程序代码伪造特征" if report["is_spoofed"] else "[✓] 通过：符合大模型自然随机分布"
    print(f"[*] 状态结论:   {status_str}")
    print(f"[*] 判定置信度: {round(report['confidence'] * 100, 2)}%")
    print(f"[*] 详细说明:   {report['verdict']}")
    if report.get("flags"):
        print(f"[*] 拦截标记:   {', '.join(report['flags'])}")
    print("------------------------------------------------------------------------")
    m = report.get("metrics", {})
    print(f"  - 提取有效数字总数: {m.get('valid_count', 0)}")
    print(f"  - 独立唯一样本数:   {m.get('unique_count', 0)} (比例: {m.get('unique_ratio', 0)})")
    print(f"  - 相邻单步跳跃比例: {m.get('step_ratio', 0)}")
    print(f"  - 单调递增比率:     {m.get('inc_ratio', 0)}")
    print(f"  - 单调递减比率:     {m.get('dec_ratio', 0)}")
    print("========================================================================")

def cmd_analyze(args):
    text = _load_input_text(args.input)
    if not text.strip():
        print("[x] 错误: 未提供待鉴定的输出文本")
        sys.exit(1)

    if not BANK_FILE.exists():
        print(f"[x] 基准库文件缺失: {BANK_FILE}")
        sys.exit(1)

    bank = load_bank(BANK_FILE)
    numbers = parse_numbers(text)
    spoof_report = SpoofDetector.analyze_sequence(numbers)

    # 构建 3 组评估样本
    items = [{"text": text, "expected_count": 250} for _ in range(3)]
    res = analyze_global_outputs(items, bank)
    all_results = sorted(res.get("results", []), key=lambda x: x.get("probability", 0), reverse=True)

    family_name = res.get("family_prediction_name", "未知")
    family_prob = round(res.get("family_probability", 0) * 100, 2)
    top_model = all_results[0].get("model", "未知") if all_results else "未知"

    if args.json:
        print(json.dumps({
            "spoof_detected": spoof_report["is_spoofed"],
            "spoof_report": spoof_report,
            "prediction": top_model,
            "family_prediction": family_name,
            "family_probability": family_prob,
            "top_models": [
                {
                    "model": m.get("model"),
                    "family": m.get("family_name"),
                    "probability": round(m.get("probability", 0) * 100, 2)
                } for m in all_results[:6]
            ],
            "family_probabilities": [
                {
                    "family": f.get("display_name"),
                    "probability": round(f.get("probability", 0) * 100, 2)
                } for f in res.get("family_probabilities", [])
            ]
        }, ensure_ascii=False, indent=2))
        return

    print("========================================================================")
    print("  ModelTrace 认知归因与真伪测谎报告")
    print("========================================================================")
    if spoof_report["is_spoofed"]:
        print(f"[!] [伪造拦截] 该响应高度疑似程序脚本伪造 (置信度: {round(spoof_report['confidence']*100, 1)}%)")
        print(f"    触发标记: {', '.join(spoof_report.get('flags', []))}")
        print("------------------------------------------------------------------------")

    print(f"[*] 预测所属家族: {family_name} (综合概率: {family_prob}%)")
    print(f"[*] 最吻合基准:   {top_model}")
    print(f"[*] 解析有效数字: {len(numbers)} 个 (要求 >= 80)")
    print("------------------------------------------------------------------------")
    print("家族概率分布:")
    for f in res.get("family_probabilities", []):
        bar = "#" * int(f.get("probability", 0) * 30)
        print(f"  {f.get('display_name'):<20} {f.get('probability', 0)*100:6.2f}% | {bar}")
    print("------------------------------------------------------------------------")
    print("Top 5 相似基准模型:")
    for idx, m in enumerate(all_results[:5], 1):
        print(f"  #{idx} {m.get('model'):<30} [{m.get('family_name')}] {m.get('probability', 0)*100:6.2f}%")
    print("========================================================================")

def cmd_test_api(args):
    if not args.base_url or not args.model:
        print("[x] 错误: 请提供 --base-url 与 --model")
        sys.exit(1)

    if not BANK_FILE.exists():
        print(f"[x] 基准库文件缺失: {BANK_FILE}")
        sys.exit(1)

    bank = load_bank(BANK_FILE)
    collector = ConcurrentCollector(
        base_url=args.base_url,
        api_key=args.api_key or "",
        model=args.model,
        timeout=args.timeout,
        api_format=args.format
    )

    if not args.json:
        print("========================================================================")
        print("  ModelTrace 在线并发多轮采样探测")
        print("========================================================================")
        print(f"[*] 目标端点: {args.base_url}")
        print(f"[*] 宣称模型: {args.model}")
        print(f"[*] 并发轮次: {args.count} 轮 (超时: {args.timeout}s)")
        print("[*] 正在调度并发请求...")

    res = collector.probe_and_analyze(bank, count=args.count, temperature=args.temperature)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if not res.get("success"):
        print(f"[x] 探测失败: {res.get('error')}")
        sys.exit(1)

    print("------------------------------------------------------------------------")
    print(f"[✓] 并发探测完成 (总耗时: {res.get('duration_sec')}s, 平均单请求耗时: {res.get('avg_latency_sec')}s)")
    if res.get("spoof_detected"):
        print(f"[!] [伪造拦截] 检出中间代理由代码/脚本伪造数字 (置信度: {res['spoof_report']['confidence']*100:.1f}%)")
    else:
        print("[✓] 防伪审查通过: 响应符合真实大模型自回归随机特征")
    print(f"[*] 归因结论:     {res.get('verdict')}")
    print(f"[*] 真实匹配家族: {res.get('familyPrediction')} ({res.get('familyProbability')}%)")
    print(f"[*] 最贴近基准:   {res.get('topMatch')}")
    print("------------------------------------------------------------------------")
    print("Top 5 相似基准模型:")
    for idx, m in enumerate(res.get("topModels", [])[:5], 1):
        print(f"  #{idx} {m.get('model'):<30} [{m.get('family')}] {m.get('probability'):6.2f}%")
    print("========================================================================")

def main():
    parser = argparse.ArgumentParser(description="ModelTrace Attribution & Anti-Spoofing CLI")
    subparsers = parser.add_subparsers(dest="command")

    # bank-info
    p_info = subparsers.add_parser("bank-info", help="查看已收录的模型基准库与家族统计")
    p_info.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # check-spoof
    p_spoof = subparsers.add_parser("check-spoof", help="对输入文本或数字序列执行防对抗伪造核验")
    p_spoof.add_argument("--input", "-i", type=str, help="待检测的文本内容或文件路径 (支持管道输入)")
    p_spoof.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="执行离线大模型特征指纹与路由归因分析")
    p_analyze.add_argument("--input", "-i", type=str, help="待鉴定的模型输出文本或文件路径 (支持管道输入)")
    p_analyze.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # test-api
    p_test = subparsers.add_parser("test-api", help="对指定 API 端点执行高并发多轮采样探测与测谎归因")
    p_test.add_argument("--base-url", required=True, help="接口 Base URL (如 https://api.openai.com/v1)")
    p_test.add_argument("--api-key", default="", help="API Key (可选)")
    p_test.add_argument("--model", required=True, help="宣称的模型名称 (如 gpt-4o 或 claude-3-5-sonnet)")
    p_test.add_argument("--count", type=int, default=3, help="并发采样轮数 (默认 3)")
    p_test.add_argument("--timeout", type=float, default=25.0, help="单次请求超时秒数 (默认 25)")
    p_test.add_argument("--format", default="auto", choices=["auto", "openai", "anthropic"], help="API 协议格式")
    p_test.add_argument("--temperature", type=float, default=None, help="采样温度 (可选)")
    p_test.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "bank-info":
        cmd_bank_info(args)
    elif args.command == "check-spoof":
        cmd_check_spoof(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "test-api":
        cmd_test_api(args)

if __name__ == "__main__":
    main()
