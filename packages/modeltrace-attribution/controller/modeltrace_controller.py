# -*- coding: utf-8 -*-
"""
模型路由归因与特征测谎控制器模块 (ModelTrace Controller)
Model Fingerprinting & Attribution Engine API
提供高并发异步探测、数学测谎归因与防对抗伪造检测三位一体能力
"""

from __future__ import annotations
import sys
import os
import json
import time
from pathlib import Path
from flask import jsonify, request

def init_modeltrace_module(app, MT_CORE_DIR: Path, DATA_DIR: Path = None):
    """
    初始化并挂载模型路由归因 API 路由
    :param app: Flask 实例
    :param MT_CORE_DIR: modeltrace_attribution_core 目录路径
    :param DATA_DIR: 自定义数据目录 (可选)
    """
    ENGINE_DIR = MT_CORE_DIR / "engine"
    BANK_FILE = MT_CORE_DIR / "data" / "unified_bank.json"

    # 注册 engine 至模块搜索路径
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))

    from fingerprint import analyze_global_outputs, load_bank, parse_numbers
    from challenge_suite import CHINESE_TEMPLATES, ENGLISH_TEMPLATES
    from spoof_detector import SpoofDetector
    from collector import ConcurrentCollector

    @app.route("/api/modeltrace/info", methods=["GET"])
    def api_modeltrace_info():
        models = []
        families_map = {}
        if BANK_FILE.exists():
            try:
                bdata = json.loads(BANK_FILE.read_text(encoding="utf-8"))
                models = bdata.get("models", [])
                for m in models:
                    fam = m.get("family_name") or m.get("family") or "Other"
                    families_map.setdefault(fam, []).append(m.get("id"))
            except Exception:
                pass

        challenges_zh = [t.format(count=250) for t in CHINESE_TEMPLATES]
        challenges_en = [t.format(count=250) for t in ENGLISH_TEMPLATES]

        return jsonify({
            "success": True,
            "modelsCount": len(models),
            "familiesCount": len(families_map),
            "families": families_map,
            "models": [
                {
                    "id": m.get("id"),
                    "displayName": m.get("display_name"),
                    "family": m.get("family"),
                    "familyName": m.get("family_name"),
                    "validNumbers": m.get("valid_number_count", 0),
                    "responseCount": m.get("response_count", 0)
                }
                for m in models
            ],
            "challenges": {
                "zh": challenges_zh,
                "en": challenges_en
            }
        })

    @app.route("/api/modeltrace/models", methods=["GET"])
    def api_modeltrace_models():
        if not BANK_FILE.exists():
            return jsonify({"success": False, "error": "基准库文件缺失"}), 404
        try:
            bdata = json.loads(BANK_FILE.read_text(encoding="utf-8"))
            models = bdata.get("models", [])
            return jsonify({
                "success": True,
                "count": len(models),
                "models": models
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/modeltrace/spoof_check", methods=["POST"])
    def api_modeltrace_spoof_check():
        """专门用于核验待测文本或数字序列是否存在程序伪造特征的接口"""
        req = request.get_json() or {}
        raw_numbers = req.get("numbers")
        raw_texts = req.get("texts") or req.get("outputs")
        if not raw_texts and req.get("text"):
            raw_texts = [req.get("text")]

        all_numbers: list[list[int]] = []
        if raw_numbers:
            if isinstance(raw_numbers[0], list):
                all_numbers = raw_numbers
            else:
                all_numbers = [raw_numbers]
        elif raw_texts:
            for t in raw_texts:
                parsed = parse_numbers(str(t))
                if parsed:
                    all_numbers.append(parsed)

        if not all_numbers:
            return jsonify({"success": False, "error": "请提供待核验的文本或数字序列"}), 400

        report = SpoofDetector.check_outputs(all_numbers)
        return jsonify({
            "success": True,
            "spoofReport": report
        })

    @app.route("/api/modeltrace/analyze", methods=["POST"])
    def api_modeltrace_analyze():
        req = request.get_json() or {}
        outputs_text = req.get("outputs", [])
        if not outputs_text and req.get("output_text"):
            outputs_text = [req.get("output_text")]
        if not outputs_text and req.get("text"):
            outputs_text = [req.get("text")]
        if not outputs_text or len(outputs_text) < 1:
            return jsonify({"success": False, "error": "请至少提供 1 组待鉴定的模型输出文本"}), 400

        try:
            bank = load_bank(BANK_FILE)

            # 1. 抗程序对抗伪造检测
            extracted_seqs = [parse_numbers(str(t)) for t in outputs_text]
            valid_seqs = [seq for seq in extracted_seqs if len(seq) >= 10]
            spoof_report = SpoofDetector.check_outputs(valid_seqs) if valid_seqs else {
                "is_spoofed": False, "confidence": 0.0, "verdict": "有效数字不足", "flags": []
            }

            # 2. 补齐至至少 3 组样本用于鲁棒特征加权
            items = [{"text": str(t), "expected_count": 250} for t in outputs_text]
            while len(items) < 3:
                items.append(items[0])

            res = analyze_global_outputs(items, bank)
            all_results = sorted(res.get("results", []), key=lambda x: x.get("probability", 0), reverse=True)

            family_name = res.get("family_prediction_name", "未知")
            family_prob = round(res.get("family_probability", 0) * 100, 2)
            top_model = all_results[0].get("model", "未知") if all_results else "未知"

            # 判定结论融合伪造告警
            if spoof_report.get("is_spoofed"):
                verdict = f"[伪造拦截] 疑似代码伪造/反代掺水注入 (置信度: {round(spoof_report['confidence']*100, 1)}%)"
            else:
                verdict = f"{family_name} / {top_model} ({family_prob}%)"

            prob_dict = {
                m.get("model"): round(m.get("probability", 0), 4)
                for m in all_results[:8]
            }

            return jsonify({
                "success": True,
                "verdict": verdict,
                "spoofDetected": spoof_report.get("is_spoofed", False),
                "spoofReport": spoof_report,
                "familyPrediction": family_name,
                "familyProbability": family_prob,
                "probabilities": prob_dict,
                "topModels": [
                    {
                        "model": m.get("model"),
                        "family": m.get("family_name"),
                        "probability": round(m.get("probability", 0) * 100, 2)
                    } for m in all_results[:6]
                ],
                "familyProbabilities": [
                    {
                        "family": f.get("display_name"),
                        "probability": round(f.get("probability", 0) * 100, 2)
                    } for f in res.get("family_probabilities", [])
                ],
                "allModels": [
                    {
                        "model": m.get("model"),
                        "family": m.get("family_name"),
                        "probability": round(m.get("probability", 0) * 100, 2)
                    } for m in all_results
                ]
            })
        except Exception as e:
            return jsonify({"success": False, "error": f"归因异常: {str(e)}"}), 500

    @app.route("/api/modeltrace/test_api", methods=["POST"])
    def api_modeltrace_test_api():
        req = request.get_json() or {}
        base_url = req.get("base_url", "").strip()
        api_key = req.get("api_key", "").strip()
        model = req.get("model", "").strip()
        count = int(req.get("count", 3))
        timeout = float(req.get("timeout", 25.0))
        api_format = req.get("api_format", "auto")
        temperature = req.get("temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (ValueError, TypeError):
                temperature = None

        if not base_url or not model:
            return jsonify({"success": False, "error": "请提供 Base URL 和模型名称"}), 400

        try:
            bank = load_bank(BANK_FILE)
            collector = ConcurrentCollector(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout=timeout,
                api_format=api_format
            )

            probe_res = collector.probe_and_analyze(bank, count=count, temperature=temperature)
            return jsonify(probe_res)
        except Exception as e:
            return jsonify({"success": False, "error": f"在线并发探测失败: {str(e)}"}), 500
