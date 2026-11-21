"""Validate a human-reviewed wake-word training asset manifest.

This tool records provenance and verifies local file hashes. It does NOT
interpret licenses or decide whether a license permits model training.
Every asset must have an explicit human approval record before the training
pipeline is allowed to consume it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FIELDS = {
    "asset_id",
    "asset_type",
    "source_url",
    "source_revision",
    "relative_path",
    "sha256",
    "license_id",
    "attribution",
    "restrictions",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "approved_uses",
}


class ManifestError(ValueError):
    """Raised when manifest provenance, approval, or local file checks fail."""


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_asset_path(asset_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or candidate.drive or not candidate.parts:
        raise ManifestError(f"Asset path must be relative: {relative_path!r}")
    if any(part in ("..", "") for part in candidate.parts):
        raise ManifestError(f"Asset path may not escape the asset root: {relative_path!r}")
    root = asset_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"Asset path escapes the asset root: {relative_path!r}") from exc
    return resolved


def validate_manifest(
    manifest_path: Path,
    asset_root: Path,
    *,
    verify_hashes: bool = True,
    required_use: str = "model_training",
) -> list[str]:
    """Validate all asset records and optionally stream-verify local SHA-256.

    A successful result means the manifest has complete fields, a human has
    marked each asset approved for the requested use, and local bytes match
    the recorded hashes. It does not independently establish that the human
    license review was correct.
    """
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read JSON manifest: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("manifest_version") != 1:
        raise ManifestError("manifest_version must be exactly 1")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ManifestError("assets must be a non-empty list")

    errors: list[str] = []
    seen_ids: set[str] = set()

    for index, asset in enumerate(assets):
        label = f"assets[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"{label} must be an object")
            continue

        missing = sorted(_REQUIRED_FIELDS - asset.keys())
        if missing:
            errors.append(f"{label} is missing fields: {', '.join(missing)}")
            continue

        asset_id = asset["asset_id"]
        if not _is_nonempty_text(asset_id):
            errors.append(f"{label}.asset_id must be non-empty text")
            continue
        if asset_id in seen_ids:
            errors.append(f"Duplicate asset_id: {asset_id}")
        seen_ids.add(asset_id)

        for field in ("asset_type", "source_url", "source_revision",
                      "relative_path", "license_id", "reviewed_by", "reviewed_at"):
            if not _is_nonempty_text(asset[field]):
                errors.append(f"{label}.{field} must be non-empty text")

        parsed_url = urlparse(str(asset["source_url"]))
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            errors.append(f"{label}.source_url must be an HTTPS URL")

        if not isinstance(asset["sha256"], str) or not _SHA256_RE.fullmatch(asset["sha256"]):
            errors.append(f"{label}.sha256 must be 64 lowercase hexadecimal characters")

        if asset["review_status"] != "approved":
            errors.append(f"{label} is not approved (review_status must be 'approved')")
        approved_uses = asset["approved_uses"]
        if not isinstance(approved_uses, list) or required_use not in approved_uses:
            errors.append(f"{label}.approved_uses must explicitly include {required_use!r}")

        if not isinstance(asset["restrictions"], list) or any(
            not _is_nonempty_text(item) for item in asset["restrictions"]
        ):
            errors.append(f"{label}.restrictions must be a list of non-empty text entries")
        if not _is_nonempty_text(asset["attribution"]):
            errors.append(f"{label}.attribution must contain attribution text or an explicit 'Not required'")

        reviewed_at = asset["reviewed_at"]
        if _is_nonempty_text(reviewed_at):
            try:
                date.fromisoformat(reviewed_at[:10])
            except ValueError:
                errors.append(f"{label}.reviewed_at must begin with an ISO date (YYYY-MM-DD)")

        if verify_hashes and isinstance(asset["relative_path"], str) and isinstance(asset["sha256"], str) and _SHA256_RE.fullmatch(asset["sha256"]):
            try:
                local_file = _safe_asset_path(asset_root, asset["relative_path"])
                if not local_file.is_file():
                    errors.append(f"{label} asset file does not exist: {asset['relative_path']}")
                else:
                    digest = hashlib.sha256()
                    with local_file.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != asset["sha256"]:
                        errors.append(f"{label} SHA-256 mismatch for {asset['relative_path']}")
            except (OSError, ManifestError) as exc:
                errors.append(f"{label} path/hash check failed: {exc}")

    if errors:
        raise ManifestError("\n".join(errors))
    return [str(asset["asset_id"]) for asset in assets]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True,
                        help="Root directory against which relative asset paths are checked")
    parser.add_argument("--required-use", default="model_training",
                        help="Approval use required for every asset (default: model_training)")
    parser.add_argument("--skip-hash-check", action="store_true",
                        help="Check provenance/approval fields without reading asset bytes")
    args = parser.parse_args(argv)

    try:
        asset_ids = validate_manifest(
            args.manifest.expanduser().resolve(),
            args.asset_root.expanduser().resolve(),
            verify_hashes=not args.skip_hash_check,
            required_use=args.required_use,
        )
    except ManifestError as exc:
        print(f"Asset manifest REJECTED:\n{exc}", file=sys.stderr)
        return 1

    print(f"Asset manifest PASSED: {len(asset_ids)} reviewed asset(s).")
    if not args.skip_hash_check:
        print("Local SHA-256 checks passed. License interpretation remains a human review responsibility.")
    else:
        print("Hash checks were skipped; this result is NOT sufficient to start training.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
