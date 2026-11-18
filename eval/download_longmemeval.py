"""Descarga LongMemEval-cleaned (licencia MIT) a eval/datasets/, que git ignora.

Verifica el SHA-256 que publica Hugging Face antes de dar el archivo por bueno.

Uso:
    uv run python eval/download_longmemeval.py [--file longmemeval_s_cleaned.json]
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import httpx

REPO = "xiaowu0162/longmemeval-cleaned"
TREE = f"https://huggingface.co/api/datasets/{REPO}/tree/main"
FILE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/main/{{name}}"
DATASETS = Path(__file__).parent / "datasets"


def expected_sha256(name: str) -> str:
    for entry in httpx.get(TREE, timeout=30).raise_for_status().json():
        if entry.get("path") == name:
            return str(entry["lfs"]["oid"])
    raise SystemExit(f"{name} no está en {REPO}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(name: str) -> Path:
    target = DATASETS / name
    expected = expected_sha256(name)
    if target.exists() and _sha256(target) == expected:
        print(f"{target} ya está descargado y verificado")
        return target
    DATASETS.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    url = FILE_URL.format(name=name)
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                digest.update(chunk)
                out.write(chunk)
    if digest.hexdigest() != expected:
        partial.unlink()
        raise SystemExit(f"SHA-256 incorrecto para {name}: archivo descartado")
    partial.rename(target)
    print(f"{target}: {target.stat().st_size / 1e6:.1f} MB, SHA-256 verificado")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="longmemeval_s_cleaned.json")
    download(parser.parse_args().file)


if __name__ == "__main__":
    main()
