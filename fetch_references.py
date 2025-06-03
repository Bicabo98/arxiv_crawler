#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
获取论文引用和参考文献的独立脚本

此脚本从papers_for_references.json中读取论文信息，
通过Semantic Scholar API获取引用和参考文献，
然后将结果保存到本地缓存文件。

与主爬虫流程解耦，可以单独运行，避免API限流影响主爬虫流程。
"""

import json
import time
import requests
import os
from pathlib import Path
import traceback
from rich.console import Console
from rich.progress import Progress

# 配置信息
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"  # Semantic Scholar API基础URL
RETRY_INTERVAL = 20  # 遇到429错误时的重试间隔（秒）
MAX_RETRIES = 150  # 最大重试次数
PAPER_INTERVAL = 5  # 处理下一篇论文前等待的时间（秒）
API_REQUEST_INTERVAL = 2  # 同一篇论文内两次API请求之间的间隔（秒）

# 初始化控制台
console = Console()

def ensure_paper_rate_limit():
    """确保不同论文之间的请求间隔"""
    time.sleep(PAPER_INTERVAL)

def ensure_api_rate_limit():
    """确保同一篇论文内API请求之间的间隔"""
    time.sleep(API_REQUEST_INTERVAL)

def safe_api_call(url, params=None, method='GET', data=None, retry_count=0):
    """安全调用API，处理429错误
    
    Args:
        url (str): API URL
        params (dict, optional): URL参数
        method (str, optional): HTTP方法，默认为'GET'
        data (dict, optional): POST请求数据
        retry_count (int, optional): 当前重试次数
    
    Returns:
        dict or None: API响应数据，如果请求失败则返回None
    """
    if retry_count >= MAX_RETRIES:
        console.log(f"[red]达到最大重试次数({MAX_RETRIES})，放弃请求: {url}")
        return None
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, params=params)
        elif method.upper() == 'POST':
            response = requests.post(url, params=params, json=data)
        else:
            console.log(f"[red]不支持的HTTP方法: {method}")
            return None
        
        # 处理429错误（请求过多）
        if response.status_code == 429:
            retry_count += 1
            console.log(f"[yellow]API限流(429)，等待{RETRY_INTERVAL}秒后重试... (尝试 {retry_count}/{MAX_RETRIES})")
            time.sleep(RETRY_INTERVAL)
            return safe_api_call(url, params, method, data, retry_count)
        
        # 如果请求成功
        if response.status_code == 200:
            return response.json()
        else:
            console.log(f"[red]API请求失败，状态码: {response.status_code}，URL: {url}")
            console.log(f"[red]响应内容: {response.text}")
            return None
        
    except Exception as e:
        console.log(f"[red]API请求出错: {str(e)}")
        return None

def get_paper_details_from_semantic_scholar(paper_title):
    """从Semantic Scholar获取论文详情，包括引用和参考文献"""
    # 1. 首先通过match API查找论文ID
    match_url = f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search/match"
    match_params = {"query": paper_title}
    
    match_data = safe_api_call(match_url, match_params)
    
    if not match_data or "data" not in match_data or not match_data["data"]:
        console.log(f"[yellow]未找到匹配的论文: {paper_title}")
        return None, None, None
    
    paper_id = match_data["data"][0]["paperId"]
    console.log(f"[green]找到匹配的论文ID: {paper_id}")
    
    # 确保API请求之间有间隔
    ensure_api_rate_limit()
    
    # 2. 使用获取到的ID获取完整论文信息
    paper_details_url = f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/{paper_id}"
    fields = "title,authors,abstract,citationCount,referenceCount,year,venue,fieldsOfStudy,openAccessPdf,s2FieldsOfStudy,citations,references"
    details_params = {
        "fields": fields,
        #"id": paper_id
    }
    
    paper_details = safe_api_call(paper_details_url, details_params)
    
    if not paper_details:
        console.log(f"[yellow]未能获取论文详情: {paper_id}")
        return paper_id, None, None
    
    # 3. 从返回结果中提取参考文献和引用
    references_list = []
    citations_list = []
    
    # 处理参考文献
    if "references" in paper_details and paper_details["references"]:
        for ref in paper_details["references"]:
            if ref and "title" in ref:
                reference = {
                    "external_id": ref.get("paperId", ""),
                    "title": ref.get("title", "无标题"),
                    "doi": "",  # Semantic Scholar API没有直接返回DOI
                    "source": "semantic_scholar",
                    "publication": "",
                    "data_source": "semantic_scholar",
                    "year": ""
                }
                references_list.append(reference)
    
    # 处理引用
    if "citations" in paper_details and paper_details["citations"]:
        for cit in paper_details["citations"]:
            if cit and "title" in cit:
                citation = {
                    "external_id": cit.get("paperId", ""),
                    "title": cit.get("title", "无标题"),
                    "doi": "",  # Semantic Scholar API没有直接返回DOI
                    "source": "semantic_scholar",
                    "publication": "",
                    "data_source": "semantic_scholar",
                    "year": ""
                }
                citations_list.append(citation)
    
    return paper_id, references_list, citations_list

def save_to_cache(arxiv_id, paper_uuid, paper_title, semantic_scholar_id, references_list, citations_list):
    """保存参考文献和引用到缓存"""
    # 创建缓存目录
    cache_dir = Path("./cache/references")
    cache_dir.mkdir(exist_ok=True, parents=True)
    
    citations_cache_dir = Path("./cache/citations")
    citations_cache_dir.mkdir(exist_ok=True, parents=True)
    
    # 参考文献缓存文件
    cache_file = cache_dir / f"{arxiv_id}_references.json"
    
    # 引用缓存文件
    citations_cache_file = citations_cache_dir / f"{arxiv_id}_citations.json"
    
    # 保存参考文献到缓存
    if references_list:
        cache_data = {
            "uuid": paper_uuid,
            "title": paper_title,
            "semantic_scholar_id": semantic_scholar_id,
            "references": references_list
        }
        
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            console.log(f"[green]参考文献已保存到缓存: {cache_file}")
        except Exception as e:
            console.log(f"[yellow]保存参考文献缓存失败: {e}")
    
    # 保存引用到缓存
    if citations_list:
        citations_cache_data = {
            "uuid": paper_uuid,
            "title": paper_title,
            "semantic_scholar_id": semantic_scholar_id,
            "citations": citations_list
        }
        
        try:
            with open(citations_cache_file, 'w', encoding='utf-8') as f:
                json.dump(citations_cache_data, f, ensure_ascii=False, indent=2)
            console.log(f"[green]引用已保存到缓存: {citations_cache_file}")
        except Exception as e:
            console.log(f"[yellow]保存引用缓存失败: {e}")

def process_paper(paper):
    """处理单篇论文的引用和参考文献"""
    paper_id = paper.get("id")
    paper_title = paper.get("title")
    arxiv_id = paper.get("arxiv_id")
    
    if not paper_id or not paper_title:
        console.log(f"[yellow]论文信息不完整，跳过处理")
        return False
    
    console.log(f"[blue]===== 处理论文: {paper_title} =====")
    
    # 检查缓存中是否已有此论文的参考文献和引用
    cache_dir = Path("./cache/references")
    citations_cache_dir = Path("./cache/citations")
    
    cache_file = None
    citations_cache_file = None
    
    if arxiv_id:
        cache_file = cache_dir / f"{arxiv_id}_references.json"
        citations_cache_file = citations_cache_dir / f"{arxiv_id}_citations.json"
    
    # 检查是否需要获取数据
    references_cache_exists = cache_file and cache_file.exists()
    citations_cache_exists = citations_cache_file and citations_cache_file.exists()
    
    if references_cache_exists and citations_cache_exists:
        console.log(f"[green]缓存中已有此论文的数据，跳过处理")
        return True
    
    # 通过API获取
    console.log(f"[blue]尝试从Semantic Scholar获取论文信息: {paper_title}")
    
    # 调用Semantic Scholar API获取论文详情
    semantic_scholar_id, references_list, citations_list = get_paper_details_from_semantic_scholar(paper_title)
    
    # 保存到缓存
    if arxiv_id and semantic_scholar_id:
        if references_list or citations_list:
            save_to_cache(arxiv_id, paper_id, paper_title, semantic_scholar_id, references_list, citations_list)
            return True
        else:
            console.log(f"[yellow]未获取到参考文献或引用")
            return False
    else:
        console.log(f"[yellow]缺少必要信息，无法保存")
        return False

def main():
    """主函数"""
    # 读取论文信息
    data_path = Path("./data/papers_for_references.json")
    
    if not data_path.exists():
        console.log(f"[red]文件不存在: {data_path}")
        return
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            papers = json.load(f)
    except Exception as e:
        console.log(f"[red]读取文件失败: {e}")
        return
    
    console.log(f"[green]读取了{len(papers)}篇论文信息")
    
    # 处理每一篇论文
    successful_count = 0
    failed_count = 0
    skipped_count = 0
    
    with Progress() as progress:
        task = progress.add_task("[cyan]处理论文...", total=len(papers))
        
        for i, paper in enumerate(papers):
            progress.update(task, description=f"[cyan]处理论文 {i+1}/{len(papers)}")
            
            try:
                # 检查缓存是否已存在
                arxiv_id = paper.get("arxiv_id")
                if arxiv_id:
                    ref_file = Path(f"./cache/references/{arxiv_id}_references.json")
                    cit_file = Path(f"./cache/citations/{arxiv_id}_citations.json")
                    
                    if ref_file.exists() and cit_file.exists():
                        console.log(f"[cyan]论文 {paper.get('title')} 的缓存已存在，跳过")
                        skipped_count += 1
                        progress.update(task, advance=1)
                        continue
                
                success = process_paper(paper)
                if success:
                    successful_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                console.log(f"[red]处理论文时出错: {str(e)}")
                traceback.print_exc()
                failed_count += 1
            
            # 更新进度
            progress.update(task, advance=1)
            
            # 间隔
            if i < len(papers) - 1:
                ensure_paper_rate_limit()
    
    # 统计结果
    console.log(f"[bold green]处理完成！成功: {successful_count}, 失败: {failed_count}, 跳过: {skipped_count}, 总计: {len(papers)}")

if __name__ == "__main__":
    main() 