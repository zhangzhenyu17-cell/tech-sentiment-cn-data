from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}
FORBIDDEN_PATH_PARTS = {"forward", "holdout", "portfolio", "shadow", "signal"}
FORBIDDEN_IMPORTS = re.compile(
    r"(?:from|import)\s+(?:tech_sentiment\.)?(?:features|buy_low_sell_high_policy|"
    r"confirmation_spectrum|crowding|exit_risk|top_divergence|v2_shadow)\b"
)
CREDENTIAL_MARKERS = ("ghp" + "_", "github" + "_pat_", "AKIA" + "[0-9A-Z]")


def main() -> None:
    failures: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if not path.is_file():
            continue
        lowered = relative.as_posix().lower()
        if any(part in lowered for part in FORBIDDEN_PATH_PARTS):
            failures.append(f"forbidden public path: {relative}")
        if path == Path(__file__):
            continue
        if path.suffix in {".py", ".yml", ".yaml", ".toml"}:
            text = path.read_text(encoding="utf-8")
            if FORBIDDEN_IMPORTS.search(text):
                failures.append(f"private model import: {relative}")
            for marker in CREDENTIAL_MARKERS:
                if re.search(marker, text):
                    failures.append(f"credential-like content: {relative}")
    if failures:
        raise SystemExit("public-tree audit failed:\n" + "\n".join(failures))
    print("public-tree audit passed")


if __name__ == "__main__":
    main()
