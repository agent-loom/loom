"""Hermes Echo Agent 真实模型端到端验证脚本。

使用 ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL 代理调用真实模型（z-ai/glm-5），
绕过 HTTP 层直接构造 RuntimeRequest → HermesRuntimeBackend.run()。

运行：
    uv run python scripts/hermes_echo_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# 项目根目录加入 path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

# 用 override=True 确保 .env 里的 Anthropic 配置优先于 shell 占位值
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import yaml

from agent_platform.domain.models import (
    AgentInput,
    AgentManifest,
    AgentRequest,
    AgentSpec,
    RuntimeRequest,
)
from agent_platform.runtime.hermes import HERMES_AVAILABLE, HermesRuntimeBackend


def _load_spec() -> AgentSpec:
    package_path = ROOT / "agents" / "hermes_echo"
    manifest_path = package_path / "manifest.yaml"
    raw = yaml.safe_load(manifest_path.read_text()) or {}
    manifest = AgentManifest.model_validate(raw)
    return AgentSpec(manifest=manifest, package_path=package_path)


def _make_request(query: str, session_id: str = "e2e-session-1") -> RuntimeRequest:
    return RuntimeRequest(
        request=AgentRequest(
            request_id="e2e-req-001",
            session_id=session_id,
            input=AgentInput(query=query),
        ),
        agent_spec=_load_spec(),
        route_reason="e2e-test",
    )


async def run_e2e(query: str) -> None:
    print(f"\n{'='*60}")
    print(f"Hermes Echo E2E — 真实模型测试")
    print(f"{'='*60}")
    print(f"HERMES_AVAILABLE : {HERMES_AVAILABLE}")
    print(f"ANTHROPIC_BASE_URL: {os.environ.get('ANTHROPIC_BASE_URL', '(未设置)')}")
    print(f"ANTHROPIC_AUTH_TOKEN: {'已设置' if os.environ.get('ANTHROPIC_AUTH_TOKEN') else '(未设置)'}")
    print(f"Query: {query!r}")
    print(f"{'-'*60}")

    if not HERMES_AVAILABLE:
        print("[FAIL] Hermes SDK (run_agent) 未安装，无法运行真实 E2E")
        sys.exit(1)

    backend = HermesRuntimeBackend()
    request = _make_request(query)

    t0 = time.perf_counter()
    try:
        response = await backend.run(request)
    except Exception as exc:
        print(f"[FAIL] backend.run() 抛出异常: {exc}")
        raise

    elapsed = time.perf_counter() - t0

    text = response.response.output.text.display
    trace = response.response.trace

    print(f"响应文本   : {text!r}")
    print(f"耗时       : {elapsed:.2f}s")
    print(f"模型       : {trace.model or '(未返回)'}")
    print(f"Token 用量 : in={trace.prompt_tokens} out={trace.completion_tokens} total={trace.total_tokens}")
    if trace.tool_calls:
        print(f"工具调用   : {[tc.tool_name for tc in trace.tool_calls]}")

    # 验证：响应不是 stub
    assert "[Hermes-stub]" not in text, f"响应是 stub，未调用真实模型: {text!r}"
    assert len(text.strip()) > 0, "响应为空"
    print(f"\n[PASS] 真实模型调用成功")


async def run_multi_turn() -> None:
    """多轮对话验证 session 连续性。"""
    print(f"\n{'='*60}")
    print(f"多轮对话测试")
    print(f"{'='*60}")

    backend = HermesRuntimeBackend()
    session_id = "e2e-multi-turn-001"

    turns = [
        "你好，我叫小明",
        "你还记得我刚才说的我叫什么名字吗？",
    ]

    for i, query in enumerate(turns, 1):
        print(f"\n[Turn {i}] {query!r}")
        request = _make_request(query, session_id=session_id)
        response = await backend.run(request)
        text = response.response.output.text.display
        print(f"  回复: {text!r}")

    print(f"\n[INFO] 多轮对话完成（session 记忆依赖 session_store，此脚本未配置，属预期）")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "请用一句话介绍你自己"
    asyncio.run(run_e2e(query))
    asyncio.run(run_multi_turn())
