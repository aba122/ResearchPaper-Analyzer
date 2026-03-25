#!/usr/bin/env python3
"""
MinerU API 调用脚本 - 使用云端 API 解析 PDF
优势：精确度高、支持公式/表格、无需本地处理
"""

import os
import sys
import json
import time
import requests
import zipfile
import ssl
import urllib3
from pathlib import Path
from typing import Optional, Dict

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class MinerUAPI:
    """MinerU Cloud API 客户端"""

    BASE_URL = "https://mineru.net/api/v4"

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        # 禁用代理，避免 SSL 握手失败
        self.proxies = {}

        # 创建自定义 session，禁用 SSL 验证以解决握手问题
        self.session = requests.Session()
        self.session.verify = False  # 禁用 SSL 证书验证
        self.session.proxies = self.proxies
        self.session.trust_env = False  # 忽略系统代理环境变量（http_proxy/https_proxy）

    def submit_task(self, pdf_url: str,
                    enable_formula: bool = True,
                    enable_table: bool = True,
                    enable_ocr: bool = False,
                    language: str = "auto") -> Optional[str]:
        """提交 PDF 解析任务，返回 batch_id"""

        url = f"{self.BASE_URL}/extract/task"
        data = {
            "url": pdf_url,
            "enable_formula": enable_formula,
            "enable_table": enable_table,
            "enable_ocr": enable_ocr,
            "language": language
        }

        try:
            response = self.session.post(url, headers=self.headers, json=data, timeout=30)
            result = response.json()

            if result.get("code") == 0 and result.get("data"):
                # URL 模式返回 task_id，文件模式返回 batch_id，统一用同一字段
                batch_id = result["data"].get("batch_id") or result["data"].get("task_id")
                print(f"✓ 任务提交成功，batch_id: {batch_id}")
                return batch_id
            else:
                print(f"✗ 任务提交失败: {result.get('msg', '未知错误')}")
                return None

        except Exception as e:
            print(f"✗ 请求异常: {e}")
            return None

    def submit_task_file(self, pdf_path: Path,
                         enable_formula: bool = True,
                         enable_table: bool = True,
                         enable_ocr: bool = False,
                         language: str = "auto") -> Optional[str]:
        """上传本地 PDF 文件并提交解析任务

        流程：
        1. 调用 /file-urls/batch 获取签名上传 URL
        2. 用 PUT 方法上传文件到签名 URL（不设置 Content-Type）
        3. 上传完成后系统自动解析，返回 batch_id
        """

        try:
            # 第一步：获取签名上传 URL
            url = f"{self.BASE_URL}/file-urls/batch"
            data = {
                "enable_formula": enable_formula,
                "enable_table": enable_table,
                "enable_ocr": enable_ocr,
                "language": language,
                "files": [
                    {"name": pdf_path.name}
                ]
            }

            print(f"正在获取上传链接...")
            response = self.session.post(url, headers=self.headers, json=data, timeout=30)
            result = response.json()

            if result.get("code") != 0:
                print(f"✗ 获取上传链接失败: {result.get('msg', '未知错误')}")
                print(f"  响应: {result}")
                return None

            batch_id = result["data"].get("batch_id")
            file_urls = result["data"].get("file_urls", [])

            if not file_urls:
                print("✗ 未获取到上传链接")
                return None

            upload_url = file_urls[0]
            print(f"✓ 获取上传链接成功，batch_id: {batch_id}")

            # 第二步：用 PUT 上传文件（重要：不设置 Content-Type）
            # 使用 curl 上传（比 Python requests 更稳定，避免 SSL write timeout）
            print(f"正在上传文件 ({pdf_path.stat().st_size / 1024 / 1024:.1f} MB)...")
            import subprocess
            # 检测是否有可用代理（代理对国内 OSS 更稳定）
            proxy_addr = os.environ.get("https_proxy") or os.environ.get("http_proxy") or ""
            proxy_args = ["-x", proxy_addr] if proxy_addr else ["--noproxy", "*"]
            curl_result = subprocess.run(
                [
                    "curl", "-s", "-X", "PUT",
                    "-T", str(pdf_path),
                    *proxy_args,
                    "--max-time", "900",
                    "-w", "%{http_code}",
                    "-o", "/dev/null",
                    upload_url,
                ],
                capture_output=True,
                text=True,
                timeout=920,
            )
            http_code = curl_result.stdout.strip()
            if http_code not in ["200", "201"]:
                print(f"✗ 文件上传失败: HTTP {http_code}")
                if curl_result.stderr:
                    print(f"  curl stderr: {curl_result.stderr[:300]}")
                return None

            print(f"✓ 文件上传成功!")
            return batch_id

        except Exception as e:
            print(f"✗ 上传异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_url_task_result(self, task_id: str) -> Optional[Dict]:
        """查询 URL 模式任务结果（接口与文件模式不同）"""
        url = f"{self.BASE_URL}/extract/task/{task_id}"
        try:
            response = self.session.get(url, headers=self.headers, timeout=30)
            result = response.json()
            if result.get("code") == 0:
                return result.get("data")
            else:
                print(f"查询失败: {result.get('msg', '未知错误')}")
                return None
        except Exception as e:
            print(f"查询异常: {e}")
            return None

    def wait_for_url_task(self, task_id: str,
                          max_wait: int = 600,
                          interval: int = 10) -> Optional[Dict]:
        """轮询等待 URL 模式任务完成，返回含 full_zip_url 的 dict"""
        print(f"等待解析完成 (最长 {max_wait} 秒)...")
        start_time = time.time()
        while time.time() - start_time < max_wait:
            data = self.get_url_task_result(task_id)
            if data:
                state = data.get("state", "")
                if state == "done":
                    print("✓ 解析完成!")
                    return data  # 含 full_zip_url，download_result 可直接使用
                elif state == "failed":
                    print(f"✗ 解析失败: {data.get('err_msg', '未知')}")
                    return None
                else:
                    elapsed = int(time.time() - start_time)
                    print(f"  状态: {state}... ({elapsed}s)")
            time.sleep(interval)
        print("✗ 等待超时")
        return None

    def get_result(self, batch_id: str) -> Optional[Dict]:
        """查询解析结果"""

        url = f"{self.BASE_URL}/extract-results/batch/{batch_id}"

        try:
            response = self.session.get(url, headers=self.headers, timeout=30)
            result = response.json()

            if result.get("code") == 0:
                return result.get("data")
            else:
                print(f"查询失败: {result.get('msg', '未知错误')}")
                return None

        except Exception as e:
            print(f"查询异常: {e}")
            return None

    def wait_for_result(self, batch_id: str,
                        max_wait: int = 300,
                        interval: int = 5) -> Optional[Dict]:
        """轮询等待解析完成"""

        print(f"等待解析完成 (最长 {max_wait} 秒)...")
        start_time = time.time()

        while time.time() - start_time < max_wait:
            result = self.get_result(batch_id)

            if result:
                # 检查所有文件的状态
                files = result.get("extract_result", [])
                if files:
                    file_info = files[0]
                    state = file_info.get("state", "")

                    if state == "done":
                        print("✓ 解析完成!")
                        return file_info
                    elif state == "failed":
                        print(f"✗ 解析失败: {file_info.get('err_msg', '未知错误')}")
                        return None
                    else:
                        elapsed = int(time.time() - start_time)
                        print(f"  状态: {state}... ({elapsed}s)")

            time.sleep(interval)

        print("✗ 等待超时")
        return None

    def download_result(self, file_info: Dict, output_dir: Path) -> Dict:
        """下载解析结果"""

        output_dir.mkdir(parents=True, exist_ok=True)

        # 下载 ZIP 包
        zip_url = file_info.get("full_zip_url")
        if not zip_url:
            print("✗ 未找到下载链接")
            return {}

        print(f"下载结果: {zip_url}")

        zip_path = output_dir / "result.zip"
        try:
            response = self.session.get(zip_url, timeout=120)
            with open(zip_path, "wb") as f:
                f.write(response.content)
            print(f"✓ 下载完成: {zip_path}")

            # 解压
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(output_dir)
            print(f"✓ 解压完成: {output_dir}")

            # 查找 markdown 文件
            md_files = list(output_dir.glob("**/*.md"))
            images_dir = output_dir / "images"
            if not images_dir.exists():
                # 查找解压后的 images 目录
                for d in output_dir.rglob("images"):
                    if d.is_dir():
                        images_dir = d
                        break

            # 清理 zip
            zip_path.unlink()

            return {
                "method": "mineru_api",
                "markdown_path": str(md_files[0]) if md_files else None,
                "images_dir": str(images_dir) if images_dir.exists() else None,
                "image_count": len(list(images_dir.glob("*"))) if images_dir.exists() else 0
            }

        except Exception as e:
            print(f"✗ 下载/解压失败: {e}")
            return {}


def convert_pdf(pdf_path: str, output_dir: str, token: str) -> Dict:
    """主函数：转换 PDF"""

    pdf_path = Path(pdf_path).resolve()
    output_dir = Path(output_dir).resolve()

    if not pdf_path.exists():
        print(f"✗ PDF 文件不存在: {pdf_path}")
        return {}

    print(f"PDF: {pdf_path}")
    print(f"输出目录: {output_dir}")
    print("-" * 40)

    api = MinerUAPI(token)

    # 提交任务
    batch_id = api.submit_task_file(pdf_path)
    if not batch_id:
        return {}

    # 等待结果
    file_info = api.wait_for_result(batch_id)
    if not file_info:
        return {}

    # 下载结果
    result = api.download_result(file_info, output_dir)

    # 保存元信息
    if result:
        info_path = output_dir / "convert_info.json"
        with open(info_path, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n转换信息已保存: {info_path}")

    return result


def main():
    if len(sys.argv) < 3:
        print("用法: python mineru_api.py <pdf_path> <output_dir> [token]")
        print("\n参数:")
        print("  pdf_path   - PDF 文件路径")
        print("  output_dir - 输出目录")
        print("  token      - MinerU API Token (或设置环境变量 MINERU_TOKEN)")
        print("\n示例:")
        print("  python mineru_api.py paper.pdf ./output")
        print("  MINERU_TOKEN=xxx python mineru_api.py paper.pdf ./output")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_dir = sys.argv[2]
    token = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("MINERU_TOKEN", "")

    if not token:
        print("✗ 请提供 MinerU API Token")
        print("  方式1: python mineru_api.py paper.pdf ./output YOUR_TOKEN")
        print("  方式2: export MINERU_TOKEN=YOUR_TOKEN")
        sys.exit(1)

    result = convert_pdf(pdf_path, output_dir, token)

    if result:
        print("\n" + "=" * 40)
        print("转换成功!")
        print(f"  Markdown: {result.get('markdown_path')}")
        print(f"  图片数量: {result.get('image_count')}")
    else:
        print("\n转换失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
