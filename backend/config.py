import os
from pathlib import Path

# 加载 .env（兼容任何启动方式）
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

VAULT_PAPERS_DIR = Path("/Users/wulinxie/Desktop/claudesidian/03_资源/科研/papers")
MINERU_TOKEN = os.getenv("MINERU_TOKEN", "")
PAPER_ANALYZER_SCRIPTS_DIR = Path("/Users/wulinxie/.claude/skills/paper-analyzer/scripts")
PAPER_ANALYZER_STYLES_DIR = Path("/Users/wulinxie/.claude/skills/paper-analyzer/styles")
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
