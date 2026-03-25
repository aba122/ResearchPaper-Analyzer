"""FastAPI 入口 — 路由定义 + SSE 流式输出"""

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import arxiv_client as arxiv_mod
import file_manager as fm
from agent import run_analysis_agent, run_chat_agent

app = FastAPI(title="Paper Analyst API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    arxiv_id: str
    style: str = "academic"
    formula: bool = False
    code: bool = False
    github_url: str = ""
    code_sections: List[str] = ["模型架构", "训练流程", "主要创新"]
    tags: List[str] = []


class CodeAnalyzeRequest(BaseModel):
    github_url: str
    sections: List[str] = ["模型架构", "训练流程", "主要创新"]


class ChatRequest(BaseModel):
    message: str
    history: List[dict] = []


class UpdateRequest(BaseModel):
    instructions: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/search")
async def search(q: str = Query(..., description="搜索关键词")):
    """搜索 arxiv 论文"""
    try:
        results = arxiv_mod.search_arxiv(q, max_results=10)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/resolve")
async def resolve(url: str = Query(..., description="arxiv URL 或 ID")):
    """解析 arxiv URL，返回论文信息"""
    arxiv_id = arxiv_mod.parse_arxiv_id(url)
    if not arxiv_id:
        raise HTTPException(status_code=400, detail="无法解析 arxiv ID")
    info = arxiv_mod.get_paper_info(arxiv_id)
    if not info:
        raise HTTPException(status_code=404, detail="论文不存在")
    return info


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    """启动分析流程，返回 SSE 流"""
    arxiv_id = req.arxiv_id
    style_config = {
        "style": req.style,
        "formula": req.formula,
        "code": req.code,
        "tags": req.tags,
    }

    # Get paper info from arxiv
    paper_info = arxiv_mod.get_paper_info(arxiv_id)
    if not paper_info:
        raise HTTPException(status_code=404, detail=f"论文 {arxiv_id} 不存在")

    async def event_stream():
        async for chunk in run_analysis_agent(arxiv_id, paper_info, style_config):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/papers")
async def list_papers():
    """列出所有已分析论文"""
    papers = fm.list_papers()
    return {"papers": papers}


@app.get("/api/tags")
async def get_tags():
    """返回所有已使用的 tag 列表"""
    return {"tags": fm.all_tags()}


@app.get("/api/paper/{arxiv_id}")
async def get_paper(arxiv_id: str):
    """返回某篇论文的完整数据"""
    metadata = fm.load_metadata(arxiv_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="论文未找到")
    analysis = fm.load_analysis(arxiv_id)
    chat = fm.load_chat(arxiv_id)
    return {
        "metadata": metadata,
        "analysis": analysis or "",
        "chat": chat,
    }


@app.post("/api/paper/{arxiv_id}/chat")
async def chat(arxiv_id: str, req: ChatRequest):
    """向 Agent 发问，返回 SSE 流式回复"""
    metadata = fm.load_metadata(arxiv_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="论文未找到")

    async def event_stream():
        async for chunk in run_chat_agent(arxiv_id, req.message, req.history):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/paper/{arxiv_id}/update")
async def update_analysis(arxiv_id: str, req: UpdateRequest):
    """Agent 根据指令修改 analysis.md"""
    metadata = fm.load_metadata(arxiv_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="论文未找到")

    async def event_stream():
        async for chunk in run_chat_agent(
            arxiv_id, f"请修改分析文章：{req.instructions}", []
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/paper/{arxiv_id}/analyze-code")
async def analyze_code(arxiv_id: str, req: CodeAnalyzeRequest):
    """克隆 GitHub 仓库并生成代码解读，返回 SSE 流"""
    metadata = fm.load_metadata(arxiv_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="论文未找到，请先完成论文分析")

    from code_analyzer import run_code_analysis_agent

    async def event_stream():
        async for chunk in run_code_analysis_agent(arxiv_id, req.github_url, req.sections):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/paper/{arxiv_id}/code-analysis")
async def get_code_analysis(arxiv_id: str):
    """返回已保存的代码解读内容"""
    content = fm.load_code_analysis(arxiv_id)
    if content is None:
        raise HTTPException(status_code=404, detail="尚未进行代码分析")
    return {"content": content}


@app.get("/api/paper/{arxiv_id}/images/{filename:path}")
async def get_image(arxiv_id: str, filename: str):
    """静态服务 raw/images/ 下的图片"""
    image_path = fm.get_image_path(arxiv_id, filename)
    if image_path is None:
        raise HTTPException(status_code=404, detail="图片未找到")
    return FileResponse(str(image_path))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
