"""Claude Agent — 基于 claude CLI (Claude Code)，无需 ANTHROPIC_API_KEY"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from config import (
    CLAUDE_MODEL,
    PAPER_ANALYZER_SCRIPTS_DIR,
    PAPER_ANALYZER_STYLES_DIR,
    MINERU_TOKEN,
)
import arxiv_client as arxiv_mod
import file_manager as fm
from file_manager import get_images_dir

# ---------------------------------------------------------------------------
# claude CLI helpers
# ---------------------------------------------------------------------------

# Claude CLI 认证说明：
# - 必须用 dict(os.environ) + pop() 方式，而不是 dict comprehension（否则丢失认证上下文）
# - 只移除 CLAUDECODE（防止嵌套 session 报错）和 ANTHROPIC_API_KEY
# - 不能移除 http_proxy/https_proxy，否则破坏 SSE 端口认证
# - MinerU 的代理绕过通过 mineru_api.py 中的 session.proxies={} 单独处理


def _claude_env() -> Dict[str, str]:
    """构建 claude CLI 的子进程环境：继承完整父环境，只移除嵌套 session 冲突变量"""
    env = dict(os.environ)
    for key in ("CLAUDECODE", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_OLD"):
        env.pop(key, None)
    return env


def _build_args(
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    resume: bool = False,
    stream: bool = False,
) -> List[str]:
    output_fmt = "stream-json" if stream else "json"
    args = [
        "claude", "-p",
        "--output-format", output_fmt,
        "--dangerously-skip-permissions",
        "--model", CLAUDE_MODEL,
    ]
    if stream:
        args.append("--verbose")
    if resume and session_id:
        args += ["--resume", session_id]
    elif session_id:
        args += ["--session-id", session_id]
    if system_prompt and not resume:
        args += ["--append-system-prompt", system_prompt]
    return args
    if stream:
        args.append("--verbose")
    if resume and session_id:
        args += ["--resume", session_id]
    elif session_id:
        args += ["--session-id", session_id]
    if system_prompt and not resume:
        args += ["--append-system-prompt", system_prompt]
    # prompt 通过 stdin 传入，避免超出 ARG_MAX
    return args


def _parse_json_result(raw: str) -> Tuple[str, str]:
    """解析 --output-format json 的输出，返回 (text, session_id)"""
    raw = raw.strip()
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
        text = data.get("result", "")
        sid = data.get("session_id", "")
        return text, sid
    except json.JSONDecodeError:
        return raw, ""


def call_claude_sync(
    prompt: str,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    resume: bool = False,
) -> Tuple[str, str]:
    """同步调用 claude CLI，返回 (text, session_id)"""
    args = _build_args(system_prompt, session_id, resume, stream=False)
    result = subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
        env=_claude_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI 调用失败: {result.stderr[:500]}")
    return _parse_json_result(result.stdout)


async def stream_claude(
    prompt: str,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    resume: bool = False,
) -> AsyncGenerator[Dict, None]:
    """异步流式调用 claude CLI，yield 解析后的 stream-json 事件"""
    args = _build_args(system_prompt, session_id, resume, stream=True)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_claude_env(),
        limit=64 * 1024 * 1024,  # 64MB，防止大输出超过默认 64KB 行限制
    )
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
    await proc.wait()


# ---------------------------------------------------------------------------
# Style loader
# ---------------------------------------------------------------------------


def _load_style_content(style: str, formula: bool, code: bool) -> str:
    parts = []
    style_file = PAPER_ANALYZER_STYLES_DIR / f"{style}.md"
    if style_file.exists():
        parts.append(f"## 写作风格（{style}）\n\n{style_file.read_text(encoding='utf-8')}")
    if formula:
        f = PAPER_ANALYZER_STYLES_DIR / "with-formulas.md"
        if f.exists():
            parts.append(f"## 公式讲解指南\n\n{f.read_text(encoding='utf-8')}")
    if code:
        f = PAPER_ANALYZER_STYLES_DIR / "with-code.md"
        if f.exists():
            parts.append(f"## 代码分析指南\n\n{f.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


def _build_analysis_with_frontmatter(
    content: str, paper_info: Dict, tags: List[str], arxiv_id: str
) -> str:
    """在 analysis.md 前面加 Obsidian YAML frontmatter"""
    authors = paper_info.get("authors", [])
    authors_str = ", ".join(authors[:5]) + (" 等" if len(authors) > 5 else "")
    published = paper_info.get("published", "")

    tag_lines = "\n".join(f"  - {t}" for t in tags) if tags else "  - 论文"

    frontmatter = f"""---
title: "{paper_info.get('title', '').replace('"', "'")}"
arxiv: "{arxiv_id}"
authors: "{authors_str}"
published: "{published}"
tags:
{tag_lines}
---

"""
    # 如果 Claude 生成的内容已经有 frontmatter，去掉它
    if content.lstrip().startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].lstrip()

    return frontmatter + content


# ---------------------------------------------------------------------------
# Python-side tool execution (no LLM tool loop needed)
# ---------------------------------------------------------------------------


def step_download_pdf(arxiv_id: str, title: Optional[str] = None) -> str:
    paper_dir = fm.ensure_paper_dir(arxiv_id, title)
    pdf_path = paper_dir / "paper.pdf"
    if pdf_path.exists():
        if arxiv_mod.is_pdf_complete(pdf_path):
            return f"PDF 已存在 ({pdf_path.stat().st_size // 1024} KB)"
        else:
            print(f"PDF 不完整，重新下载: {pdf_path}")
            pdf_path.unlink()
    ok = arxiv_mod.download_pdf(arxiv_id, pdf_path)
    if ok:
        return f"PDF 下载成功 ({pdf_path.stat().st_size // 1024} KB)"
    raise RuntimeError("PDF 下载失败")


def step_parse_mineru(arxiv_id: str, pdf_url: Optional[str] = None) -> str:
    """使用 MinerU URL 模式解析论文（无需上传文件，秒级提交）"""
    if not MINERU_TOKEN:
        raise RuntimeError("未设置 MINERU_TOKEN 环境变量")

    full_md = fm.get_raw_dir(arxiv_id) / "full.md"
    if full_md.exists():
        return "已有解析结果，跳过 MinerU"

    # 确定 PDF URL：优先使用传入的，否则按 arxiv_id 构造
    if not pdf_url:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    sys.path.insert(0, str(PAPER_ANALYZER_SCRIPTS_DIR))
    from mineru_api import MinerUAPI

    api = MinerUAPI(MINERU_TOKEN)

    # 尝试恢复上次未完成的 task_id
    task_id_file = fm.get_paper_dir(arxiv_id) / "mineru_batch_id.txt"
    task_id = None
    if task_id_file.exists():
        saved = task_id_file.read_text().strip()
        try:
            data = api.get_url_task_result(saved)
            state = (data or {}).get("state", "")
            if state == "failed":
                print(f"上次 task_id={saved} 已失败，重新提交")
                task_id_file.unlink(missing_ok=True)
            elif state in ("done", "pending", "running"):
                task_id = saved
                print(f"恢复已有 task_id: {task_id} (state={state})")
        except Exception:
            task_id = saved

    if not task_id:
        print(f"提交 URL 给 MinerU: {pdf_url}")
        task_id = api.submit_task(pdf_url)
        if not task_id:
            raise RuntimeError("MinerU URL 任务提交失败")
        task_id_file.write_text(task_id)

    file_info = api.wait_for_url_task(task_id, max_wait=600, interval=10)
    if not file_info:
        task_id_file.unlink(missing_ok=True)
        raise RuntimeError("MinerU 解析失败或超时，请重试")

    with tempfile.TemporaryDirectory() as tmp:
        result = api.download_result(file_info, Path(tmp))
        if not result:
            raise RuntimeError("MinerU 结果下载失败")
        fm.copy_mineru_output(arxiv_id, Path(tmp))

    task_id_file.unlink(missing_ok=True)
    return "MinerU 解析完成（URL 模式）"


def step_extract_metadata(arxiv_id: str) -> Dict:
    raw_dir = fm.get_raw_dir(arxiv_id)
    md_files = list(raw_dir.glob("*.md"))
    if not md_files:
        raise RuntimeError("未找到 raw/full.md，请先执行 MinerU 解析")
    sys.path.insert(0, str(PAPER_ANALYZER_SCRIPTS_DIR))
    from extract_paper_info import extract_paper_info

    images_dir = raw_dir / "images"
    return extract_paper_info(md_files[0], images_dir)


# ---------------------------------------------------------------------------
# Analysis Agent (SSE streaming)
# ---------------------------------------------------------------------------


async def run_analysis_agent(
    arxiv_id: str,
    paper_info: Dict,
    style_config: Dict,
) -> AsyncGenerator[str, None]:
    """运行分析流程，yield SSE 格式消息"""

    def emit(msg: str) -> str:
        return f"data: {json.dumps({'type': 'progress', 'message': msg})}\n\n"

    style = style_config.get("style", "academic")
    formula = style_config.get("formula", False)
    code = style_config.get("code", False)
    tags = style_config.get("tags", [])

    # ── Step 1: Download PDF ──────────────────────────────────────────────
    yield emit("正在下载 PDF...")
    try:
        loop = asyncio.get_event_loop()
        title = paper_info.get("title")
        msg = await loop.run_in_executor(None, step_download_pdf, arxiv_id, title)
        yield emit(f"✓ {msg}")
    except Exception as e:
        yield emit(f"❌ PDF 下载失败: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    # ── Step 2: MinerU Parse ──────────────────────────────────────────────
    yield emit("MinerU 解析中（URL 模式，约60秒）...")
    try:
        pdf_url = paper_info.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}"
        msg = await loop.run_in_executor(None, step_parse_mineru, arxiv_id, pdf_url)
        yield emit(f"✓ {msg}")
    except Exception as e:
        yield emit(f"❌ MinerU 解析失败: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    # ── Step 3: Extract metadata ──────────────────────────────────────────
    yield emit("提取论文元数据...")
    try:
        paper_meta = await loop.run_in_executor(None, step_extract_metadata, arxiv_id)
        yield emit(f"✓ 提取到 {len(paper_meta.get('sections', []))} 个章节，{paper_meta.get('image_count', 0)} 张图片")
    except Exception as e:
        yield emit(f"⚠️ 元数据提取失败（继续生成）: {e}")
        paper_meta = {}

    # ── Step 4: Read paper content ────────────────────────────────────────
    paper_content = fm.load_raw_md(arxiv_id) or ""
    max_chars = 80000
    if len(paper_content) > max_chars:
        paper_content = paper_content[:max_chars] + "\n\n[内容已截断]"

    # ── Step 5: Generate analysis with claude CLI ─────────────────────────
    yield emit("正在生成分析文章（使用 Claude Code）...")

    # 获取实际可用的图片列表
    images_dir = get_images_dir(arxiv_id)
    available_images = []
    if images_dir.exists():
        available_images = [img.name for img in images_dir.glob("*.jpg") if img.is_file()]
        available_images.extend([img.name for img in images_dir.glob("*.png") if img.is_file()])

    images_info = ""
    if available_images:
        images_info = f"\n\n## 可用图片列表\n以下是MinerU从PDF中提取的实际图片文件（仅引用这些存在的文件）：\n"
        images_info += "\n".join(f"- images/{img}" for img in available_images)
        images_info += "\n\n**重要**：只能引用上述列表中的图片。如果原文中有公式或表格，它们已经以LaTeX或Markdown表格格式保留在论文内容中，无需引用图片。"
    else:
        images_info = "\n\n## 图片说明\n本论文没有提取到图片文件。如果原文中有公式或表格，它们以LaTeX或Markdown格式保留在论文内容中。"

    style_content = _load_style_content(style, formula, code)
    system_prompt = f"""你是一位专业的学术论文分析专家，负责将学术论文转化为高质量的技术分析文章。

## 论文信息
- arxiv_id: {arxiv_id}
- 标题: {paper_info.get("title", "Unknown")}
- 作者: {", ".join(paper_info.get("authors", [])[:5])}
- 摘要: {paper_info.get("abstract", "")[:500]}

## 风格配置
- 写作风格: {style}
- 公式讲解: {"是" if formula else "否"}
- 代码分析: {"是" if code else "否"}

{style_content}
{images_info}

## 通用写作原则
避免：AI 常用词（"深入探讨"、"至关重要"）、机械化标题、平铺直叙。
采用：自然段落叙述、充分利用图片（只引用上面列出的实际存在的图片文件）、每张关键图都讲解。

## 文章结构
1. 论文信息（标题/链接/作者）
2. 直觉引入（2-3段）
3. 背景知识（3-4段）
4. 核心创新（4-5段，含图片引用）
5. 实验验证（2-3段）
6. 深入分析（2-3段）
7. 思考与展望（1-2段）

**图片引用规则**：
- 只能引用"可用图片列表"中实际存在的文件
- 引用格式：![说明](images/图片文件名)
- 对于论文中的公式和表格，直接使用LaTeX或Markdown格式展示，不要尝试引用图片"""

    prompt = f"""请根据以下论文内容，生成完整的技术分析文章。

## 论文原文（MinerU 解析）

{paper_content}

---

请直接输出 Markdown 格式的分析文章，不要包含任何说明或前缀。"""

    analysis_text = ""
    final_session_id = ""

    claude_error = ""
    async for event in stream_claude(prompt, system_prompt=system_prompt):
        ev_type = event.get("type", "")

        if ev_type == "assistant":
            # 流式文本块
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    analysis_text += chunk

        elif ev_type == "result":
            # 检查是否出错
            if event.get("is_error"):
                claude_error = event.get("result", "Claude 返回错误")
            else:
                result_text = event.get("result", "")
                if result_text:
                    analysis_text = result_text
            final_session_id = event.get("session_id", "")

    if claude_error:
        yield emit(f"❌ Claude 调用失败: {claude_error}")
        yield f"data: {json.dumps({'type': 'error', 'message': claude_error})}\n\n"
        return

    if not analysis_text.strip():
        yield emit("❌ 生成失败：Claude 没有返回内容")
        yield f"data: {json.dumps({'type': 'error', 'message': '生成失败'})}\n\n"
        return

    yield emit(f"✓ 分析文章生成完成（{len(analysis_text)} 字符）")

    # ── Step 6: Save ──────────────────────────────────────────────────────
    # 拼 Obsidian YAML frontmatter（含 tags）
    analysis_with_frontmatter = _build_analysis_with_frontmatter(
        analysis_text, paper_info, tags, arxiv_id
    )
    fm.save_analysis(arxiv_id, analysis_with_frontmatter)
    metadata = {**paper_info, "style_config": style_config, "tags": tags}
    fm.save_metadata(arxiv_id, metadata)
    fm.move_paper_to_tag(arxiv_id, tags)

    # 保存 session_id 供后续 chat 复用
    if final_session_id:
        fm.save_session_id(arxiv_id, "analysis", final_session_id)

    yield emit("✅ 已保存到 vault")
    yield f"data: {json.dumps({'type': 'done', 'arxiv_id': arxiv_id})}\n\n"


# ---------------------------------------------------------------------------
# Chat Agent (SSE streaming)
# ---------------------------------------------------------------------------


async def run_chat_agent(
    arxiv_id: str,
    user_message: str,
    chat_history: List[Dict],
) -> AsyncGenerator[str, None]:
    """处理用户提问，yield SSE 流式回复"""

    metadata = fm.load_metadata(arxiv_id)
    analysis = fm.load_analysis(arxiv_id) or ""

    # 判断是否是修改指令
    is_update = any(kw in user_message for kw in ["修改", "添加", "删除", "改写", "补充", "在...后", "在...前"])

    # 取已有 chat session_id（支持多轮对话记忆）
    saved_session_id = fm.load_session_id(arxiv_id, "chat")
    is_resume = bool(saved_session_id and chat_history)

    if is_update:
        # ── 修改模式：重新生成 analysis ──────────────────────────────────
        prompt = (
            f"以下是当前的论文分析文章：\n\n{analysis}\n\n"
            f"用户的修改指令：{user_message}\n\n"
            "请按照指令修改文章，输出完整的修改后文章（只输出文章内容，不要加任何说明）："
        )
        full_text = ""
        async for event in stream_claude(prompt):
            ev_type = event.get("type", "")
            if ev_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        chunk = block.get("text", "")
                        full_text += chunk
                        yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
            elif ev_type == "result":
                result_text = event.get("result", "")
                if result_text:
                    full_text = result_text

        if full_text.strip():
            # 修改后保留原有 frontmatter（tags 不变）
            new_content = full_text.strip()
            existing = fm.load_analysis(arxiv_id) or ""
            if existing.lstrip().startswith("---"):
                end = existing.find("---", 3)
                if end != -1:
                    frontmatter = existing[: end + 3]
                    new_content = frontmatter + "\n\n" + new_content
            fm.save_analysis(arxiv_id, new_content)
            yield f"data: {json.dumps({'type': 'analysis_updated'})}\n\n"

    else:
        # ── 问答模式：多轮对话 ────────────────────────────────────────────
        system_prompt = None
        if not is_resume:
            # 首次对话：在 system prompt 里注入论文上下文
            system_prompt = (
                f"你是学术论文分析专家，正在帮助用户理解以下论文。\n\n"
                f"论文标题：{metadata.get('title', 'Unknown') if metadata else 'Unknown'}\n"
                f"作者：{', '.join(metadata.get('authors', [])[:5]) if metadata else ''}\n\n"
                f"当前分析文章摘要（前3000字）：\n{analysis[:3000]}"
            )

        full_text = ""
        new_session_id = saved_session_id if is_resume else str(uuid.uuid4())

        async for event in stream_claude(
            user_message,
            system_prompt=system_prompt,
            session_id=new_session_id,
            resume=is_resume,
        ):
            ev_type = event.get("type", "")
            if ev_type == "system" and event.get("subtype") == "init":
                # 保存服务端返回的 session_id
                sid = event.get("session_id", new_session_id)
                fm.save_session_id(arxiv_id, "chat", sid)

            elif ev_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        chunk = block.get("text", "")
                        full_text += chunk
                        yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"

            elif ev_type == "result":
                result_text = event.get("result", "")
                if result_text and not full_text.strip():
                    full_text = result_text
                    yield f"data: {json.dumps({'type': 'text', 'content': result_text})}\n\n"
                # 更新 session_id
                sid = event.get("session_id", "")
                if sid:
                    fm.save_session_id(arxiv_id, "chat", sid)

    # 保存对话记录
    fm.append_chat(arxiv_id, "user", user_message)
    fm.append_chat(arxiv_id, "assistant", full_text.strip() if "full_text" in dir() else "")

    yield f"data: {json.dumps({'type': 'done'})}\n\n"
