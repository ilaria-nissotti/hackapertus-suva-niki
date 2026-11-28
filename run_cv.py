"""Grouped cross-validation on the training split; same as `python -m suva cv`.

Usage (from the repo root, venv active):

    python run_cv.py data/scoring/dataset.jsonl
    python run_cv.py data/scoring/dataset.jsonl --modes refit random --seeds 0 1 2 --top-n 20
"""

from __future__ import annotations

import sys

from suva.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        args = ["--dataset", args[0], *args[1:]]
    raise SystemExit(main(["cv", *args]))
