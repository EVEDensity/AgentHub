"""One-off: cross-check DB avatar_url vs filesystem."""
import asyncio
import asyncpg
from pathlib import Path
import os

DB_URL = open(".env", encoding="utf-8").read().split("DATABASE_URL=", 1)[1].splitlines()[0].strip()

# Reconstruct AVATAR_DIR same as app/api/agent.py
BASE_DIR = Path(__file__).resolve().parent / "app"
DATA_DIR = BASE_DIR / "data"
AVATAR_DIR = DATA_DIR / "avatars"

print(f"AVATAR_DIR = {AVATAR_DIR}")
print(f"AVATAR_DIR exists? {AVATAR_DIR.exists()}")
if AVATAR_DIR.exists():
    files = list(AVATAR_DIR.iterdir())
    print(f"AVATAR_DIR file count: {len(files)}")
    for f in files[:5]:
        print(f"  - {f.name} ({f.stat().st_size} bytes)")
else:
    print("AVATAR_DIR MISSING - all DB refs are dangling")
print()


async def main():
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT agent_id, display_name, avatar_url FROM agent_registry ORDER BY agent_id"
        )
        missing = []
        for r in rows:
            url = r["avatar_url"] or ""
            status = "(empty)"
            if url:
                if url.startswith("/api/agent/registry/avatar/"):
                    fname = url.rsplit("/", 1)[-1]
                    fp = AVATAR_DIR / fname
                    if fp.exists():
                        status = f"OK ({fp.stat().st_size} B)"
                    else:
                        status = "MISSING_FILE"
                        missing.append((r["agent_id"], url, fname))
                elif url.startswith("http"):
                    status = "REMOTE_URL"
                else:
                    status = f"UNKNOWN_FORMAT"
            print(f"{r['agent_id']:<15}  {r['display_name'] or '(no display)':<20}  url={url!r:<55}  {status}")
        print()
        print(f"=== TOTAL MISSING: {len(missing)} / {len(rows)} ===")
        for m in missing:
            print(f"  - {m[0]}: {m[1]} (file={m[2]})")
    finally:
        await conn.close()


asyncio.run(main())
