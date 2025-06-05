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
import traceback
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
    
    def __init__(self, api_base_url="http://localhost:8080/neo4j/v1", max_workers=5, similarity_threshold=0.85):
        """
        初始化引用关系处理器
        
        Args:
            api_base_url (str): API基础URL
            max_workers (int): 最大工作线程数
            similarity_threshold (float): 标题匹配相似度阈值
        """
        self.api_base_url = api_base_url
        self.http = urllib3.PoolManager()
        self.title_mapping = {}
        self.max_workers = max_workers
        self.similarity_threshold = similarity_threshold
        logger.info(f"引用关系处理器初始化，API URL: {api_base_url}")
    
    def save_title_mapping(self):
        """保存论文标题到ID的映射"""
        # 创建映射存储目录
        mapping_dir = Path("./data")
        mapping_dir.mkdir(exist_ok=True, parents=True)
        
        # 保存到文件
        mapping_file = mapping_dir / "paper_title_mapping.json"
        
        # 保存映射
        with open(mapping_file, 'w', encoding='utf-8') as f:
            json.dump(self.title_mapping, f, ensure_ascii=False, indent=2)
            
        logger.info(f"已保存 {len(self.title_mapping)} 个论文标题-ID映射")
        return self.title_mapping

    def load_title_mapping(self):
        """重新加载论文标题到ID的映射"""  
        
        mapping_file = Path("./data/paper_title_mapping.json")
        if mapping_file.exists():
            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    self.title_mapping = json.load(f)
                logger.info(f"已加载 {len(self.title_mapping)} 个现有论文映射")
                return True
            except Exception as e:
                logger.error(f"加载标题映射时出错: {str(e)}")
                return False
        else:
            logger.warning(f"标题映射文件未找到: {mapping_file}")
            return False
    
    def add_paper_mapping(self, title, paper_id):
        """添加单个论文标题到ID的映射"""
        
        if title and paper_id:
            self.title_mapping[title.lower().strip()] = paper_id
    
    def add_multiple_mappings(self, mappings):
        """添加多个论文标题到ID的映射"""
        
        self.title_mapping.update(mappings)
    
    def normalize_title(self, title):
        """规范化论文标题以提高匹配率
        
        Args:
            title (str): 原始标题
            
        Returns:
            str: 规范化标题
        """
        if not title:
            return ""
        
        # 转为小写
        title = title.lower()
        
        # 移除特殊字符
        title = re.sub(r'[^\w\s]', ' ', title)
        
        # 将多个空格替换为单个空格
        title = re.sub(r'\s+', ' ', title)
        
        # 移除前导和尾随空格
        title = title.strip()
        
        return title
    
    def find_best_title_match(self, reference_title):
        """使用模糊匹配找到最佳标题匹配
        
        Args:
            reference_title (str): 参考论文标题
            
        Returns:
            str: 匹配的论文ID或None
        """
        normalized_ref_title = self.normalize_title(reference_title)
        if not normalized_ref_title:
            return None

        if normalized_ref_title in self.title_mapping:
            logger.debug(f"精确匹配成功: {normalized_ref_title}")
            return self.title_mapping[normalized_ref_title]
        
        # 模糊匹配
        best_ratio = 0
        best_match_id = None
        
        for title, paper_id in self.title_mapping.items():
            normalized_title = self.normalize_title(title)
            
            # 计算相似度
            ratio = SequenceMatcher(None, normalized_ref_title, normalized_title).ratio()
            
            if ratio > best_ratio and ratio >= self.similarity_threshold:
                best_ratio = ratio
                best_match_id = paper_id
        
        if best_match_id:
            logger.debug(f"模糊匹配成功: {reference_title} -> {best_ratio:.2f}")
        
        return best_match_id
    
    def scan_citation_files(self, citation_dir="./cache/citations"):
        """扫描引用文件目录获取所有引用文件
        
        Args:
            citation_dir (str): 引用文件目录
            
        Returns:
            list: 引用文件路径列表
        """
        citation_files = []
        citation_dir_path = Path(citation_dir)
        
        if not citation_dir_path.exists():
            logger.warning(f"引用目录未找到: {citation_dir}")
            return citation_files
        
        # 遍历目录中的所有JSON文件
        for cite_file in citation_dir_path.glob("*.json"):
            citation_files.append(cite_file)
        
        logger.info(f"在 {citation_dir} 目录中找到 {len(citation_files)} 个引用文件")
        return citation_files
    
    def parse_citation_file(self, citation_file):
        """解析引用文件，提取源论文和它的引用列表
        
        Args:
            citation_file (Path): 引用文件路径
            
        Returns:
            tuple: (源论文标题, 源论文UUID/ID, 引用列表)
        """
        try:
            with open(citation_file, 'r', encoding='utf-8') as f:
                cite_data = json.load(f)
            
            source_title = cite_data.get("title", "")
            source_id = cite_data.get("uuid", "")
            citations = cite_data.get("citations", [])
            
            return source_title, source_id, citations
        except Exception as e:
            logger.error(f"解析引用文件 {citation_file} 时出错: {str(e)}")
            return None, None, []
    
    def create_citation_relationship(self, source_paper_id, target_paper_id, context=""):
        """创建单个引用关系
        
        Args:
            source_paper_id (str): 源论文ID
            target_paper_id (str): 目标论文ID
            context (str): 引用上下文
            
        Returns:
            bool: 引用关系是否成功创建
        """
        try:
            # 构造引用数据
            citation_data = {
                "target_paper_id": target_paper_id,
                "context": context or "common field: \"Academic Research\""
            }
            
            # 发送API请求
            full_url = f"{self.api_base_url}/papers/{source_paper_id}/citations"
            encoded_data = json.dumps(citation_data).encode('utf-8')
            
            citation_response = self.http.request(
                'POST',
                full_url,
                body=encoded_data,
                headers={'Content-Type': 'application/json'},
                timeout=10.0  
            )
            
            # 检查响应
            if citation_response.status in [200, 201]:
                logger.info(f"成功创建引用关系: {source_paper_id} -> {target_paper_id}")
                return True
            elif citation_response.status == 409:  # 引用关系已存在
                logger.info(f"引用关系已存在: {source_paper_id} -> {target_paper_id}")
                return True
            else:
                response_text = citation_response.data.decode('utf-8')
                logger.error(f"创建引用关系失败: {citation_response.status} - {response_text}")
                return False
                
        except Exception as e:
            logger.error(f"创建引用关系时出错: {str(e)}")
            return False
    
    def process_paper_citations(self, source_title, source_id, citations):
        """处理单个论文的引用关系
        
        Args:
            source_title (str): 源论文标题
            source_id (str): 源论文ID
            citations (list): 引用论文列表
            
        Returns:
            tuple: (processed, success, failed, skipped)
        """
        logger.info(f"处理论文 '{source_title}' (ID: {source_id}) 的引用关系，共 {len(citations)} 个引用")
        
        processed = 0
        success = 0
        failed = 0
        skipped = 0
        
        # 如果没有源论文ID，尝试通过标题查找
        if not source_id and source_title:
            source_id = self.find_best_title_match(source_title)
            if not source_id:
                logger.error(f"无法找到源论文 '{source_title}' 的ID，跳过处理其引用关系")
                return 0, 0, 0, len(citations)
        
        if not source_id:
            logger.error("源论文ID为空，跳过处理其引用关系")
            return 0, 0, 0, len(citations)
        
        for cite in citations:
            processed += 1
            
            # 获取引用论文标题
            cite_title = cite.get("title", "")
            
            # 跳过空标题
            if not cite_title:
                logger.warning("跳过空引用标题")
                skipped += 1
                continue
            
            # 尝试获取引用论文ID
            target_paper_id = None
            
            # 如果有外部ID，直接使用
            external_id = cite.get("external_id", "")
            if external_id:
                # 如果有外部ID，需要在数据库中查找对应的内部ID
                # 这里简化处理，如果有标题映射就使用它
                target_paper_id = self.find_best_title_match(cite_title)
            else:
                # 通过标题匹配查找ID
                target_paper_id = self.find_best_title_match(cite_title)
            
            # 如果没有找到匹配项，跳过
            if not target_paper_id:
                logger.warning(f"未找到匹配的论文: {cite_title}")
                skipped += 1
                continue
            
            # 避免自引用
            if target_paper_id == source_id:
                logger.warning(f"跳过自引用: {source_title}")
                skipped += 1
                continue
            
            # 创建引用关系
            context = f"common field: \"{cite.get('publication', 'Academic Research')}\""
            if self.create_citation_relationship(source_id, target_paper_id, context):
                success += 1
            else:
                failed += 1
        
        logger.info(f"论文引用处理完成: processed={processed}, success={success}, failed={failed}, skipped={skipped}")
        return processed, success, failed, skipped
    
    def process_citations(self, citation_dir="./cache/citations"):
        """处理所有论文的引用关系
        
        Args:
            citation_dir (str): 引用文件目录
            
        Returns:
            tuple: (total_processed, total_success, total_failed, total_skipped)
        """
        # 加载标题映射
        if not self.load_title_mapping():
            logger.error("加载标题映射失败，无法处理引用关系")
            return 0, 0, 0, 0
        
        # 获取引用文件
        citation_files = self.scan_citation_files(citation_dir)
        if not citation_files:
            logger.info("未找到引用文件")
            return 0, 0, 0, 0
        
        total_processed = 0
        total_success = 0
        total_failed = 0
        total_skipped = 0
        
        # 使用进度条跟踪处理进度
        with tqdm(total=len(citation_files), desc="处理引用关系") as pbar:
            # 使用线程池并行处理
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # 提交所有任务
                future_to_paper = {}
                for cite_file in citation_files:
                    source_title, source_id, citations = self.parse_citation_file(cite_file)
                    if source_title and citations:
                        future = executor.submit(self.process_paper_citations, source_title, source_id, citations)
                        future_to_paper[future] = (source_title, source_id)
                
                # 处理结果
                for future in as_completed(future_to_paper):
                    source_title, source_id = future_to_paper[future]
                    try:
                        processed, success, failed, skipped = future.result()
                        total_processed += processed
                        total_success += success
                        total_failed += failed
                        total_skipped += skipped
                        pbar.update(1)
                    except Exception as e:
                        logger.error(f"处理论文 {source_title} (ID: {source_id}) 时出错: {str(e)}")
                        pbar.update(1)
        
        logger.info(f"引用处理完成: processed={total_processed}, success={total_success}, failed={total_failed}, skipped={total_skipped}")
        return total_processed, total_success, total_failed, total_skipped

    def generate_title_mapping_from_api(self):
        """从API生成标题映射
        
        Returns:
            bool: 是否成功生成映射
        """
        try:
            # 请求所有论文
            full_url = f"{self.api_base_url}/papers?limit=1000"
            
            response = self.http.request(
                'GET',
                full_url,
                timeout=30.0
            )
            
            if response.status != 200:
                logger.error(f"获取论文列表失败: {response.status}")
                return False
            
            data = json.loads(response.data.decode('utf-8'))
            papers = data.get("papers", [])
            
            if not papers:
                logger.warning("API未返回任何论文")
                return False
            
            # 构建标题到ID的映射
            for paper in papers:
                title = paper.get("title", "")
                paper_id = paper.get("id", "")
                if title and paper_id:
                    self.add_paper_mapping(title, paper_id)
            
            # 保存映射
            self.save_title_mapping()
            logger.info(f"从API生成了 {len(self.title_mapping)} 个标题映射")
            return True
            
        except Exception as e:
            logger.error(f"从API生成标题映射时出错: {str(e)}")
            traceback.print_exc()
            return False


def main():
    """主函数，处理命令行参数并执行引用关系处理"""
    parser = argparse.ArgumentParser(description='引用管理器 - 基于标题匹配处理论文引用关系')
    parser.add_argument('--api-url', default='http://localhost:8080/neo4j/v1', help='API基础URL')
    parser.add_argument('--citation-dir', default='./cache/citations', help='引用文件目录')
    parser.add_argument('--workers', type=int, default=5, help='最大工作线程数')
    parser.add_argument('--similarity', type=float, default=0.85, help='标题匹配相似度阈值 (0-1)')
    parser.add_argument('--interval', type=int, default=0, help='处理间隔（秒），0表示只运行一次')
    parser.add_argument('--generate-mapping', action='store_true', help='从API生成标题映射')
    args = parser.parse_args()
    
    # 创建引用管理器
    processor = CitationProcessor(
        api_base_url=args.api_url,
        max_workers=args.workers,
        similarity_threshold=args.similarity
    )
    
    # 如果需要，生成标题映射
    if args.generate_mapping:
        logger.info("从API生成标题映射...")
        if processor.generate_title_mapping_from_api():
            logger.info("标题映射生成成功")
        else:
            logger.error("标题映射生成失败")
    
    if args.interval > 0:
        logger.info(f"启动引用处理服务，间隔: {args.interval} 秒")
        try:
            while True:
                processor.process_citations(args.citation_dir)
                logger.info(f"等待 {args.interval} 秒后进行下一次处理周期...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("服务被用户中断")
    else:
        # 一次性运行模式
        total_processed, total_success, total_failed, total_skipped = processor.process_citations(args.citation_dir)
        logger.info(f"引用处理摘要:")
        logger.info(f"- 总处理引用: {total_processed}")
        logger.info(f"- 成功创建: {total_success}")
        logger.info(f"- 创建失败: {total_failed}")
        logger.info(f"- 跳过: {total_skipped}")


if __name__ == "__main__":
    main() 