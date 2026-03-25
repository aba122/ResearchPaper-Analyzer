"""针对已解析论文重新生成更详细的分析文章"""

import asyncio
import json
import os
import sys
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

# 不移除代理变量：MinerU Python session 用 trust_env=False，curl 用代理上传 OSS

import file_manager as fm
from agent import stream_claude, _load_style_content, _build_analysis_with_frontmatter
from file_manager import get_images_dir


async def regen(arxiv_id: str, extra_instruction: str = ""):
    # 读取元数据
    metadata = fm.load_metadata(arxiv_id)
    if not metadata:
        raise RuntimeError(f"未找到论文: {arxiv_id}")

    title = metadata.get("title", "Unknown")
    style_config = metadata.get("style_config", {})
    style = style_config.get("style", "academic")
    formula = style_config.get("formula", False)
    code = style_config.get("code", False)
    tags = metadata.get("tags", [])

    print(f"\n{'='*60}")
    print(f"重新生成分析: {title[:60]}...")
    print(f"{'='*60}\n")

    # 读取原始论文内容（80K 字符，避免 context 溢出）
    raw_content = fm.load_raw_md(arxiv_id) or ""
    max_chars = 80000
    if len(raw_content) > max_chars:
        raw_content = raw_content[:max_chars] + "\n\n[内容已截断，以上为前80000字符]"
    print(f"论文内容: {len(raw_content)} 字符")

    # 图片列表
    images_dir = get_images_dir(arxiv_id)
    available_images = []
    if images_dir.exists():
        available_images = [img.name for img in images_dir.glob("*.jpg")]
        available_images.extend([img.name for img in images_dir.glob("*.png")])

    if available_images:
        images_info = "\n\n## 可用图片列表\n" + "\n".join(f"- images/{img}" for img in available_images)
        images_info += "\n\n**重要**：只能引用上述列表中的图片。"
    else:
        images_info = "\n\n## 图片说明\n本论文没有提取到图片文件。"

    style_content = _load_style_content(style, formula, code)

    system_prompt = f"""你是一位专业的学术论文分析专家，负责将学术论文转化为高质量的深度技术分析文章。

## 论文信息
- 标题: {title}

## 核心要求：每个章节必须详细展开

**这是最重要的要求**：每个技术章节（建模/Modeling、编码器/Encoder、解码器/Decoder、训练/Training等）必须进行深度展开，包括：

1. **建模范式（Modeling）**：
   - 详细描述每种建模策略（外部专家集成、模块化联合、端到端统一）
   - 对每种建模范式（自回归、扩散、混合）给出具体的技术细节
   - 列举代表性模型并分析其核心设计
   - 对比不同范式的优劣势，给出量化或定性分析

2. **编码器（Encoder）**：
   - 详细描述连续表示编码器（CLIP-ViT、SigLIP、InternViT等）的架构与特点
   - 详细描述离散表示编码器（VQVAE、VQGAN、SEED等）的量化机制
   - 混合编码策略的设计细节
   - 视频编码器和音频编码器的具体方法

3. **解码器（Decoder）**：
   - 自回归解码的具体实现（next-token prediction、扫描顺序等）
   - 扩散解码的技术细节（去噪过程、conditioning机制等）
   - 混合解码的设计方案
   - 各模态（图像/视频/音频）解码方法的差异

4. **训练策略（Training）**：
   - 统一预训练的具体目标函数和数据格式
   - 多阶段训练的设计逻辑
   - SFT/RLHF/DPO等对齐方法的具体应用
   - 数据管理（数据来源、过滤、构建）的详细方法

{style_content}
{images_info}

## 通用写作原则
- 每个章节至少 400-600 字，核心技术章节（建模/编码/解码/训练）每节至少 800 字
- 使用具体的公式、架构描述、代表性模型名称支撑论点
- 避免泛泛而谈，每个技术点都要给出具体细节
- 充分引用图片（仅限可用图片列表中的图片）

## 文章结构（严格遵循，每节详细展开）
1. 论文基本信息（标题/作者/机构）
2. 研究背景与动机（引入UFM的必要性，理解与生成的协同关系）
3. 统一任务形式化（形式化定义，任务集划分）
4. 建模范式详解（**重点**：三大范式，各范式技术细节，代表模型）
5. 编码器策略详解（**重点**：连续/离散/混合，各编码器设计细节）
6. 解码器策略详解（**重点**：自回归/扩散/混合，各模态解码方法）
7. 训练策略详解（**重点**：预训练目标/微调方法/对齐训练/数据管理）
8. 评估基准体系（理解/生成/混合基准分类）
9. 应用与挑战（机器人/自驾/医疗等，当前开放问题）
10. 未来展望

{extra_instruction}

**图片引用规则**：格式为 ![说明](images/文件名)，只引用可用图片列表中的文件。"""

    prompt = f"""请根据以下论文内容，生成完整、详细的技术分析文章。每个核心技术章节（建模、编码器、解码器、训练）必须深度展开，给出充分的技术细节。

## 论文原文（MinerU 解析）

{raw_content}

---

请直接输出 Markdown 格式的分析文章，不要包含任何说明或前缀。"""

    print("正在生成详细分析文章...")
    analysis_text = ""
    char_count = 0

    async for event in stream_claude(prompt, system_prompt=system_prompt):
        ev_type = event.get("type", "")
        if ev_type == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    analysis_text += chunk
                    char_count += len(chunk)
                    if char_count % 1000 == 0:
                        print(f"  已生成 {char_count} 字符...", end="\r")
        elif ev_type == "result":
            if event.get("is_error"):
                raise RuntimeError(f"Claude 返回错误: {event.get('result', '')}")
            # 只在流式未捕获到内容时，才用 result 字段兜底（避免覆盖已累积的完整文本）
            result_text = event.get("result", "")
            if result_text and len(analysis_text) < 200:
                analysis_text = result_text
            session_id = event.get("session_id", "")
            if session_id:
                fm.save_session_id(arxiv_id, "analysis", session_id)

    print(f"\n✓ 生成完成: {len(analysis_text)} 字符")

    # 保存（保留原有 frontmatter）
    paper_info = {
        "title": title,
        "authors": metadata.get("authors", []),
        "abstract": metadata.get("abstract", ""),
        "published": metadata.get("published", ""),
    }
    analysis_with_fm = _build_analysis_with_frontmatter(analysis_text, paper_info, tags, arxiv_id)
    fm.save_analysis(arxiv_id, analysis_with_fm)

    paper_dir = fm.get_paper_dir(arxiv_id)
    print(f"✓ 已保存到: {paper_dir}/analysis.md")
    print(f"\n{'='*60}")
    print(f"✅ 重新生成完成！({len(analysis_text)} 字符)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    arxiv_id = "local_A_Survey_of_Unified_Multimodal_Understanding_and_Generation-_Advances_and_Challenges"
    asyncio.run(regen(arxiv_id))
