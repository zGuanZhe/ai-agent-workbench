# -*- coding: utf-8 -*-
"""
高并发异步/多线程多轮采样与快速探测引擎 (Concurrent Probing & Sampling Collector)
ModelTrace Prober & Collector
"""

from __future__ import annotations
import math
import os
import time
import json
import random
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path

# 内部依赖
from fingerprint import parse_numbers, analyze_global_outputs, load_bank
from challenge_suite import CHINESE_TEMPLATES, ENGLISH_TEMPLATES
from spoof_detector import SpoofDetector

DEFAULT_USER_AGENT = (
    "Codex Desktop/0.147.0-alpha.1.2 (Windows 10.0.26200; x86_64) unknown "
    "(codex_exec; 0.147.0-alpha.1.2)"
)

class ConcurrentCollector:
    """高并发模型采样与快速探测器"""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 30.0,
        api_format: str = "auto",
        user_agent: Optional[str] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.api_format = api_format
        self.user_agent = user_agent or os.environ.get("MODELTRACE_USER_AGENT", DEFAULT_USER_AGENT)

    def _build_url(self, api_format: str) -> str:
        url = self.base_url
        if api_format == "anthropic":
            if url.endswith("/messages"):
                return url
            if url.endswith("/v1"):
                return url + "/messages"
            return url + "/v1/messages"
        else:
            if url.endswith("/chat/completions"):
                return url
            if url.endswith("/v1"):
                return url + "/chat/completions"
            return url + "/v1/chat/completions"

    def _execute_http(self, prompt: str, api_format: str, temperature: Optional[float] = None) -> str:
        url = self._build_url(api_format)
        if api_format == "anthropic":
            payload = {
                "model": self.model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "x-api-key": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
        else:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }

        if temperature is not None:
            payload["temperature"] = temperature

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            res_json = json.loads(body)

        if api_format == "anthropic":
            content = "".join(
                b.get("text", "") for b in res_json.get("content", []) if b.get("type") == "text"
            )
            if res_json.get("stop_reason") == "refusal":
                raise RuntimeError("模型拒绝回答 (refusal)")
        else:
            choices = res_json.get("choices", [])
            if not choices:
                raise RuntimeError("上游返回 choices 为空")
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content)
            finish_reason = choices[0].get("finish_reason")
            if finish_reason in {"length", "content_filter"}:
                raise RuntimeError(f"模型未正常完成: {finish_reason}")

        return str(content)

    def sample_single(
        self,
        prompt: str,
        expected_count: int = 250,
        temperature: Optional[float] = None,
        mock_fn: Optional[Callable[[str], str]] = None
    ) -> Dict[str, Any]:
        """执行单次采样并解析验证"""
        t0 = time.perf_counter()
        if mock_fn:
            text = mock_fn(prompt)
        else:
            if self.api_format != "auto":
                text = self._execute_http(prompt, self.api_format, temperature)
            else:
                try:
                    text = self._execute_http(prompt, "openai", temperature)
                except Exception as err_openai:
                    try:
                        text = self._execute_http(prompt, "anthropic", temperature)
                    except Exception as err_anthropic:
                        raise RuntimeError(f"接口自动探测失败 (OpenAI: {err_openai}; Anthropic: {err_anthropic})")

        latency = round(time.perf_counter() - t0, 3)
        numbers = parse_numbers(text)
        threshold = max(80, math.ceil(expected_count * 0.55))
        valid = len(numbers) >= threshold

        return {
            "text": text,
            "latency": latency,
            "parsed_count": len(numbers),
            "threshold": threshold,
            "valid": valid,
            "numbers": numbers,
            "expected_count": expected_count,
        }

    def collect_parallel(
        self,
        count: int = 3,
        temperature: Optional[float] = None,
        use_chinese: bool = True,
        mock_fn: Optional[Callable[[str], str]] = None
    ) -> Dict[str, Any]:
        """并发采样指定轮次 (默认 3 组)"""
        templates = CHINESE_TEMPLATES if use_chinese else ENGLISH_TEMPLATES
        prompts = []
        for i in range(count):
            tpl = templates[i % len(templates)]
            cnt = 250 + (i * 5)
            prompts.append((tpl.format(count=cnt), cnt))

        start_time = time.perf_counter()
        results: List[Dict[str, Any]] = []
        errors: List[str] = []

        with ThreadPoolExecutor(max_workers=min(count, 5)) as pool:
            future_to_idx = {
                pool.submit(self.sample_single, p, cnt, temperature, mock_fn): idx
                for idx, (p, cnt) in enumerate(prompts)
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    res = fut.result()
                    if res["valid"]:
                        results.append(res)
                    else:
                        errors.append(f"采样 #{idx+1} 有效数字不足 ({res['parsed_count']}/{res['threshold']})")
                except Exception as ex:
                    errors.append(f"采样 #{idx+1} 异常: {str(ex)}")

        total_duration = round(time.perf_counter() - start_time, 3)
        avg_latency = round(sum(r["latency"] for r in results) / max(len(results), 1), 3)

        return {
            "success": len(results) >= count or len(results) >= 1,
            "duration_sec": total_duration,
            "avg_latency_sec": avg_latency,
            "collected_count": len(results),
            "target_count": count,
            "outputs": results,
            "errors": errors,
        }

    def probe_and_analyze(
        self,
        bank: Dict[str, Any],
        count: int = 3,
        temperature: Optional[float] = None,
        mock_fn: Optional[Callable[[str], str]] = None
    ) -> Dict[str, Any]:
        """一站式并发探测、抗伪造检测与数学归因"""
        collect_res = self.collect_parallel(count=count, temperature=temperature, mock_fn=mock_fn)
        if not collect_res["outputs"]:
            return {
                "success": False,
                "duration_sec": collect_res["duration_sec"],
                "error": "未采集到有效的大模型数字序列: " + "; ".join(collect_res["errors"]),
                "errors": collect_res["errors"]
            }

        # 1. 抗对抗伪造检测
        all_numbers = [item["numbers"] for item in collect_res["outputs"]]
        spoof_report = SpoofDetector.check_outputs(all_numbers)

        # 2. 统计指纹与模型归因
        items = [{"text": item["text"], "expected_count": item["expected_count"]} for item in collect_res["outputs"]]
        while len(items) < 3:
            items.append(items[0])

        attr_res = analyze_global_outputs(items, bank)
        top_results = sorted(attr_res.get("results", []), key=lambda x: x.get("probability", 0), reverse=True)

        family_name = attr_res.get("family_prediction_name", "未知")
        family_prob = round(attr_res.get("family_probability", 0) * 100, 2)
        top_model = top_results[0].get("model", "未知") if top_results else "未知"

        if spoof_report.get("is_spoofed"):
            verdict = f"[伪造拦截] 疑似代码伪造/反代掺水注入 (置信度: {round(spoof_report['confidence']*100, 1)}%)"
        else:
            verdict = f"{family_name} / {top_model} ({family_prob}%)"

        return {
            "success": True,
            "duration_sec": collect_res["duration_sec"],
            "avg_latency_sec": collect_res["avg_latency_sec"],
            "collected_count": len(collect_res["outputs"]),
            "spoof_detected": spoof_report.get("is_spoofed", False),
            "spoof_report": spoof_report,
            "verdict": verdict,
            "familyPrediction": family_name,
            "familyProbability": family_prob,
            "topMatch": top_model,
            "topModels": [
                {
                    "model": m.get("model"),
                    "family": m.get("family_name"),
                    "probability": round(m.get("probability", 0) * 100, 2)
                } for m in top_results[:6]
            ],
            "familyProbabilities": [
                {
                    "family": f.get("display_name"),
                    "probability": round(f.get("probability", 0) * 100, 2)
                } for f in attr_res.get("family_probabilities", [])
            ],
            "allModels": [
                {
                    "model": m.get("model"),
                    "family": m.get("family_name"),
                    "probability": round(m.get("probability", 0) * 100, 2)
                } for m in top_results
            ],
            "errors": collect_res["errors"]
        }
