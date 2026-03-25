"""arxiv 搜索 + PDF 下载"""

import concurrent.futures
import contextlib
import os
import arxiv
import requests
from pathlib import Path
from typing import List, Dict, Optional

# arxiv API 调用统一超时（秒）
_ARXIV_TIMEOUT = 60

# arxiv 代理环境变量键名
_PROXY_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")


@contextlib.contextmanager
def _bypass_proxy():
    """临时移除代理环境变量，让 arxiv/feedparser 直连（避免代理超时）"""
    saved = {k: os.environ.pop(k) for k in _PROXY_KEYS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _minimal_info(arxiv_id: str) -> Dict:
    """无法调用 arxiv API 时返回的最小信息（仅凭 ID 构造）"""
    return {
        "arxiv_id": arxiv_id,
        "title": arxiv_id,          # 分析时会从 MinerU 输出里补全
        "authors": [],
        "abstract": "",
        "published": "",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def _run_with_timeout(fn, timeout=_ARXIV_TIMEOUT):
    """在线程里跑 fn，超时抛 TimeoutError"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=timeout)


def _run_with_retry(fn, retries=3, backoff=8, timeout=_ARXIV_TIMEOUT):
    """带退避重试，自动处理 429 和超时"""
    import time
    last_exc = None
    for i in range(retries):
        try:
            return _run_with_timeout(fn, timeout)
        except Exception as e:
            last_exc = e
            msg = str(e)
            is_retryable = "429" in msg or "Too Many" in msg or isinstance(e, concurrent.futures.TimeoutError)
            if i < retries - 1 and is_retryable:
                wait = backoff * (i + 1)
                print(f"  arxiv 429/timeout，{wait}s 后重试 ({i+1}/{retries-1})...")
                time.sleep(wait)
                continue
            raise
    raise last_exc


def search_arxiv(query: str, max_results: int = 10) -> List[Dict]:
    """搜索 arxiv 论文，429 / 超时时返回空列表"""
    def _do():
        with _bypass_proxy():
            client = arxiv.Client(num_retries=1)
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.Relevance,
            )
            results = []
            for paper in client.results(search):
                arxiv_id = paper.entry_id.split("/abs/")[-1]
                arxiv_id = arxiv_id.split("v")[0] if "v" in arxiv_id.split("/")[-1] else arxiv_id
                results.append({
                    "arxiv_id": arxiv_id,
                    "title": paper.title,
                    "authors": [a.name for a in paper.authors],
                    "abstract": paper.summary[:500] + "..." if len(paper.summary) > 500 else paper.summary,
                    "published": paper.published.strftime("%Y-%m-%d"),
                    "pdf_url": paper.pdf_url,
                    "arxiv_url": paper.entry_id,
                })
            return results

    try:
        return _run_with_retry(_do)
    except Exception as e:
        print(f"arxiv search failed: {e}")
        return []


def get_paper_info(arxiv_id: str) -> Optional[Dict]:
    """通过 arxiv_id 获取论文信息；429 / 超时时降级为最小信息"""
    def _do():
        with _bypass_proxy():
            client = arxiv.Client(num_retries=0)
            search = arxiv.Search(id_list=[arxiv_id])
            papers = list(client.results(search))
            if not papers:
                return None
            paper = papers[0]
            return {
                "arxiv_id": arxiv_id,
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "abstract": paper.summary,
                "published": paper.published.strftime("%Y-%m-%d"),
                "pdf_url": paper.pdf_url,
                "arxiv_url": paper.entry_id,
            }

    try:
        # 只尝试一次，失败立即降级（标题会从 MinerU 解析结果补全）
        result = _run_with_timeout(_do, timeout=15)
        return result if result else _minimal_info(arxiv_id)
    except Exception as e:
        print(f"arxiv get_paper_info failed ({e}), using minimal info")
        return _minimal_info(arxiv_id)


def parse_arxiv_id(url_or_id: str) -> Optional[str]:
    """从 URL 或 ID 字符串提取 arxiv_id"""
    url_or_id = url_or_id.strip()
    if "arxiv.org" in url_or_id:
        for part in ["abs/", "pdf/"]:
            if part in url_or_id:
                return url_or_id.split(part)[-1].split("v")[0].rstrip("/")
    if url_or_id and "." in url_or_id:
        return url_or_id.split("v")[0]
    return None


def is_pdf_complete(pdf_path: Path) -> bool:
    """检查 PDF 文件是否完整（包含 %%EOF 结尾标记）"""
    if not pdf_path.exists() or pdf_path.stat().st_size < 100:
        return False
    try:
        with open(pdf_path, "rb") as f:
            f.seek(-64, 2)
            return b"%%EOF" in f.read()
    except Exception:
        return False


def download_pdf(arxiv_id: str, output_path: Path) -> bool:
    """下载 arxiv PDF 到指定路径，并验证文件完整性"""
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        response = requests.get(pdf_url, timeout=120, stream=True, proxies={})
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        if not is_pdf_complete(output_path):
            print(f"PDF incomplete (missing %%EOF): {output_path}")
            output_path.unlink(missing_ok=True)
            return False
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        output_path.unlink(missing_ok=True)
        return False
