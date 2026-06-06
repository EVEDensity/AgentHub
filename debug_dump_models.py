"""One-off DB diagnostic: dump model_configs + agent_registry + role_bindings."""
import asyncio
import asyncpg
import os

DB_URL = os.environ.get("DATABASE_URL") or open(".env", encoding="utf-8").read().split("DATABASE_URL=", 1)[1].splitlines()[0].strip()


async def main() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        print("=" * 80)
        print("TABLE: model_configs (id, provider, model_name, base_url, is_active)")
        print("=" * 80)
        rows = await conn.fetch(
            "SELECT id, provider, model_name, base_url, is_active FROM model_configs ORDER BY id"
        )
        for r in rows:
            print(f"  id={r['id']:>3}  provider={r['provider']:<12}  model_name={r['model_name']:<40}  active={r['is_active']}  base_url={r['base_url'][:60] if r['base_url'] else ''}")

        print()
        print("=" * 80)
        print("TABLE: agent_registry (agent_id, domain, adapter_type, base_model_name, status)")
        print("=" * 80)
        rows = await conn.fetch(
            "SELECT agent_id, domain, adapter_type, base_model_name, status FROM agent_registry ORDER BY agent_id"
        )
        for r in rows:
            print(f"  agent_id={r['agent_id']:<15}  domain={r['domain']:<15}  adapter={r['adapter_type']:<10}  base_model={r['base_model_name']:<35}  status={r['status']}")

        print()
        print("=" * 80)
        print("TABLE: role_bindings (role, model_config_id)")
        print("=" * 80)
        try:
            rows = await conn.fetch(
                """SELECT rb.role, rb.model_config_id, mc.provider, mc.model_name
                   FROM role_bindings rb
                   LEFT JOIN model_configs mc ON rb.model_config_id = mc.id
                   ORDER BY rb.role, rb.model_config_id"""
            )
            if not rows:
                print("  *** role_bindings 表是空的！所有 agent 走 fallback ***")
            for r in rows:
                print(f"  role={r['role']:<15}  model_config_id={r['model_config_id']}  -> {r['provider']}/{r['model_name']}")
        except Exception as e:
            print(f"  role_bindings 查询失败: {e}")
    finally:
        await conn.close()


asyncio.run(main())
