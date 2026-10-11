"""Provision checksummed public tokenizers before offline CI validation."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIKTOKEN_ASSETS = {
    "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
}
QWEN_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
QWEN_SHA256 = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"


def ensure_asset(path: Path, urls: tuple[str, ...], expected_hash: str) -> None:
    if path.is_file():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Cached tokenizer checksum mismatch: {path}")
        return
    errors = []
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AgentHub-CI"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
            if hashlib.sha256(payload).hexdigest() != expected_hash:
                raise ValueError("Downloaded tokenizer checksum mismatch")
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return
        except (OSError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Tokenizer provisioning failed: " + "; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen", action="store_true")
    args = parser.parse_args()
    cache = Path(os.environ.setdefault(
        "TIKTOKEN_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache"),
    ))
    for name, digest in TIKTOKEN_ASSETS.items():
        url = f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken"
        # tiktoken names cached files by URL SHA-1; integrity uses SHA-256.
        cache_key = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()
        ensure_asset(cache / cache_key, (url,), digest)
        import tiktoken

        tiktoken.get_encoding(name).encode("AgentHub 中文验证")
        print(f"[ready] {name}: verified SHA-256 and offline runtime load")
    if args.qwen:
        path = ROOT / "assets" / "tokenizers" / "qwen" / "tokenizer.json"
        suffix = f"/Qwen/Qwen2.5-7B-Instruct/resolve/{QWEN_REVISION}/tokenizer.json"
        ensure_asset(path, (
            "https://huggingface.co" + suffix,
            "https://hf-mirror.com" + suffix,
        ), QWEN_SHA256)
        sys.path.insert(0, str(ROOT))
        from benchmarks.fetch_tokenizers import _verify_through_production_loader

        _verify_through_production_loader("qwen", path)
        print("[ready] qwen: pinned revision, verified SHA-256 and runtime load")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
