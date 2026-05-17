#!/usr/bin/env python
"""过期会话清理脚本 — 清理超过 TTL 的过期会话，防止数据无限增长。

用法:
  python scripts/cleanup_sessions.py [--ttl-hours 24] [--dry-run]

功能:
  1. 扫描所有会话，找出超过 TTL 的过期会话
  2. 输出过期会话统计信息
  3. 非 dry-run 模式下删除过期会话
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def cleanup(ttl_hours: int, dry_run: bool) -> int:
    # 尝试使用 SQL 持久化
    session_store = None
    try:
        import os
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            from agent_platform.persistence.sql import SqlAgentSessionRepository

            engine = create_async_engine(db_url, echo=False)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            session_store = SqlAgentSessionRepository(session_factory)
            print(f"使用 SQL 后端: {db_url}")
        else:
            print("未配置 DATABASE_URL，使用内存存储（无需清理）")
            return 0
    except Exception as e:
        print(f"SQL 后端初始化失败: {e}")
        return 0

    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    print(f"TTL: {ttl_hours} 小时，截止时间: {cutoff.isoformat()}")

    sessions = await session_store.list_sessions()
    total = len(sessions)
    expired = [s for s in sessions if s.updated_at < cutoff]
    active = total - len(expired)

    print("\n会话统计:")
    print(f"  总会话数: {total}")
    print(f"  活跃会话: {active}")
    print(f"  过期会话: {len(expired)}")

    if not expired:
        print("\n无需清理。")
        return 0

    if dry_run:
        print(f"\n[DRY RUN] 以下 {len(expired)} 个会话将被清理:")
        for s in expired[:20]:
            age = datetime.now(UTC) - s.updated_at
            print(f"  {s.session_id} (agent={s.agent_id}, 年龄={age.total_seconds()/3600:.1f}h)")
        if len(expired) > 20:
            print(f"  ... 还有 {len(expired) - 20} 个")
        return len(expired)

    deleted = 0
    for s in expired:
        try:
            await session_store.delete(s.session_id)
            deleted += 1
        except Exception as e:
            print(f"  删除失败 {s.session_id}: {e}")

    print(f"\n已清理 {deleted}/{len(expired)} 个过期会话。")
    return deleted


def main():
    parser = argparse.ArgumentParser(description="过期会话清理")
    parser.add_argument("--ttl-hours", type=int, default=24, help="会话 TTL（小时）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际删除")
    args = parser.parse_args()

    print("=== Agent Platform 过期会话清理 ===\n")
    result = asyncio.run(cleanup(args.ttl_hours, args.dry_run))
    if result > 0 and not args.dry_run:
        print(f"\n清理完成，释放 {result} 个会话。")


if __name__ == "__main__":
    main()
