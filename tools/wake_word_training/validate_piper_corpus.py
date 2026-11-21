"""Read-only integrity and provenance validator for a generated Piper WAV corpus."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from tools.wake_word_training.piper_clip_generator import (
        PiperCorpusError,
        validate_piper_corpus_manifest,
    )
except ImportError:
    from piper_clip_generator import (
        PiperCorpusError,
        validate_piper_corpus_manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--asset-manifest", type=Path, required=True,
                        help="Parent manifest containing the approved Piper model/config records")
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--allow-single-language", action="store_true",
                        help="Do not require both English and Mexican-Spanish clips")
    args = parser.parse_args(argv)

    try:
        entries = validate_piper_corpus_manifest(
            args.corpus_manifest.expanduser().resolve(),
            args.corpus_root.expanduser().resolve(),
            asset_manifest_path=args.asset_manifest.expanduser().resolve(),
            asset_root=args.asset_root.expanduser().resolve(),
            require_english_and_mexican_spanish=not args.allow_single_language,
        )
    except PiperCorpusError as exc:
        print(f"Piper corpus REJECTED: {exc}", file=sys.stderr)
        return 1

    print(f"Piper corpus PASSED: {len(entries)} WAV file(s); 16 kHz mono PCM16.")
    print("Per-file SHA-256 values and reviewed voice-source lineage were verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
