from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_public_workflows_do_not_reference_repository_secrets() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "secrets." not in text, path.name


def test_public_workflows_do_not_embed_private_credentials_or_tokens() -> None:
    suspicious = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|private[_-]?key|client[_-]?secret)\s*[:=]\s*[^$\s{][^\s#]*"
    )
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert suspicious.search(text) is None, path.name


def test_public_repo_keeps_private_model_repo_as_identity_only_not_fetch_target() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "github.com/zhangzhenyu17-cell/tech-sentiment-cn.git" not in text, path.name
        assert "gh repo clone zhangzhenyu17-cell/tech-sentiment-cn" not in text, path.name
        assert "actions/checkout" not in text or "repository: zhangzhenyu17-cell/tech-sentiment-cn\n" not in text, path.name
