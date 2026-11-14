#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ModelTrace 归因测谎中枢全量自动化回归测试
ModelTrace Attribution & Anti-Spoofing Regression Test Suite
"""

import sys
import os
import json
import time
from pathlib import Path

# 确保 UTF-8 编码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CORE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = CORE_DIR.parent.parent
ENGINE_DIR = CORE_DIR / "engine"
CONTROLLER_DIR = CORE_DIR / "controller"
DATA_DIR = CORE_DIR / "data"
BANK_FILE = DATA_DIR / "unified_bank.json"
CLAUDE_REF = DATA_DIR / "claude_reference.jsonl"
GPT_REF = DATA_DIR / "gpt_reference.jsonl"

sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(CONTROLLER_DIR))

from fingerprint import load_bank, analyze_global_outputs, parse_numbers, count_numbers
from spoof_detector import SpoofDetector
from collector import ConcurrentCollector

def test_1_bank_integrity():
    print("[*] 1. 校验 ModelTrace 官方基准库完备性与校准参数...")
    assert BANK_FILE.exists(), f"基准库文件缺失: {BANK_FILE}"
    bank = load_bank(BANK_FILE)
    models = bank.get("models", [])
    assert len(models) == 18, f"期望 18 款官方基准模型，实际仅 {len(models)} 款"

    families = {m.get("family") for m in models}
    assert "gpt" in families and "claude" in families, f"未正确解析到 gpt 与 claude 家族: {families}"

    calib = bank.get("calibration", {})
    assert "1" in calib and "3" in calib, "校准参数缺少 1 轮与 3 轮 beta 设置"

    total_numbers = sum(m.get("valid_number_count", 0) for m in models)
    print(f"[✓] 基准库验证通过: 共收录 18 款模型，2 大核心家族，总计 {total_numbers:,} 个有效特征数字")

def test_2_anti_spoofing():
    print("\n[*] 2. 校验 Task 2.3: 抗对抗程序伪造与反代掺水检测器 (SpoofDetector)...")

    # 2.1 真实样本 0 误报校验 (0 False Positives)
    real_samples = []
    if CLAUDE_REF.exists():
        with open(CLAUDE_REF, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    real_samples.append(json.loads(line))
    if GPT_REF.exists():
        with open(GPT_REF, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    real_samples.append(json.loads(line))

    assert len(real_samples) >= 600, f"真实参考样本量不足: {len(real_samples)}"

    false_positives = 0
    for sample in real_samples:
        nums = parse_numbers(sample["text"])
        res = SpoofDetector.analyze_sequence(nums)
        if res["is_spoofed"]:
            false_positives += 1

    fp_rate = (false_positives / len(real_samples)) * 100
    print(f"[✓] 真实历史大模型样本核验 ({len(real_samples)} 份): 误报数={false_positives} (误报率: {fp_rate:.2f}%)")
    assert false_positives == 0, f"真实大模型样本出现误报: {false_positives} 处"

    # 2.2 伪造对抗样本 100% 拦截校验
    # Vector 1: 单调等差数列 (1, 2, 3...)
    spoof_monotonic = list(range(1, 251))
    r1 = SpoofDetector.analyze_sequence(spoof_monotonic)
    assert r1["is_spoofed"] and "MONOTONIC_SEQUENCE_SPOOF" in r1["flags"], "未能拦截单调等差伪造"

    # Vector 2: 算术步长递减 (350, 349, 348...)
    spoof_dec = list(range(350, 100, -1))
    r2 = SpoofDetector.analyze_sequence(spoof_dec)
    assert r2["is_spoofed"], "未能拦截递减步长伪造"

    # Vector 3: 周期循环重放 (如 [12, 85, 230, 44] 循环 60 次)
    spoof_cycle = [12, 85, 230, 44] * 60
    r3 = SpoofDetector.analyze_sequence(spoof_cycle)
    assert r3["is_spoofed"] and any("PERIODIC_CYCLE" in f for f in r3["flags"]), "未能拦截周期循环伪造"

    # Vector 4: 固定步长同余步进 (Stride Progression: 每次加 7 模 355)
    spoof_stride = [(10 + i * 7) % 355 + 1 for i in range(250)]
    r4 = SpoofDetector.analyze_sequence(spoof_stride)
    assert r4["is_spoofed"] and any("CONSTANT_STRIDE" in f for f in r4["flags"]), "未能拦截固定步长伪造"

    # Vector 5: 极端退化占位 (仅 3 个数字轮换)
    spoof_degen = [10, 20, 30] * 80
    r5 = SpoofDetector.analyze_sequence(spoof_degen)
    assert r5["is_spoofed"] and "DEGENERATE_LOW_DIVERSITY" in r5["flags"], "未能拦截退化占位伪造"

    print("[✓] 5 类程序对抗伪造攻击向量拦截率: 100% (等差、递减、周期、同余步进、退化占位)")

def test_3_concurrent_collector():
    print("\n[*] 3. 校验 Task 2.2: 高并发异步多轮采样与快速探测引擎 (ConcurrentCollector)...")

    # 构造模拟 LLM 响应器 (模拟每个并发请求 0.05 秒延迟返回有效随机数)
    sample_claude = json.loads(open(CLAUDE_REF, encoding="utf-8").readline())["text"]

    def mock_llm_responder(prompt: str) -> str:
        time.sleep(0.05)
        return sample_claude

    collector = ConcurrentCollector(
        base_url="https://mock.openai.com/v1",
        api_key="sk-mock-test",
        model="claude-haiku-4-5-20251001",
        timeout=10.0
    )

    t0 = time.perf_counter()
    res = collector.collect_parallel(count=3, mock_fn=mock_llm_responder)
    elapsed = time.perf_counter() - t0

    assert res["success"], f"并发采样失败: {res.get('errors')}"
    assert res["collected_count"] == 3, f"期望采集 3 组，实际 {res['collected_count']}"
    assert elapsed < 0.3, f"并发执行耗时过长: {elapsed:.2f}s (未实现真并发调度)"
    print(f"[✓] 并发多轮采样调度成功: 3 组并发采样总耗时 {elapsed:.3f}s (单次平均延时 {res['avg_latency_sec']}s)")

    # 探测与综合归因全链路
    bank = load_bank(BANK_FILE)
    probe_res = collector.probe_and_analyze(bank, count=3, mock_fn=mock_llm_responder)
    assert probe_res["success"], "探测分析全链路失败"
    assert not probe_res["spoof_detected"], "真实模型样本被误报为伪造"
    assert probe_res["familyPrediction"] == "Claude", f"预测家族错误: {probe_res['familyPrediction']}"
    print(f"[✓] 一站式并发探测与测谎归因成功: 预测家族={probe_res['familyPrediction']}, 置信度={probe_res['familyProbability']}%")

def test_4_attribution_precision():
    print("\n[*] 4. 校验 Task 2.1: 18 款模型特征指纹与路由归因精度...")
    bank = load_bank(BANK_FILE)

    # 4.1 Claude 样本推断
    c_line = open(CLAUDE_REF, encoding="utf-8").readline()
    c_text = json.loads(c_line)["text"]
    c_items = [{"text": c_text, "expected_count": 250} for _ in range(3)]
    res_c = analyze_global_outputs(c_items, bank)
    assert res_c["family_prediction_name"] == "Claude", f"Claude 样本误判为: {res_c['family_prediction_name']}"
    assert res_c["family_probability"] > 0.90, f"Claude 置信度过低: {res_c['family_probability']}"
    print(f"[✓] Claude 样本归因通过: 预测={res_c['prediction']}, 家族={res_c['family_prediction_name']} ({res_c['family_probability']*100:.2f}%)")

    # 4.2 GPT 样本推断
    g_line = open(GPT_REF, encoding="utf-8").readline()
    g_text = json.loads(g_line)["text"]
    g_items = [{"text": g_text, "expected_count": 250} for _ in range(3)]
    res_g = analyze_global_outputs(g_items, bank)
    assert res_g["family_prediction_name"] == "GPT", f"GPT 样本误判为: {res_g['family_prediction_name']}"
    assert res_g["family_probability"] > 0.90, f"GPT 置信度过低: {res_g['family_probability']}"
    print(f"[✓] GPT 样本归因通过: 预测={res_g['prediction']}, 家族={res_g['family_prediction_name']} ({res_g['family_probability']*100:.2f}%)")

def test_5_controller_api():
    print("\n[*] 5. 校验 Flask 微服务 Controller API 路由...")
    from flask import Flask
    from modeltrace_controller import init_modeltrace_module

    app = Flask(__name__)
    init_modeltrace_module(app, MT_CORE_DIR=CORE_DIR, DATA_DIR=DATA_DIR)
    client = app.test_client()

    # 5.1 /api/modeltrace/info
    resp_info = client.get("/api/modeltrace/info")
    assert resp_info.status_code == 200
    data_info = resp_info.get_json()
    assert data_info["success"] and data_info["modelsCount"] == 18

    # 5.2 /api/modeltrace/models
    resp_models = client.get("/api/modeltrace/models")
    assert resp_models.status_code == 200
    assert resp_models.get_json()["count"] == 18

    # 5.3 /api/modeltrace/spoof_check
    resp_spoof = client.post("/api/modeltrace/spoof_check", json={
        "numbers": list(range(1, 200))
    })
    assert resp_spoof.status_code == 200
    assert resp_spoof.get_json()["spoofReport"]["is_spoofed"] is True

    # 5.4 /api/modeltrace/analyze (含伪造告警联动)
    resp_ana_spoof = client.post("/api/modeltrace/analyze", json={
        "outputs": [" ".join(str(i) for i in range(1, 200))]
    })
    assert resp_ana_spoof.status_code == 200
    ana_data = resp_ana_spoof.get_json()
    assert ana_data["spoofDetected"] is True
    assert "[伪造拦截]" in ana_data["verdict"]

    # 5.5 /api/modeltrace/analyze (自然 Claude 样本)
    c_text = json.loads(open(CLAUDE_REF, encoding="utf-8").readline())["text"]
    resp_ana_nat = client.post("/api/modeltrace/analyze", json={
        "outputs": [c_text]
    })
    assert resp_ana_nat.status_code == 200
    nat_data = resp_ana_nat.get_json()
    assert nat_data["spoofDetected"] is False
    assert nat_data["familyPrediction"] == "Claude"

    print("[✓] Controller API 端点校验全部通过 (info, models, spoof_check, analyze)")

def test_6_cli_integration():
    print("\n[*] 6. 校验 ModelTrace CLI 命令行工具...")
    import subprocess
    py_bin = sys.executable

    # 6.1 bank-info --json
    p1 = subprocess.run([py_bin, "-X", "utf8", str(CORE_DIR / "cli.py"), "bank-info", "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert p1.returncode == 0, f"cli bank-info 失败: {p1.stderr}"
    d1 = json.loads(p1.stdout)
    assert d1["models_count"] == 18

    # 6.2 check-spoof --json
    p2 = subprocess.run([py_bin, "-X", "utf8", str(CORE_DIR / "cli.py"), "check-spoof", "--input", " ".join(str(i) for i in range(1, 200)), "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert p2.returncode == 0
    d2 = json.loads(p2.stdout)
    assert d2["is_spoofed"] is True

    # 6.3 analyze --json
    c_text = json.loads(open(CLAUDE_REF, encoding="utf-8").readline())["text"]
    p3 = subprocess.run([py_bin, "-X", "utf8", str(CORE_DIR / "cli.py"), "analyze", "--input", c_text, "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert p3.returncode == 0
    d3 = json.loads(p3.stdout)
    assert d3["family_prediction"] == "Claude"

    print("[✓] CLI 命令行全量集成通过 (bank-info, check-spoof, analyze)")

def main():
    print("========================================================================")
    print("  ModelTrace 模型路由归因与特征测谎升级全量回归测试")
    print("========================================================================")
    t_start = time.perf_counter()

    test_1_bank_integrity()
    test_2_anti_spoofing()
    test_3_concurrent_collector()
    test_4_attribution_precision()
    test_5_controller_api()
    test_6_cli_integration()

    elapsed = time.perf_counter() - t_start
    print("\n========================================================================")
    print(f"  [ALL TESTS PASSED] 模块二：模型路由归因与特征测谎系统全量绿灯通过！({elapsed:.2f}s)")
    print("========================================================================")

if __name__ == "__main__":
    main()
