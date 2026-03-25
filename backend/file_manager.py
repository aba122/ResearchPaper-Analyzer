"""Vault 文件读写操作"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import VAULT_PAPERS_DIR

# ---------------------------------------------------------------------------
# arxiv_id → 文件夹名 索引
# ---------------------------------------------------------------------------

def _index_path() -> Path:
    return VAULT_PAPERS_DIR / "index.json"

def _load_index() -> Dict[str, str]:
    p = _index_path()
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}

def _save_index(index: Dict[str, str]) -> None:
    VAULT_PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_index_path(), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

def _sanitize_title(title: str) -> str:
    """将论文标题转为合法文件夹名"""
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '', title)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:80].rstrip() if len(name) > 80 else name or "Unknown Paper"

# ---------------------------------------------------------------------------
# 核心路径解析
# ---------------------------------------------------------------------------

def get_paper_dir(arxiv_id: str) -> Path:
    """通过 arxiv_id 找到对应的文件夹（优先查 index，兼容旧 arxiv_id 命名）"""
    index = _load_index()
    if arxiv_id in index:
        return VAULT_PAPERS_DIR / index[arxiv_id]
    # 兼容旧版：文件夹直接以 arxiv_id 命名
    legacy = VAULT_PAPERS_DIR / arxiv_id
    if legacy.exists():
        return legacy
    # 兜底：返回 arxiv_id 路径（待 ensure_paper_dir 创建）
    return VAULT_PAPERS_DIR / arxiv_id

def get_raw_dir(arxiv_id: str) -> Path:
    return get_paper_dir(arxiv_id) / "raw"

def get_images_dir(arxiv_id: str) -> Path:
    return get_raw_dir(arxiv_id) / "images"

def ensure_paper_dir(arxiv_id: str, title: Optional[str] = None) -> Path:
    """创建或获取论文目录。首次创建时用 title 命名，后续复用已有目录。"""
    index = _load_index()

    if arxiv_id in index:
        paper_dir = VAULT_PAPERS_DIR / index[arxiv_id]
    elif title:
        folder_name = _sanitize_title(title)
        # 防止重名
        candidate = VAULT_PAPERS_DIR / folder_name
        suffix = 1
        while candidate.exists() and not (candidate / "metadata.json").exists():
            candidate = VAULT_PAPERS_DIR / f"{folder_name} ({suffix})"
            suffix += 1
        paper_dir = candidate
        index[arxiv_id] = str(paper_dir.relative_to(VAULT_PAPERS_DIR))
        _save_index(index)
    else:
        paper_dir = VAULT_PAPERS_DIR / arxiv_id

    (paper_dir / "raw" / "images").mkdir(parents=True, exist_ok=True)
    return paper_dir

# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------

def save_metadata(arxiv_id: str, metadata: Dict) -> None:
    paper_dir = ensure_paper_dir(arxiv_id)
    metadata["analyzed_at"] = datetime.now().isoformat()
    metadata["arxiv_id"] = arxiv_id
    with open(paper_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

def load_metadata(arxiv_id: str) -> Optional[Dict]:
    path = get_paper_dir(arxiv_id) / "metadata.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Analysis / Chat
# ---------------------------------------------------------------------------

def save_analysis(arxiv_id: str, content: str) -> None:
    paper_dir = ensure_paper_dir(arxiv_id)
    with open(paper_dir / "analysis.md", "w", encoding="utf-8") as f:
        f.write(content)

def load_analysis(arxiv_id: str) -> Optional[str]:
    path = get_paper_dir(arxiv_id) / "analysis.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")

def load_raw_md(arxiv_id: str) -> Optional[str]:
    raw_dir = get_raw_dir(arxiv_id)
    md_files = list(raw_dir.glob("*.md"))
    if not md_files:
        return None
    full_md = raw_dir / "full.md"
    target = full_md if full_md.exists() else md_files[0]
    return target.read_text(encoding="utf-8")

def append_chat(arxiv_id: str, role: str, content: str) -> None:
    paper_dir = ensure_paper_dir(arxiv_id)
    chat_path = paper_dir / "chat.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(chat_path, "a", encoding="utf-8") as f:
        label = "用户" if role == "user" else "Assistant"
        f.write(f"\n\n## {label} [{timestamp}]\n\n{content}\n")

def load_chat(arxiv_id: str) -> str:
    path = get_paper_dir(arxiv_id) / "chat.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 论文库列表
# ---------------------------------------------------------------------------

def list_papers() -> List[Dict]:
    papers = []
    if not VAULT_PAPERS_DIR.exists():
        return papers
    index = _load_index()

    # 收集所有论文目录：直接在 papers/ 下的，以及一级子目录（tag 文件夹）内的
    all_paper_dirs = []
    for entry in VAULT_PAPERS_DIR.iterdir():
        if not entry.is_dir() or entry.name.startswith('.'):
            continue
        if (entry / "metadata.json").exists():
            all_paper_dirs.append(entry)
        else:
            # 可能是 tag 子目录，遍历其中的论文
            for sub in entry.iterdir():
                if sub.is_dir() and (sub / "metadata.json").exists():
                    all_paper_dirs.append(sub)

    all_paper_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # 反向映射：相对路径 → arxiv_id
    folder_to_id = {v: k for k, v in index.items()}

    for paper_dir in all_paper_dirs:
        meta_path = paper_dir / "metadata.json"
        if not meta_path.exists():
            continue
        with open(meta_path, encoding="utf-8") as f:
            metadata = json.load(f)
        rel_path = str(paper_dir.relative_to(VAULT_PAPERS_DIR))
        arxiv_id = metadata.get("arxiv_id") or folder_to_id.get(rel_path) or folder_to_id.get(paper_dir.name, paper_dir.name)
        papers.append({
            "arxiv_id": arxiv_id,
            "title": metadata.get("title", "Unknown"),
            "authors": metadata.get("authors", []),
            "tags": metadata.get("tags", []),
            "analyzed_at": metadata.get("analyzed_at", ""),
            "style": metadata.get("style_config", {}).get("style", "academic"),
        })
    return papers

# ---------------------------------------------------------------------------
# 图片 / Session
# ---------------------------------------------------------------------------

def get_image_path(arxiv_id: str, filename: str) -> Optional[Path]:
    images_dir = get_images_dir(arxiv_id)
    direct = images_dir / filename
    if direct.exists():
        return direct
    for p in images_dir.parent.rglob(filename):
        return p
    return None

def copy_mineru_output(arxiv_id: str, mineru_output_dir: Path) -> None:
    raw_dir = get_raw_dir(arxiv_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for md_file in mineru_output_dir.glob("*.md"):
        shutil.copy2(md_file, raw_dir / "full.md")
    src_images = mineru_output_dir / "images"
    if src_images.exists():
        dest_images = raw_dir / "images"
        if dest_images.exists():
            shutil.rmtree(dest_images)
        shutil.copytree(src_images, dest_images)

def move_paper_to_tag(arxiv_id: str, tags: List[str]) -> Path:
    """分析完成后，将论文目录移动到对应 tag 子目录下。"""
    paper_dir = get_paper_dir(arxiv_id)
    primary_tag = tags[0] if tags else "Untagged"
    tag_dir = VAULT_PAPERS_DIR / primary_tag

    # 已经在正确的 tag 目录下，无需移动
    if paper_dir.parent == tag_dir:
        return paper_dir

    tag_dir.mkdir(exist_ok=True)
    target = tag_dir / paper_dir.name
    shutil.move(str(paper_dir), str(target))

    index = _load_index()
    index[arxiv_id] = str(target.relative_to(VAULT_PAPERS_DIR))
    _save_index(index)
    return target


def all_tags() -> List[str]:
    """返回所有论文中已使用的 tag 列表（去重排序）。"""
    tags: set = set()
    for paper in list_papers():
        tags.update(paper.get("tags", []))
    return sorted(tags)


def save_session_id(arxiv_id: str, scope: str, session_id: str) -> None:
    path = get_paper_dir(arxiv_id) / f"{scope}_session_id.txt"
    path.write_text(session_id, encoding="utf-8")

def load_session_id(arxiv_id: str, scope: str) -> Optional[str]:
    path = get_paper_dir(arxiv_id) / f"{scope}_session_id.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def get_code_dir(arxiv_id: str) -> Path:
    return get_paper_dir(arxiv_id) / "code"


def save_code_analysis(arxiv_id: str, content: str) -> None:
    paper_dir = ensure_paper_dir(arxiv_id)
    with open(paper_dir / "code_analyse.md", "w", encoding="utf-8") as f:
        f.write(content)


def load_code_analysis(arxiv_id: str) -> Optional[str]:
    path = get_paper_dir(arxiv_id) / "code_analyse.md"
    return path.read_text(encoding="utf-8") if path.exists() else None
