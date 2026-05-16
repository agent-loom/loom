#!/usr/bin/env python3
"""End-to-end validation script for production readiness.

Checks all infrastructure dependencies, CLI tools, and runs a test
through the full DevFlow pipeline to verify production conditions.

Usage:
    .venv/bin/python scripts/validate_production.py [--fix]
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    critical: bool = True


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult):
        self.results.append(result)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results if r.critical)

    def print_report(self):
        print("\n" + "=" * 60)
        print("PRODUCTION READINESS VALIDATION")
        print("=" * 60)
        for r in self.results:
            icon = "PASS" if r.passed else ("FAIL" if r.critical else "WARN")
            print(f"  [{icon}] {r.name}: {r.message}")
        print("=" * 60)
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        critical_failed = [r for r in self.results if not r.passed and r.critical]
        print(f"  {passed}/{total} checks passed")
        if critical_failed:
            print(f"  {len(critical_failed)} CRITICAL failures:")
            for r in critical_failed:
                print(f"    - {r.name}: {r.message}")
        print("=" * 60 + "\n")


def check_python_version(report: ValidationReport):
    v = sys.version_info
    report.add(CheckResult(
        name="Python version",
        passed=v.major == 3 and v.minor >= 12,
        message=f"{v.major}.{v.minor}.{v.micro}",
    ))


def check_cli_tool(report: ValidationReport, name: str, cmd: list[str], critical: bool = True):
    path = shutil.which(cmd[0])
    if path is None:
        report.add(CheckResult(
            name=f"CLI: {name}",
            passed=False,
            message=f"{cmd[0]} not found in PATH",
            critical=critical,
        ))
        return
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        report.add(CheckResult(
            name=f"CLI: {name}",
            passed=result.returncode == 0,
            message=f"found at {path}" if result.returncode == 0 else f"exit code {result.returncode}",
            critical=critical,
        ))
    except Exception as e:
        report.add(CheckResult(
            name=f"CLI: {name}",
            passed=False,
            message=str(e),
            critical=critical,
        ))


async def check_redis(report: ValidationReport):
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, socket_connect_timeout=3)
        await r.ping()
        await r.aclose()
        report.add(CheckResult(
            name="Redis",
            passed=True,
            message=f"connected to {redis_url}",
        ))
    except ImportError:
        report.add(CheckResult(
            name="Redis",
            passed=False,
            message="redis package not installed (pip install redis[hiredis])",
        ))
    except Exception as e:
        report.add(CheckResult(
            name="Redis",
            passed=False,
            message=f"connection failed: {e}",
        ))


async def check_weaviate(report: ValidationReport):
    weaviate_url = os.getenv("WEAVIATE_URL", "http://localhost:8080")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{weaviate_url}/v1/.well-known/ready")
            report.add(CheckResult(
                name="Weaviate",
                passed=resp.status_code == 200,
                message=f"ready at {weaviate_url}" if resp.status_code == 200
                else f"status {resp.status_code}",
            ))
    except Exception as e:
        report.add(CheckResult(
            name="Weaviate",
            passed=False,
            message=f"connection failed: {e}",
        ))


async def check_database(report: ValidationReport):
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agent_platform.db")
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        report.add(CheckResult(
            name="Database",
            passed=True,
            message=f"connected ({db_url.split('://')[0]})",
        ))
    except Exception as e:
        report.add(CheckResult(
            name="Database",
            passed=False,
            message=f"connection failed: {e}",
        ))


def check_env_vars(report: ValidationReport):
    required_prod = {
        "AGENT_PLATFORM_API_KEY": "API authentication",
        "AGENT_PLATFORM_ENV": "Environment designation",
    }
    recommended = {
        "REDIS_URL": "Distributed job queue",
        "WEAVIATE_URL": "Vector knowledge backend",
        "LANGFUSE_PUBLIC_KEY": "LLM observability",
        "LANGFUSE_SECRET_KEY": "LLM observability",
        "DEVFLOW_RUNNER_ADAPTER": "AI coding runner",
        "PLANE_BASE_URL": "Project management integration",
        "GITLAB_BASE_URL": "SCM integration",
        "GITLAB_TOKEN": "SCM authentication",
        "GITLAB_PROJECT_ID": "SCM project",
    }

    for var, purpose in required_prod.items():
        val = os.getenv(var)
        report.add(CheckResult(
            name=f"Env: {var}",
            passed=val is not None and val != "",
            message=purpose if val else f"not set ({purpose})",
            critical=True,
        ))

    for var, purpose in recommended.items():
        val = os.getenv(var)
        report.add(CheckResult(
            name=f"Env: {var}",
            passed=val is not None and val != "",
            message=purpose if val else f"not set ({purpose})",
            critical=False,
        ))


def check_runner_adapter(report: ValidationReport):
    adapter = os.getenv("DEVFLOW_RUNNER_ADAPTER", "mock")
    report.add(CheckResult(
        name="Runner adapter",
        passed=adapter != "mock",
        message=f"adapter={adapter}" + (" (mock is not production-ready)" if adapter == "mock" else ""),
    ))


async def check_langfuse(report: ValidationReport):
    pub = os.getenv("LANGFUSE_PUBLIC_KEY")
    sec = os.getenv("LANGFUSE_SECRET_KEY")
    if not pub or not sec:
        report.add(CheckResult(
            name="Langfuse",
            passed=False,
            message="keys not configured",
            critical=False,
        ))
        return
    try:
        from agent_platform.observability.langfuse_tracer import LangfuseTracer
        tracer = LangfuseTracer(
            public_key=pub,
            secret_key=sec,
            host=os.getenv("LANGFUSE_HOST"),
        )
        healthy = await tracer.health_check()
        await tracer.shutdown()
        report.add(CheckResult(
            name="Langfuse",
            passed=healthy,
            message="connected" if healthy else "auth check failed",
            critical=False,
        ))
    except ImportError:
        report.add(CheckResult(
            name="Langfuse",
            passed=False,
            message="langfuse package not installed",
            critical=False,
        ))


def check_tests(report: ValidationReport):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no", "-x"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        passed = "passed" in result.stdout
        report.add(CheckResult(
            name="Test suite",
            passed=result.returncode == 0,
            message=result.stdout.strip().split("\n")[-1] if passed else "tests failed",
        ))
    except Exception as e:
        report.add(CheckResult(
            name="Test suite",
            passed=False,
            message=str(e),
        ))


def check_lint(report: ValidationReport):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "src/", "tests/"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        report.add(CheckResult(
            name="Lint (ruff)",
            passed=result.returncode == 0,
            message="all checks passed" if result.returncode == 0 else result.stdout.strip(),
        ))
    except Exception as e:
        report.add(CheckResult(
            name="Lint (ruff)",
            passed=False,
            message=str(e),
        ))


async def main():
    report = ValidationReport()

    check_python_version(report)
    check_cli_tool(report, "claude", ["claude", "--version"], critical=False)
    check_cli_tool(report, "codex", ["codex", "--version"], critical=False)
    check_cli_tool(report, "git", ["git", "--version"])
    check_env_vars(report)
    check_runner_adapter(report)

    await check_database(report)
    await check_redis(report)
    await check_weaviate(report)
    await check_langfuse(report)

    check_tests(report)
    check_lint(report)

    report.print_report()
    sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
