"""分析论文脚本：支持本地 PDF 或公开 URL 两种模式

用法：
  # 模式1：本地 PDF（需要上传，速度慢）
  analyze_local_pdf(pdf_path=Path("xxx.pdf"), title="...", ...)

  # 模式2：公开 URL（直接提交给 MinerU，秒级，推荐）
  analyze_url(pdf_url="https://arxiv.org/pdf/2501.xxxxx", title="...", ...)
"""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 加载 .env
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

backend_dir = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(Path("/Users/wulinxie/.claude/skills/paper-analyzer/scripts")))

# 注意：不移除代理变量
# - MinerU Python session 用 trust_env=False 忽略代理
# - curl 子进程上传 OSS 时读取代理（更稳定）

import file_manager as fm
from agent import (
    step_parse_mineru,
    step_extract_metadata,
    _load_style_content,
    _build_analysis_with_frontmatter,
    stream_claude,
)
from file_manager import get_images_dir
from config import MINERU_TOKEN


def _download_pdf(url: str, dest: Path) -> int:
    """从公开 URL 下载 PDF，返回文件大小（KB）"""
    import urllib.request
    print(f"  下载 PDF: {url}")
    # 走代理下载（arxiv 需要）
    proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy") or ""
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    with opener.open(url, timeout=120) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data) // 1024


def _step_parse_mineru_url(arxiv_id: str, pdf_url: str) -> str:
    """用公开 URL 直接提交 MinerU，跳过文件上传"""
    if not MINERU_TOKEN:
        raise RuntimeError("未设置 MINERU_TOKEN")

    full_md = fm.get_raw_dir(arxiv_id) / "full.md"
    if full_md.exists():
        return "已有解析结果，跳过 MinerU"

    from mineru_api import MinerUAPI

    api = MinerUAPI(MINERU_TOKEN)

    # 直接提交 URL（无需上传文件，秒级返回 task_id）
    print(f"  提交 URL 给 MinerU: {pdf_url[:80]}...")
    task_id = api.submit_task(pdf_url)
    if not task_id:
        raise RuntimeError("MinerU URL 任务提交失败")

    task_id_file = fm.get_paper_dir(arxiv_id) / "mineru_batch_id.txt"
    task_id_file.write_text(task_id)

    # 轮询等待（URL 模式用专用接口）
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


async def _run_analysis(arxiv_id: str, title: str, style: str, formula: bool,
                        code: bool, tags: list, paper_info: dict):
    """通用分析流程（从 Step3 开始，PDF 已就位）"""

    # Step 3: 元数据
    print("\nStep 3: 提取论文元数据...")
    try:
        loop = asyncio.get_event_loop()
        paper_meta = await loop.run_in_executor(None, step_extract_metadata, arxiv_id)
        print(f"  ✓ {len(paper_meta.get('sections', []))} 个章节，{paper_meta.get('image_count', 0)} 张图片")
    except Exception as e:
        print(f"  ⚠️ 元数据提取失败（继续）: {e}")

    # Step 4: 读取内容
    print("\nStep 4: 读取解析内容...")
    paper_content = fm.load_raw_md(arxiv_id) or ""
    if len(paper_content) > 80000:
        paper_content = paper_content[:80000] + "\n\n[内容已截断]"
    print(f"  ✓ {len(paper_content)} 字符")

    # 从内容中识别标题
    for line in paper_content.splitlines():
        s = line.strip()
        if s.startswith("# ") and len(s) > 5:
            paper_info["title"] = s[2:].strip()
            title = paper_info["title"]
            print(f"  ✓ 识别标题: {title[:60]}")
            break

    # Step 5: 生成分析
    print("\nStep 5: 生成分析文章（Claude Code）...")
    images_dir = get_images_dir(arxiv_id)
    available_images = []
    if images_dir.exists():
        available_images = [f.name for f in images_dir.glob("*.jpg")]
        available_images += [f.name for f in images_dir.glob("*.png")]

    images_info = (
        "\n\n## 可用图片\n" + "\n".join(f"- images/{i}" for i in available_images)
        + "\n\n**只能引用上述图片。**"
        if available_images
        else "\n\n## 图片说明\n无图片文件，公式/表格用 LaTeX/Markdown 展示。"
    )

    style_content = _load_style_content(style, formula, code)
    system_prompt = f"""你是专业学术论文分析专家，将论文转化为高质量技术分析文章。

## 论文信息
- 标题: {title}
- 写作风格: {style} | 公式: {"是" if formula else "否"} | 代码: {"是" if code else "否"}

{style_content}
{images_info}

## 写作原则
避免 AI 口头禅，采用自然段落叙述，每张关键图都讲解。

## 结构
1. 论文信息（标题/作者/机构）
2. 直觉引入（2-3段）
3. 背景（3-4段）
4. 核心创新（4-5段，引用图片）
5. 实验验证（2-3段）
6. 深入分析（2-3段）
7. 展望（1-2段）

图片引用格式：![说明](images/文件名)，只引用可用图片列表中的文件。"""

    prompt = f"请根据以下论文内容生成完整技术分析文章，直接输出 Markdown。\n\n{paper_content}\n\n---\n直接输出，不要前缀。"

    analysis_text = ""
    final_session_id = ""
    char_count = 0

    async for event in stream_claude(prompt, system_prompt=system_prompt):
        ev_type = event.get("type", "")
        if ev_type == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    analysis_text += chunk
                    char_count += len(chunk)
                    print(f"  ... {char_count} 字符", end="\r")
        elif ev_type == "result":
            if event.get("is_error"):
                raise RuntimeError(f"Claude 错误: {event.get('result', '')}")
            result_text = event.get("result", "")
            if result_text and len(analysis_text) < 200:
                analysis_text = result_text
            final_session_id = event.get("session_id", "")

    if not analysis_text.strip():
        raise RuntimeError("Claude 没有返回内容")
    print(f"\n  ✓ 生成完成（{len(analysis_text)} 字符）")

    # Step 6: 保存
    print("\nStep 6: 保存到 vault...")
    content = _build_analysis_with_frontmatter(analysis_text, paper_info, tags, arxiv_id)
    fm.save_analysis(arxiv_id, content)
    fm.save_metadata(arxiv_id, {**paper_info, "style_config": {"style": style, "formula": formula, "code": code}, "tags": tags})
    if final_session_id:
        fm.save_session_id(arxiv_id, "analysis", final_session_id)

    final_dir = fm.get_paper_dir(arxiv_id)
    print(f"  ✓ 已保存: {final_dir}")
    print(f"\n{'='*60}")
    print(f"✅ 完成！标题: {title[:60]}")
    print(f"   路径: {final_dir}")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────────────────────

async def analyze_url(
    pdf_url: str,
    title: str,
    style: str = "academic",
    formula: bool = False,
    code: bool = False,
    tags: list = None,
):
    """URL 模式：直接提交给 MinerU，同时下载 PDF 到 Obsidian（推荐）"""
    tags = tags or []
    # 用 URL 中的文件名或 title 生成 ID
    url_stem = pdf_url.rstrip("/").split("/")[-1].replace(".pdf", "")
    arxiv_id = f"url_{url_stem}"

    print(f"\n{'='*60}")
    print(f"URL 模式: {pdf_url[:70]}...")
    print(f"写作风格: {style} | 标签: {tags}")
    print(f"{'='*60}\n")

    paper_dir = fm.ensure_paper_dir(arxiv_id, title)
    paper_info = {"title": title, "authors": [], "abstract": "", "published": "", "arxiv_id": arxiv_id}

    # Step 1: 下载 PDF 到 vault（与 MinerU 提交并行感知上，先下载后提交）
    dest_pdf = paper_dir / "paper.pdf"
    if not dest_pdf.exists():
        print("Step 1: 下载 PDF 到 vault...")
        try:
            loop = asyncio.get_event_loop()
            kb = await loop.run_in_executor(None, _download_pdf, pdf_url, dest_pdf)
            print(f"  ✓ PDF 下载完成 ({kb} KB)")
        except Exception as e:
            print(f"  ⚠️ PDF 下载失败（继续分析）: {e}")
    else:
        print("Step 1: PDF 已存在，跳过下载")

    # Step 2: MinerU URL 模式（直接提交，无需上传）
    print("\nStep 2: 提交给 MinerU（URL 模式，无需上传文件）...")
    try:
        loop = asyncio.get_event_loop()
        msg = await loop.run_in_executor(None, _step_parse_mineru_url, arxiv_id, pdf_url)
        print(f"  ✓ {msg}")
    except Exception as e:
        print(f"  ❌ MinerU 失败: {e}")
        raise

    await _run_analysis(arxiv_id, title, style, formula, code, tags, paper_info)


async def analyze_local_pdf(
    pdf_path: Path,
    title: str,
    style: str = "academic",
    formula: bool = False,
    code: bool = False,
    tags: list = None,
):
    """本地 PDF 模式：上传文件到 MinerU（网速慢时较慢）"""
    tags = tags or []
    arxiv_id = f"local_{pdf_path.stem.replace(' ', '_')}"

    print(f"\n{'='*60}")
    print(f"本地模式: {pdf_path.name}")
    print(f"写作风格: {style} | 标签: {tags}")
    print(f"{'='*60}\n")

    paper_info = {"title": title, "authors": [], "abstract": "", "published": "", "arxiv_id": arxiv_id}

    # Step 1: 复制 PDF
    print("Step 1: 复制 PDF 到 vault...")
    paper_dir = fm.ensure_paper_dir(arxiv_id, title)
    dest_pdf = paper_dir / "paper.pdf"
    if not dest_pdf.exists():
        shutil.copy2(pdf_path, dest_pdf)
        print(f"  ✓ 已复制 ({dest_pdf.stat().st_size // 1024} KB)")
    else:
        print(f"  ✓ 已存在")

    # Step 2: MinerU 文件上传
    print("\nStep 2: MinerU 解析（文件上传模式）...")
    loop = asyncio.get_event_loop()
    msg = await loop.run_in_executor(None, step_parse_mineru, arxiv_id)
    print(f"  ✓ {msg}")

    await _run_analysis(arxiv_id, title, style, formula, code, tags, paper_info)


# ─────────────────────────────────────────────────────────────
# 入口：在这里配置要分析的论文
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── 示例1：arxiv 论文（URL 模式，推荐）──────────────────────
    # asyncio.run(analyze_url(
    #     pdf_url="https://arxiv.org/pdf/2501.09999",
    #     title="论文标题",
    #     style="academic",
    #     tags=["标签"],
    # ))

    # ── 示例2：本地 PDF（文件上传模式）─────────────────────────
    # asyncio.run(analyze_local_pdf(
    #     pdf_path=Path("/Users/wulinxie/Desktop/UPDGD-Net.pdf"),
    #     title="UPDGD-Net",
    #     style="academic",
    #     tags=["Multi-View"],
    # ))

    # ── Kimi K2（URL 模式，推荐）────────────────────────────────
    asyncio.run(analyze_url(
        pdf_url="https://arxiv.org/pdf/2507.20534",
        title="Kimi K2 Open Agentic Intelligence",
        style="academic",
        tags=["LLM", "Agent"],
    ))
