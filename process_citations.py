#!/usr/bin/env python3
"""
引用关系处理脚本 - 基于标题匹配在已爬取的论文集合内建立引用关系
"""

import os
import json
import time
import urllib3
import argparse
import logging
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
import re

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='citation_processing.log',
    filemode='a'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)
logger = logging.getLogger('citation_processor')


class CitationProcessor:
    """引用关系处理器 - 基于标题匹配"""
    
    def __init__(self, api_base_url="http://localhost:8080/api/v1", data_dir="./data", 
                 max_workers=5, similarity_threshold=0.85):
        """
        初始化引用关系处理器
        
        参数:
            api_base_url: API基础URL
            data_dir: 数据目录，包含标题映射文件
            max_workers: 最大工作线程数
            similarity_threshold: 标题匹配的相似度阈值
        """
        self.api_base_url = api_base_url
        self.data_dir = Path(data_dir)
        self.max_workers = max_workers
        self.similarity_threshold = similarity_threshold
        self.http = urllib3.PoolManager()
        self.paper_title_map = {}  # 标题到ID的映射
        
        # 确保数据目录存在
        if not self.data_dir.exists():
            logger.warning(f"数据目录不存在: {self.data_dir}，创建该目录")
            self.data_dir.mkdir(exist_ok=True, parents=True)
    
    def load_title_mapping(self):
        """加载论文标题到ID的映射"""
        mapping_file = self.data_dir / "paper_title_mapping.json"
        if not mapping_file.exists():
            logger.error(f"标题映射文件不存在: {mapping_file}")
            return False
        
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                self.paper_title_map = json.load(f)
            
            logger.info(f"已加载{len(self.paper_title_map)}个论文标题-ID映射")
            return True
        except Exception as e:
            logger.error(f"加载标题映射失败: {str(e)}")
            return False
    
    def normalize_title(self, title):
        """标准化论文标题，提高匹配率"""
        if not title:
            return ""
        
        # 转为小写
        title = title.lower()
        
        # 移除特殊字符
        title = re.sub(r'[^\w\s]', ' ', title)
        
        # 替换多个空格为单个空格
        title = re.sub(r'\s+', ' ', title)
        
        # 去除首尾空格
        title = title.strip()
        
        return title
    
    def find_best_title_match(self, reference_title):
        """
        使用模糊匹配找到最佳的标题匹配
        返回匹配到的论文ID或None
        """
        normalized_ref_title = self.normalize_title(reference_title)
        if not normalized_ref_title:
            return None
        
        # 精确匹配
        if normalized_ref_title in self.paper_title_map:
            logger.debug(f"精确匹配成功: {normalized_ref_title}")
            return self.paper_title_map[normalized_ref_title]
        
        # 模糊匹配
        best_ratio = 0
        best_match_id = None
        
        for title, paper_id in self.paper_title_map.items():
            normalized_title = self.normalize_title(title)
            
            # 计算相似度
            ratio = SequenceMatcher(None, normalized_ref_title, normalized_title).ratio()
            
            if ratio > best_ratio and ratio >= self.similarity_threshold:
                best_ratio = ratio
                best_match_id = paper_id
        
        if best_match_id:
            logger.debug(f"模糊匹配成功: {reference_title} -> {best_ratio:.2f}")
        
        return best_match_id
    
    def get_papers_with_references(self):
        """获取所有带引用关系的论文"""
        try:
            # 从API获取所有论文列表 - 添加一个额外参数避免400错误
            response = self.http.request(
                'GET',
                f"{self.api_base_url}/papers?limit=10000&keyword=",  # 添加空的keyword参数
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status != 200:
                logger.error(f"获取论文列表失败: {response.status}, 错误信息: {response.data.decode('utf-8')}")
                return []
            
            # 解析响应
            data = json.loads(response.data.decode('utf-8'))
            papers = data.get('papers', [])
            logger.info(f"从API获取到{len(papers)}篇论文")
            
            # 过滤出有引用关系的论文
            papers_with_refs = []
            for paper in papers:
                paper_id = paper.get('id')
                title = paper.get('title')
                
                # 获取该论文的详情（包括引用信息）
                try:
                    detail_response = self.http.request(
                        'GET',
                        f"{self.api_base_url}/papers/{paper_id}",
                        headers={'Content-Type': 'application/json'}
                    )
                    
                    if detail_response.status == 200:
                        detail_data = json.loads(detail_response.data.decode('utf-8'))
                        if 'references' in detail_data and detail_data['references']:
                            references = detail_data['references']
                            papers_with_refs.append((paper_id, title, references))
                    else:
                        logger.warning(f"获取论文详情失败: {paper_id}, 错误信息: {detail_response.data.decode('utf-8')}")
                except Exception as e:
                    logger.error(f"获取论文详情时出错: {str(e)}")
            
            logger.info(f"找到{len(papers_with_refs)}篇有引用关系的论文")
            return papers_with_refs
            
        except Exception as e:
            logger.error(f"获取论文列表时出错: {str(e)}")
            return []
    
    def process_references(self):
        """处理所有论文的引用关系"""
        # 先加载标题映射
        if not self.load_title_mapping():
            logger.error("加载标题映射失败，无法继续处理")
            return
        
        # 获取有引用关系的论文
        papers_with_refs = self.get_papers_with_references()
        if not papers_with_refs:
            logger.info("没有发现有引用关系的论文")
            return
        
        total_processed = 0
        total_success = 0
        total_failed = 0
        total_skipped = 0
        
        # 使用进度条跟踪处理进度
        with tqdm(total=len(papers_with_refs), desc="处理引用关系") as pbar:
            # 使用线程池并行处理
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # 提交所有任务
                future_to_paper = {executor.submit(self.process_paper_references, paper_id, title, references): 
                                (paper_id, title) for paper_id, title, references in papers_with_refs}
                
                # 处理结果
                for future in as_completed(future_to_paper):
                    paper_id, title = future_to_paper[future]
                    try:
                        processed, success, failed, skipped = future.result()
                        total_processed += processed
                        total_success += success
                        total_failed += failed
                        total_skipped += skipped
                        pbar.update(1)
                    except Exception as e:
                        logger.error(f"处理论文时出错 {title} (ID: {paper_id}): {str(e)}")
                        pbar.update(1)
        
        logger.info(f"处理完成: 总共处理了{total_processed}个引用关系, 成功: {total_success}, 失败: {total_failed}, 跳过: {total_skipped}")
    
    def process_paper_references(self, paper_id, paper_title, references):
        """处理单个论文的引用关系"""
        try:
            logger.info(f"处理论文 '{paper_title}' (ID: {paper_id}) 的引用关系, 共{len(references)}个引用")
            
            processed = 0
            success = 0
            failed = 0
            skipped = 0
            
            # 处理每个引用关系
            for reference_title in references:
                processed += 1
                
                # 检查引用数据完整性
                if not reference_title:
                    logger.warning(f"跳过空标题的引用")
                    skipped += 1
                    continue
                
                # 找到最佳匹配的论文ID
                target_paper_id = self.find_best_title_match(reference_title)
                
                # 如果没找到匹配的论文，跳过
                if not target_paper_id:
                    logger.warning(f"未找到匹配的目标论文: {reference_title}")
                    skipped += 1
                    continue
                
                # 避免自引用
                if target_paper_id == paper_id:
                    logger.warning(f"跳过自引用: {paper_title}")
                    skipped += 1
                    continue
                
                # 创建引用关系
                result = self.create_citation(paper_id, target_paper_id, reference_title)
                if result:
                    success += 1
                else:
                    failed += 1
            
            return processed, success, failed, skipped
            
        except Exception as e:
            logger.error(f"处理论文引用关系时出错: {str(e)}")
            return 0, 0, 0, 0
    
    def create_citation(self, source_paper_id, target_paper_id, reference_title):
        """创建引用关系"""
        try:
            # 构造引用数据
            citation_data = {
                "target_paper_id": target_paper_id,
                "context": "common field: \"Academic Research\""
            }
            
            # 发送API请求
            full_url = f"{self.api_base_url}/papers/{source_paper_id}/citations"
            encoded_data = json.dumps(citation_data).encode('utf-8')
            
            citation_response = self.http.request(
                'POST',
                full_url,
                body=encoded_data,
                headers={'Content-Type': 'application/json'},
                timeout=10.0  # 设置超时时间
            )
            
            # 检查响应
            if citation_response.status in [200, 201]:
                logger.info(f"成功创建引用关系: {source_paper_id} -> {target_paper_id} ({reference_title})")
                return True
            elif citation_response.status == 409:  # 引用关系已存在
                logger.info(f"引用关系已存在: {source_paper_id} -> {target_paper_id} ({reference_title})")
                return True
            else:
                response_text = citation_response.data.decode('utf-8')
                logger.error(f"创建引用关系失败: {citation_response.status} - {response_text}")
                return False
                
        except Exception as e:
            logger.error(f"创建引用关系时出错: {str(e)}")
            return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='处理论文引用关系')
    parser.add_argument('--api-url', default='http://localhost:8080/api/v1', help='API基础URL')
    parser.add_argument('--data-dir', default='./data', help='数据目录')
    parser.add_argument('--workers', type=int, default=5, help='最大工作线程数')
    parser.add_argument('--similarity', type=float, default=0.85, help='标题匹配的相似度阈值')
    parser.add_argument('--interval', type=int, default=0, help='处理间隔（秒），0表示只运行一次')
    args = parser.parse_args()
    
    processor = CitationProcessor(
        api_base_url=args.api_url,
        data_dir=args.data_dir,
        max_workers=args.workers,
        similarity_threshold=args.similarity
    )
    
    if args.interval > 0:
        logger.info(f"启动引用关系处理服务，间隔: {args.interval}秒")
        try:
            while True:
                processor.process_references()
                logger.info(f"等待{args.interval}秒后继续处理...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("服务被用户中断")
    else:
        processor.process_references()


if __name__ == "__main__":
    main() 