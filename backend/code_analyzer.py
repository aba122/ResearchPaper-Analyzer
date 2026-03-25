"""GitHub 代码分析模块 — 克隆仓库、筛选关键文件、驱动 Claude 生成带行号的三维解读"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import file_manager as fm
from agent import stream_claude

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAR_BUDGET = 50_000      # 喂给 Claude 的总字符上限
MAX_FILE_CHARS = 4_000    # 单文件截断上限
README_MAX_CHARS = 2_000  # README 上限

SKIP_DIRS = {
    "test", "tests", "docs", "doc", "examples", "example",
    "benchmarks", "benchmark", "__pycache__", ".git",
    "node_modules", "build", "dist", "assets", ".github",
    "notebooks", "notebook", "demo", "demos",
}

PRIORITY_FILENAMES: Dict[str, int] = {
    "model.py": 100, "models.py": 90, "network.py": 85, "net.py": 80,
    "train.py": 90, "trainer.py": 90, "training.py": 85,
    "loss.py": 80, "losses.py": 80, "criterion.py": 75,
    "dataset.py": 70, "data.py": 65, "dataloader.py": 65,
    "config.py": 40, "utils.py": 20,
}

PRIORITY_DIRS: Dict[str, int] = {
    "models": 50, "model": 50, "src": 30, "core": 40,
    "training": 40, "train": 40, "data": 20, "losses": 30,
}


# ---------------------------------------------------------------------------
# Repo cloning
# ---------------------------------------------------------------------------

def clone_repo(github_url: str, dest: Path) -> None:
    """浅克隆 GitHub 仓库到 dest 目录（幂等：已存在则跳过）"""
    if shutil.which("git") is None:
        raise RuntimeError("git 未安装，无法进行代码分析")
    if (dest / ".git").exists():
        return  # 已克隆，跳过
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth=1", "--single-branch", github_url, str(dest)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"克隆失败: {result.stderr[:400]}")


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------

def _score_file(path: Path, repo_root: Path) -> int:
    rel = path.relative_to(repo_root)
    parts = rel.parts
    score = 0

    # 文件名得分
    score += PRIORITY_FILENAMES.get(path.name.lower(), 5)

    # 父目录得分
    for part in parts[:-1]:
        score += PRIORITY_DIRS.get(part.lower(), 0)

    # 路径含 test → 大减分
    if any("test" in p.lower() for p in parts):
        score -= 50

    # 深度惩罚（超过 3 层）
    depth = len(parts) - 1
    if depth > 3:
        score -= (depth - 3) * 5

    return score


def select_files(repo_root: Path, budget: int = CHAR_BUDGET) -> List[Tuple[str, str]]:
    """
    遍历仓库 .py 文件，按关键词评分筛选，每行注入行号，贪心填满 budget。
    返回 [(相对路径, 带行号内容), ...]
    """
    candidates: List[Tuple[int, Path]] = []

    for py_file in repo_root.rglob("*.py"):
        # 跳过 SKIP_DIRS 中的目录
        rel_parts = py_file.relative_to(repo_root).parts
        if any(p.lower() in SKIP_DIRS for p in rel_parts):
            continue
        score = _score_file(py_file, repo_root)
        candidates.append((score, py_file))

    candidates.sort(key=lambda x: x[0], reverse=True)

    selected: List[Tuple[str, str]] = []
    used_chars = 0

    for _, py_file in candidates:
        if used_chars >= budget:
            break
        try:
            raw = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # 注入行号
        lines = raw.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))

        # 截断单文件
        if len(numbered) > MAX_FILE_CHARS:
            numbered = numbered[:MAX_FILE_CHARS] + "\n  # ... [文件已截断] ..."

        rel_path = str(py_file.relative_to(repo_root))
        selected.append((rel_path, numbered))
        used_chars += len(numbered)

    return selected


def _read_readme(repo_root: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        p = repo_root / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:README_MAX_CHARS]
    return ""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompts(
    files: List[Tuple[str, str]],
    readme: str,
    analysis_md: str,
    title: str,
    github_url: str,
    sections: List[str],
) -> Tuple[str, str]:
    """构建 (human_prompt, system_prompt) 供 claude CLI 使用"""

    sections_spec = " / ".join(f"## {s}" for s in sections)

    system_prompt = f"""你是一位资深 ML 工程师，正在为一篇学术论文的代码仓库编写详细解读文档。

## 论文信息
标题：{title}
GitHub：{github_url}

## 论文创新点（来自 analysis.md，供"主要创新"章节参考）
{analysis_md[:5000] if analysis_md else "（尚未完成论文分析）"}

## 输出格式规范
- 严格按照以下章节顺序输出（只输出被请求的章节）：{sections_spec}
- 每一个处理步骤：先用 1 段自然语言描述，再紧跟一个代码块
- 代码块第一行必须是注释，格式：`# 文件路径  Line X-Y`，例如：
  ```python
  # models/vit.py  Line 45-67
  def forward(self, x):
      ...
  ```
- 行号必须与下方提供的代码（含行号前缀）对应，务必准确
- 不要捏造不存在的文件名或函数
- 使用中文撰写所有解读文字

## 章节要求

### 模型架构（如被请求）
按输入→处理→输出的数据流串联叙述，每个处理节点单独一个小节。
示例小节：输入处理 / Patch Embedding / Encoder / 输出解码

### 训练流程（如被请求）
包含三个固定子节：
1. **数据格式**：Dataset 类的 `__getitem__` 结构，样本字段说明
2. **训练范式**：识别并说明是 SFT / DPO / PPO / GRPO 还是其他，并引用对应代码
3. **损失函数**：每个 loss 的计算方式，含公式说明 + 代码块

### 主要创新（如被请求）
对照 analysis.md 中提炼的每个创新点：
1. 引用原文创新描述（blockquote 格式）
2. 在代码中找到对应实现，展示代码块

---
输出格式为 Markdown，直接输出内容，不要前言或后记。"""

    # Human prompt：README + 代码文件
    parts = []
    if readme:
        parts.append(f"## 仓库 README（节选）\n\n{readme}")

    if files:
        parts.append("## 仓库关键代码文件（含行号）")
        for rel_path, content in files:
            parts.append(f"### {rel_path}\n```python\n{content}\n```")
    else:
        parts.append("## 注意\n仓库中未找到 Python 文件，请根据目录结构尽力分析。")

    section_list = "、".join(sections)
    parts.append(f"\n---\n请输出以下分析章节：**{section_list}**\n按格式规范直接输出，不要多余说明。")

    human_prompt = "\n\n".join(parts)
    return human_prompt, system_prompt


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

async def run_code_analysis_agent(
    arxiv_id: str,
    github_url: str,
    sections: List[str],
) -> AsyncGenerator[str, None]:
    """克隆仓库 → 筛选文件 → Claude 分析 → 保存 code_analyse.md，yield SSE 消息"""

    def emit(msg: str) -> str:
        return f"data: {json.dumps({'type': 'progress', 'message': msg})}\n\n"

    loop = asyncio.get_event_loop()

    # ── Step 1: Clone ────────────────────────────────────────────────────────
    code_dir = fm.get_code_dir(arxiv_id)
    yield emit(f"正在克隆仓库：{github_url}")
    try:
        await loop.run_in_executor(None, clone_repo, github_url, code_dir)
        yield emit("✓ 仓库克隆完成")
    except Exception as e:
        yield emit(f"❌ 克隆失败: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    # ── Step 2: Select files ─────────────────────────────────────────────────
    yield emit("筛选关键代码文件（预算 50k 字符）...")
    try:
        files = await loop.run_in_executor(None, select_files, code_dir, CHAR_BUDGET)
        readme = await loop.run_in_executor(None, _read_readme, code_dir)
        total_chars = sum(len(c) for _, c in files)
        yield emit(f"✓ 选取 {len(files)} 个文件，共 {total_chars:,} 字符")
    except Exception as e:
        yield emit(f"❌ 文件筛选失败: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    # ── Step 3: Load paper context ───────────────────────────────────────────
    analysis_md = fm.load_analysis(arxiv_id) or ""
    metadata = fm.load_metadata(arxiv_id) or {}
    title = metadata.get("title", arxiv_id)

    # ── Step 4: Build prompts ────────────────────────────────────────────────
    human_prompt, system_prompt = build_prompts(
        files, readme, analysis_md, title, github_url, sections
    )

    # ── Step 5: Stream Claude ────────────────────────────────────────────────
    yield emit(f"Claude 分析中（分析内容：{'、'.join(sections)}）...")
    code_text = ""
    claude_error = ""

    async for event in stream_claude(human_prompt, system_prompt=system_prompt):
        ev_type = event.get("type", "")
        if ev_type == "assistant":
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text":
                    code_text += block.get("text", "")
        elif ev_type == "result":
            if event.get("is_error"):
                claude_error = event.get("result", "Claude 返回错误")
            else:
                result_text = event.get("result", "")
                if result_text:
                    code_text = result_text

    if claude_error:
        yield emit(f"❌ Claude 调用失败: {claude_error}")
        yield f"data: {json.dumps({'type': 'error', 'message': claude_error})}\n\n"
        return

    if not code_text.strip():
        yield emit("❌ 生成失败：Claude 没有返回内容")
        yield f"data: {json.dumps({'type': 'error', 'message': '生成失败'})}\n\n"
        return

    yield emit(f"✓ 代码解读生成完成（{len(code_text):,} 字符）")

    # ── Step 6: Save ─────────────────────────────────────────────────────────
    authors = metadata.get("authors", [])
    authors_str = ", ".join(authors[:5]) + (" 等" if len(authors) > 5 else "")
    frontmatter = f"""---
title: "{title.replace('"', "'")}"
arxiv: "{arxiv_id}"
authors: "{authors_str}"
github: "{github_url}"
sections: {json.dumps(sections, ensure_ascii=False)}
---

"""
    fm.save_code_analysis(arxiv_id, frontmatter + code_text)
    yield emit("✅ code_analyse.md 已保存")
    yield f"data: {json.dumps({'type': 'code_done', 'arxiv_id': arxiv_id})}\n\n"
