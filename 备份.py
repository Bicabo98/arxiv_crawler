import asyncio
import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, UTC
from pathlib import Path

from rich.console import Console
from typing_extensions import Iterable

from async_translator import async_translate
from categories import parse_categories
import requests
import uuid
from datetime import datetime
import re
import urllib3
import os
import time
from bs4 import BeautifulSoup

@dataclass
class Paper:
    first_submitted_date: datetime
    title: str
    categories: list
    url: str
    authors: str
    abstract: str
    comments: str
    title_translated: str | None = None
    abstract_translated: str | None = None
    first_announced_date: datetime | None = None
    references: list = None 



    @classmethod
    def from_row(cls, row: sqlite3.Row):
        return cls(
            first_submitted_date=datetime.strptime(row["first_submitted_date"], "%Y-%m-%d"),
            title=row["title"],
            categories=row["categories"].split(","),
            url=row["url"],
            authors=row["authors"],
            abstract=row["abstract"],
            comments=row["comments"],
            title_translated=row["title_translated"],
            abstract_translated=row["abstract_translated"],
            first_announced_date=datetime.strptime(row["first_announced_date"], "%Y-%m-%d"),
            references=json.loads(row["paper_references"]) if row["paper_references"] else None,
        )
    @property
    def papers_cool_url(self):
        return self.url.replace("https://arxiv.org/abs", "https://papers.cool/arxiv")
    
    @property
    def pdf_url(self):
        return self.url.replace("https://arxiv.org/abs", "https://arxiv.org/pdf")

    def to_markdown(self):
        categories = ",".join(parse_categories(self.categories))
        abstract = (
            f"- **摘要**: {self.abstract_translated}"
            if self.abstract_translated
            else f"- **Abstract**: {self.abstract}"
        )

        return f"""### {self.title} 
[[arxiv]({self.url})] [[cool]({self.papers_cool_url})] [[pdf]({self.pdf_url})]
> **Authors**: {self.authors}
> **First submission**: {self.first_submitted_date.strftime("%Y-%m-%d")}
> **First announcement**: {self.first_announced_date.strftime("%Y-%m-%d")}
> **comment**: {self.comments}
- **标题**: {self.title_translated}
- **领域**: {categories}
{abstract}

"""

    async def translate(self, langto="zh-CN"):
        self.title_translated = await async_translate(self.title, langto=langto)
        self.abstract_translated = await async_translate(self.abstract, langto=langto)


    def add_reference(self, reference_title):
        """添加引用论文标题"""
        if not hasattr(self, 'references') or self.references is None:
            self.references = []
        if reference_title not in self.references:
            self.references.append(reference_title)


@dataclass
class PaperRecord:
    paper: Paper
    comment: str

    def to_markdown(self):
        if self.comment != "-":
            return f"""- [{self.paper.title}]({self.paper.url})
  - **标题**: {self.paper.title_translated}
  - **Filtered Reason**: {self.comment}
"""
        else:
            return self.paper.to_markdown()

    def add_reference(self, reference_title):
        """添加引用论文标题"""
        if self.references is None:
            self.references = []
        if reference_title not in self.references:
            self.references.append(reference_title)


class PaperDatabase:
    def __init__(self, db_path="papers.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = self._row_factory
        self._create_table()

    @staticmethod
    def _row_factory(cursor, row):
        row = sqlite3.Row(cursor, row)
        # all fields in Paper, plus `update_time`
        if len(row.keys()) == len(fields(Paper)) + 1:
            return Paper.from_row(row)
        else:
            return row

    def _create_table(self):
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    url TEXT PRIMARY KEY,
                    authors TEXT NOT NULL,
                    title_translated TEXT,
                    first_submitted_date DATE NOT NULL,
                    first_announced_date DATE NOT NULL,
                    update_time DATETIME NOT NULL,
                    categories TEXT NOT NULL,
                    title TEXT NOT NULL,
                    comments TEXT NOT NULL,
                    abstract TEXT NOT NULL,
                    abstract_translated TEXT,
                    paper_references TEXT
                )
            """
            )

    def add_papers(self, papers: Iterable[Paper]):
        assert all([paper.first_announced_date is not None for paper in papers])
        with self.conn:
            data_to_insert = [
                (
                    paper.url,
                    paper.authors,
                    paper.abstract,
                    paper.title,
                    ",".join(paper.categories),
                    paper.first_submitted_date.strftime("%Y-%m-%d"),
                    paper.first_announced_date.strftime("%Y-%m-%d"),
                    paper.title_translated,
                    paper.abstract_translated,
                    paper.comments,
                    json.dumps(paper.references) if paper.references else None,
                    datetime.now(UTC).replace(tzinfo=None)
                )
                for paper in papers
            ]
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO papers 
                (url, authors, abstract, title, categories, first_submitted_date, first_announced_date, title_translated, abstract_translated, comments, paper_references, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                data_to_insert,
            )

    def count_new_papers(self, papers: Iterable[Paper]) -> int:
        cnt = 0
        for paper in papers:
            with self.conn:
                cursor = self.conn.execute(
                    """
                    SELECT * FROM papers WHERE url = ?
                    """,
                    (paper.url,),
                )
                if cursor.fetchone():
                    break
                else:
                    cnt += 1
        return cnt

    def fetch_papers_on_date(self, date: datetime) -> list[Paper]:
        with self.conn:
            cursor = self.conn.execute(
                """
                SELECT * FROM papers WHERE first_announced_date = ?
                """,
                (date.strftime("%Y-%m-%d"),),
            )
            return cursor.fetchall()

    def fetch_all(self) -> list[Paper]:
        with self.conn:
            cursor = self.conn.execute(
                """
                SELECT * FROM papers ORDER BY url DESC
                """
            )
            return cursor.fetchall()

    def newest_update_time(self) -> datetime:
        """
        最新更新时间是"上一次爬取最新论文的时间"
        由于数据库可能补充爬取过去的论文，所以先选最新论文，再从其中选最新的爬取时间
        """
        with self.conn:
            cursor = self.conn.execute(
                """
                SELECT MAX(update_time) as max_updated_time
                FROM papers
                WHERE first_announced_date = (SELECT MAX(first_announced_date) FROM papers)
                """
            )
        time = cursor.fetchone()["max_updated_time"].split(".")[0]
        return datetime.strptime(time, "%Y-%m-%d %H:%M:%S")

    async def translate_missing(self, langto="zh-CN"):
        with self.conn:
            cursor = self.conn.execute(
                "SELECT url, title, abstract FROM papers WHERE title_translated IS NULL OR abstract_translated IS NULL"
            )
            papers = cursor.fetchall()

        async def worker(url, title, abstract):
            title_translated = await async_translate(title, langto=langto) if title else None
            abstract_translated = await async_translate(abstract, langto=langto) if abstract else None
            with self.conn:
                self.conn.execute(
                    "UPDATE papers SET title_translated = ?, abstract_translated = ? WHERE url = ?",
                    (title_translated, abstract_translated, url),
                )

        await asyncio.gather(*[worker(url, title, abstract) for url, title, abstract in papers])


class PaperExporter:
    def __init__(
        self,
        date_from: str,
        date_until: str,
        categories_blacklist: list[str] = [],
        categories_whitelist: list[str] = ["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"],
        database_path="papers.db",
    ):
        self.db = PaperDatabase(database_path)
        self.date_from = datetime.strptime(date_from, "%Y-%m-%d")
        self.date_until = datetime.strptime(date_until, "%Y-%m-%d")
        self.date_range_days = (self.date_until - self.date_from).days + 1
        self.categories_blacklist = set(categories_blacklist)
        self.categories_whitelist = set(categories_whitelist)
        self.console = Console()

    def filter_papers(self, papers: list[Paper]) -> tuple[list[PaperRecord], list[PaperRecord]]:
        filtered_paper_records = []
        chosen_paper_records = []
        for paper in papers:
            # categories = set(paper.categories)
            # if not (self.categories_whitelist & categories):
            #     categories_str = ",".join(categories)
            #     filtered_paper_records.append(PaperRecord(paper, f"none of {categories_str} in whitelist"))
            # elif black := self.categories_blacklist & categories:
            #     black_str = ",".join(black)
            #     filtered_paper_records.append(PaperRecord(paper, f"cat:{black_str} in blacklist"))
            # else:
            chosen_paper_records.append(PaperRecord(paper, "-"))
        return chosen_paper_records, filtered_paper_records

    def to_markdown(self, output_dir="./output_llms", filename_format="%Y-%m-%d", metadata=None):
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        if metadata:
            repo_url = metadata["repo_url"]
            categories = ",".join(metadata["category_whitelist"])
            optional_keywords = ", ".join(metadata["optional_keywords"])
            preface_str = f"""
> 本文由 [{repo_url}]({repo_url}) 自动生成
>
> 领域白名单：{categories}
> 关键词： {optional_keywords}

""".lstrip()
        else:
            preface_str = ""

        # 收集所有日期范围内的论文记录
        all_paper_records = []
        
        for i in range(self.date_range_days):
            current = self.date_from + timedelta(days=i)
            current_filename = current.strftime(filename_format)

            papers = self.db.fetch_papers_on_date(current)
            chosen_records, filtered_records = self.filter_papers(papers)
            
            # 将当天的论文记录添加到全部集合
            all_paper_records.extend(chosen_records + filtered_records)
            
            with open(output_dir / f"{current_filename}.md", "w", encoding="utf-8") as file:
                papers_str = f"# 论文全览：{current_filename}\n\n共有{len(chosen_records)}篇相关领域论文, 另有{len(filtered_records)}篇其他\n\n"

                chosen_dict = defaultdict(list)
                for record in chosen_records:
                    chosen_dict[record.paper.categories[0]].append(record)
                for category in sorted(chosen_dict.keys()):
                    category_en = parse_categories([category], lang="en")[0]
                    category_zh = parse_categories([category], lang="zh-CN")[0]
                    papers_str += f"## {category_zh}({category}:{category_en})\n\n"
                    for record in chosen_dict[category]:
                        papers_str += record.to_markdown()

                papers_str += "## 其他论文\n\n"
                for record in filtered_records:
                    papers_str += record.to_markdown()

                file.write(preface_str + papers_str)

            self.console.log(
                f"[bold green]Output {current_filename}.md completed. {len(chosen_records)} papers chosen, {len(filtered_records)} papers filtered"
            )
            
        # 处理完所有日期的文件后，单独处理一次图数据库存储
        # 这样可以避免频繁调用ADS API
        if all_paper_records:
            self.console.log(f"[bold blue]开始处理{len(all_paper_records)}篇论文的图数据库存储...")
            self.save_papers_to_graph_db(all_paper_records)

    def save_papers_to_graph_db(self, paper_records):
        """将论文记录保存到图数据库，并获取参考文献"""
        import requests
        import uuid
        import re
        import json
        import time
        import urllib3
        import os
        from datetime import datetime
        from pathlib import Path
        
        api_base_url = "http://localhost:8080/neo4j/v1"  # 图数据库API地址
        ads_api_token = "fRz4Ale1aJyAsatFcdns7cuQPPnl1Mte63HhUZ1T"  # ADS API令牌，请替换为实际令牌
        http = urllib3.PoolManager()

        # 保存所有论文的映射 {标题: 论文ID}
        paper_title_to_id = self.load_title_mapping()  # 加载现有映射
        papers_with_references = []
        
        # 创建缓存目录
        cache_dir = Path("./cache/references")
        cache_dir.mkdir(exist_ok=True, parents=True)
        
        # 全局限速设置
        MIN_REQUEST_INTERVAL = 40  # 两次请求之间的最小间隔（秒）
        last_request_time = 0  # 上次请求的时间戳
        
        # 请求计数和成功率统计
        total_requests = 0
        successful_requests = 0
        
        def ensure_rate_limit():
            """确保请求不超过速率限制"""
            nonlocal last_request_time
            current_time = time.time()
            elapsed = current_time - last_request_time
            
            if elapsed < MIN_REQUEST_INTERVAL:
                wait_time = MIN_REQUEST_INTERVAL - elapsed
                self.console.log(f"[yellow]限速等待: {wait_time:.1f}秒...")
                time.sleep(wait_time)
            
            last_request_time = time.time()
        
        for index, record in enumerate(paper_records):
            paper = record.paper
            self.console.log(f"[blue]处理论文 {index+1}/{len(paper_records)}: {paper.title}")
            
            # 统计当前成功率
            if total_requests > 0:
                success_rate = (successful_requests / total_requests) * 100
                self.console.log(f"[blue]当前请求成功率: {success_rate:.1f}% ({successful_requests}/{total_requests})")
            
            # 生成论文UUID
            paper_uuid = str(uuid.uuid4())
            
            # 从URL中提取arXiv ID
            arxiv_id = ""
            if paper.url and "arxiv.org/abs/" in paper.url:
                match = re.search(r'arxiv\.org/abs/([^/]+)', paper.url)
                if match:
                    arxiv_id = match.group(1)
                    
                    # 修改URL，从abs变为pdf
                    pdf_url = paper.url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"
                    
                    # 尝试获取论文的参考文献
                    references_list = []
                    cache_data = None
                    
                    # 检查缓存中是否已有此论文的参考文献
                    cache_file = cache_dir / f"{arxiv_id}_references.json"
                    if cache_file.exists():
                        try:
                            with open(cache_file, 'r', encoding='utf-8') as f:
                                cache_data = json.load(f)
                                # 检查缓存是否是新格式（包含uuid、title和references）
                                if 'references' in cache_data:
                                    references_list = cache_data['references']
                                    self.console.log(f"[green]从缓存加载{len(references_list)}条参考文献")
                                    # 如果缓存中的uuid不同于新生成的，使用缓存中的uuid
                                    if 'uuid' in cache_data:
                                        paper_uuid = cache_data['uuid']
                                        self.console.log(f"[green]使用缓存中的论文UUID: {paper_uuid}")
                                else:
                                    # 旧格式缓存，直接作为references列表
                                    references_list = cache_data
                                    self.console.log(f"[green]从旧格式缓存加载{len(references_list)}条参考文献")
                        except Exception as e:
                            self.console.log(f"[yellow]读取缓存失败: {e}")
                    
                    # 如果缓存中没有，则通过API获取
                    if not references_list:
                        # 解析作者列表
                        authors_list = [author_name.strip() for author_name in paper.authors.split(',') if author_name.strip()]
                        
                        if arxiv_id and authors_list:
                            self.console.log(f"[blue]尝试获取论文参考文献...")
                            
                            # 确保请求限速
                            ensure_rate_limit()
                            
                            # 1. 构造ADS URL和基础bibcode
                            abstract_url, base_id = construct_ads_url(arxiv_id, authors_list, "abstract")
                            
                            if abstract_url and base_id:
                                self.console.log(f"[blue]ADS摘要页面URL: {abstract_url}")
                                
                                # 更新请求计数
                                total_requests += 1
                                
                                # 2. 爬取摘要页面以获取bibcode
                                abstract_html = crawl_ads_page(abstract_url)
                                
                                if abstract_html:
                                    # 更新成功请求计数
                                    successful_requests += 1
                                    
                                    # 3. 从HTML中提取arxiv ID
                                    extracted_arxiv_id = extract_bibcode_from_html(abstract_html)
                                    
                                    if extracted_arxiv_id:
                                        self.console.log(f"[green]从HTML中提取到arXiv ID: {extracted_arxiv_id}")
                                        
                                        # 4. 使用提取的arxiv ID和作者列表重新构造正确的bibcode
                                        _, new_bibcode = construct_ads_url(extracted_arxiv_id, authors_list, "abstract")
                                        
                                        if new_bibcode:
                                            self.console.log(f"[green]构造ADS bibcode: {new_bibcode}")
                                            bibcode = new_bibcode
                                        else:
                                            self.console.log(f"[yellow]无法构造新的bibcode，使用原始基础ID: {base_id}")
                                            bibcode = base_id
                                    else:
                                        self.console.log(f"[yellow]未能从HTML中提取arXiv ID，使用原始基础ID: {base_id}")
                                        bibcode = base_id
                                    
                                    # 5. 使用ADS API获取参考文献
                                    if bibcode:
                                        # 确保请求限速
                                        ensure_rate_limit()
                                        
                                        # 更新请求计数
                                        total_requests += 1
                                        
                                        self.console.log(f"[blue]使用bibcode获取参考文献: {bibcode}")
                                        references_list = get_references_from_ads_api(bibcode, ads_api_token)
                                        
                                        if references_list:
                                            # 更新成功请求计数
                                            successful_requests += 1
                                            
                                            self.console.log(f"[green]成功获取{len(references_list)}条参考文献")
                                            
                                            # 创建包含论文UUID和标题的缓存数据
                                            cache_data = {
                                                "uuid": paper_uuid,
                                                "title": paper.title,
                                                "references": references_list
                                            }
                                            
                                            # 保存到缓存
                                            try:
                                                with open(cache_file, 'w', encoding='utf-8') as f:
                                                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                                                self.console.log(f"[green]参考文献已保存到缓存（新格式）")
                                            except Exception as e:
                                                self.console.log(f"[yellow]保存缓存失败: {e}")
                                        else:
                                            self.console.log(f"[yellow]未找到参考文献或获取失败")
                                else:
                                    self.console.log(f"[yellow]未能爬取ADS摘要页面")
                            else:
                                self.console.log(f"[yellow]无法构造有效的ADS URL")
                    
                    # 如果已经有了缓存数据但没有新格式的缓存，更新缓存
                    elif cache_data and 'references' not in cache_data:
                        # 创建包含论文UUID和标题的缓存数据
                        new_cache_data = {
                            "uuid": paper_uuid,
                            "title": paper.title,
                            "references": references_list
                        }
                        
                        # 保存到缓存
                        try:
                            with open(cache_file, 'w', encoding='utf-8') as f:
                                json.dump(new_cache_data, f, ensure_ascii=False, indent=2)
                            self.console.log(f"[green]更新缓存为新格式")
                        except Exception as e:
                            self.console.log(f"[yellow]更新缓存格式失败: {e}")
            
            # 构建DOI
            doi = f"10.48550/arXiv.{arxiv_id}" if arxiv_id else ""
            
            # 从categories中提取关键词
            keywords = []
            for category in paper.categories:
                keywords.append(category)

            # 处理作者列表
            authors_list = [author_name.strip() for author_name in paper.authors.split(',') if author_name.strip()]
            
            # 1. 创建论文
            paper_data = {
                "paper": {
                    "id": paper_uuid,  # 使用提前生成的UUID
                    "title": paper.title,
                    "abstract": paper.abstract,
                    "doi": doi,
                    "url": paper.url,
                    "citations": 0,  # 设置为0
                    "referencescount": len(references_list) if references_list else 0,  # 引用数量
                    "published_at": paper.first_submitted_date.isoformat() + "Z",
                    "keywords": keywords,
                    "venue_id": "arxiv",
                    "venue_name": "arXiv",
                    "external_id": "",  # 清空外部ID
                    "data_source": "arxiv"  # 设置数据来源为arxiv
                },
                "authors": [
                    {
                        "id": str(uuid.uuid4()),  # 生成作者ID
                        "name": author_name,
                        "affiliation": "",  # 如果有机构信息可以填入
                        "email": "",
                        "orcid": "",
                        "research_interests": paper.categories  # 使用论文分类作为研究兴趣
                    }
                    for author_name in authors_list
                ]
            }
            
            # 如果有参考文献，添加到paper_data中
            if references_list:
                paper_data["references"] = references_list
            
            try:
                # 确保请求限速
                ensure_rate_limit()
                
                # 创建论文
                full_url = f"{api_base_url}/papers/create"
                encoded_data = json.dumps(paper_data).encode('utf-8')
                
                # 更新请求计数
                total_requests += 1
                
                paper_response = http.request(
                    'POST',
                    full_url,
                    body=encoded_data,
                    headers={'Content-Type': 'application/json'}
                )
                
                response_text = paper_response.data.decode('utf-8')
                if paper_response.status >= 400:
                    raise Exception(f"HTTP错误: {paper_response.status}")
                
                # 更新成功请求计数
                successful_requests += 1
                
                # 解析返回的JSON以获取服务器分配的论文ID
                response_json = json.loads(response_text)
                paper_id = response_json.get("paper", {}).get("id", paper_data["paper"]["id"])
                self.console.log(f"[bold blue]论文已保存到图数据库: {paper.title}")

                # 保存论文标题和ID的映射关系
                paper_title_to_id[paper.title.lower().strip()] = paper_id
                
                # 每篇论文处理完成后立即更新映射文件
                self.save_title_mapping(paper_title_to_id)
                self.console.log(f"[green]已更新论文映射 (当前总计: {len(paper_title_to_id)}篇)")

                if paper.references and len(paper.references) > 0:
                    papers_with_references.append((paper_id, paper.title, paper.references))
                
            except Exception as e:
                self.console.log(f"[bold red]保存论文到图数据库失败: {str(e)}")
                import traceback
                self.console.log(traceback.format_exc())
            
            # 每处理完一篇论文后，根据请求成功率动态调整请求间隔
            if total_requests > 10:
                success_rate = (successful_requests / total_requests) * 100
                if success_rate < 70:
                    # 如果成功率低于70%，增加请求间隔
                    MIN_REQUEST_INTERVAL = min(60, MIN_REQUEST_INTERVAL + 5)
                    self.console.log(f"[yellow]成功率低于70%，已增加请求间隔至{MIN_REQUEST_INTERVAL}秒")
                elif success_rate > 95 and MIN_REQUEST_INTERVAL > 10:
                    # 如果成功率高于95%，可以适当减少请求间隔，但不低于10秒
                    MIN_REQUEST_INTERVAL = max(10, MIN_REQUEST_INTERVAL - 2)
                    self.console.log(f"[green]成功率高于95%，已减少请求间隔至{MIN_REQUEST_INTERVAL}秒")
        
        # 如果有论文数据，更新本地数据库
        if paper_records:
            papers_to_update = [record.paper for record in paper_records]
            self.db.add_papers(papers_to_update)
            self.console.log(f"[green]已更新{len(papers_to_update)}篇论文到本地数据库")
        
        # 输出最终统计
        if total_requests > 0:
            final_success_rate = (successful_requests / total_requests) * 100
            self.console.log(f"[bold blue]请求统计: 总计{total_requests}次请求，成功{successful_requests}次，成功率{final_success_rate:.1f}%")
        
        return papers_with_references
    
    def load_title_mapping(self):
        """加载论文标题到ID的映射，如果不存在则返回空字典"""
        import json
        from pathlib import Path
        
        # 创建映射存储目录
        mapping_dir = Path("./data")
        mapping_dir.mkdir(exist_ok=True, parents=True)
        
        # 映射文件路径
        mapping_file = mapping_dir / "paper_title_mapping.json"
        
        # 读取现有映射（如果存在）
        existing_mapping = {}
        if mapping_file.exists():
            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    existing_mapping = json.load(f)
                self.console.log(f"[green]成功加载现有映射，包含{len(existing_mapping)}个论文")
            except Exception as e:
                self.console.log(f"[yellow]读取现有映射失败: {str(e)}")
        else:
            self.console.log("[blue]未找到现有映射文件，将创建新映射")
            
        return existing_mapping

    def save_title_mapping(self, title_mapping):
        """保存论文标题到ID的映射"""
        import json
        from pathlib import Path
        
        # 创建映射存储目录
        mapping_dir = Path("./data")
        mapping_dir.mkdir(exist_ok=True, parents=True)
        
        # 保存到文件
        mapping_file = mapping_dir / "paper_title_mapping.json"
        
        try:
            with open(mapping_file, 'w', encoding='utf-8') as f:
                json.dump(title_mapping, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.console.log(f"[red]保存映射失败: {str(e)}")
            
        return len(title_mapping)

    def to_csv(self, output_dir="./output_llms", filename_format="%Y-%m-%d", header=True, csv_config={}):
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        csv_table = {
            "Title": lambda record: record.paper.title,
            "Interest": lambda record: ("chosen" if record.comment == "-" else "filtered"),
            "Title Translated": lambda record: (
                record.paper.title_translated if record.paper.title_translated else "-"
            ),
            "Categories": lambda record: ",".join(record.paper.categories),
            "Authors": lambda record: record.paper.authors,
            "URL": lambda record: record.paper.url,
            "PapersCool": lambda record: record.paper.url.replace("https://arxiv.org/abs", "https://papers.cool/arxiv"),
            "First Submitted Date": lambda record: record.paper.first_submitted_date.strftime("%Y-%m-%d"),
            "First Announced Date": lambda record: record.paper.first_announced_date.strftime("%Y-%m-%d"),
            "Abstract": lambda record: record.paper.abstract,
            "Abstract Translated": lambda record: (
                record.paper.abstract_translated if record.paper.abstract_translated else "-"
            ),
            "Comments": lambda record: record.paper.comments,
            "Note": lambda record: record.comment,
        }

        headers = list(csv_table.keys())

        for i in range(self.date_range_days):
            current = self.date_from + timedelta(days=i)
            current_filename = current.strftime(filename_format)

            with open(output_dir / f"{current_filename}.csv", "w", encoding="utf-8") as file:
                if "lineterminator" not in csv_config:
                    csv_config["lineterminator"] = "\n"
                writer = csv.writer(file, **csv_config)
                if header:
                    writer.writerow(headers)

                papers = self.db.fetch_papers_on_date(current)
                chosen_records, filtered_records = self.filter_papers(papers)
                for record in chosen_records + filtered_records:
                    writer.writerow([fn(record) for fn in csv_table.values()])

                self.console.log(
                    f"[bold green]Output {current_filename}.csv completed. {len(chosen_records)} papers chosen, {len(filtered_records)} papers filtered"
                )


def extract_arxiv_id_from_url(url):
    """从完整的arXiv URL中提取论文ID"""
    match = re.search(r'arxiv\.org/abs/(\d+\.\d+)', url)
    if match:
        return match.group(1)
    return None


def parse_arxiv_id(arxiv_id):
    """解析arXiv ID，支持新旧格式"""
    # 检查是否是完整URL
    if arxiv_id.startswith(('http://', 'https://')):
        extracted_id = extract_arxiv_id_from_url(arxiv_id)
        if extracted_id:
            arxiv_id = extracted_id
        else:
            return None, None
    
    # 尝试匹配常规格式 YYMM.数字
    match = re.match(r'(\d{4})\.(\d+)', arxiv_id)
    if match:
        year_month = match.group(1)
        numbers = match.group(2)
        
        # 检查是否是新格式 (如 2505.16672，表示2025年5月)
        if year_month[:2] in ['20', '21', '22', '23', '24', '25', '26', '27', '28', '29']:
            year = '20' + year_month[:2]  # 提取完整年份
            return year, year_month + numbers
        # 旧格式，年份是yy形式
        else:
            return '20' + year_month[:2], year_month + numbers
    return None, None


def get_author_initial(author_list):
    """获取第一位作者姓氏的首字母"""
    if not author_list:
        return None
    
    first_author = author_list[0]
    # 假设姓在最后
    parts = first_author.split()
    surname = parts[-1]
    return surname[0].upper()


def construct_ads_url(arxiv_id, author_list, page_type="abstract"):
    """根据arXiv ID和作者列表构造ADS URL
    
    page_type: 页面类型，可以是 "abstract" 或 "references"
    """
    year, id_without_dot = parse_arxiv_id(arxiv_id)
    if not year or not id_without_dot:
        return None, None
    
    author_initial = get_author_initial(author_list)
    if not author_initial:
        return None, None
    
    # 构造基础ADS URL
    base_url = f"{year}arXiv{id_without_dot}{author_initial}"
    
    # 根据page_type构造完整URL
    url = f"https://ui.adsabs.harvard.edu/abs/{base_url}/{page_type}"
    return url, base_url


def get_references_from_ads_api(bibcode, api_token, max_retries=5):
    """使用ADS API获取参考文献列表，遇到429错误时自动重试"""
    api_url = "https://api.adsabs.harvard.edu/v1/search/query"
    params = {
        'q': f'references(bibcode:{bibcode})',
        'fl': 'title,author,year,pub,bibcode,doi',
        'rows': 50,
        'sort': 'year desc'
    }
    
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json'
    }
    
    retries = 0
    while retries <= max_retries:
        try:
            response = requests.get(api_url, params=params, headers=headers)
            
            # 检查是否为429错误（请求过多）
            if response.status_code == 429:
                retries += 1
                wait_time = 30  # 等待30秒
                print(f"遇到API限流(429)，等待{wait_time}秒后重试... (尝试 {retries}/{max_retries})")
                time.sleep(wait_time)
                continue
                
            # 其他错误状态码处理
            if response.status_code != 200:
                print(f"ADS API请求失败，状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return None
            
            data = response.json()
            if 'response' not in data or 'docs' not in data['response']:
                print("API响应格式不正确")
                return None
            
            docs = data['response']['docs']
            if not docs:
                print("未找到参考文献")
                return None
            
            references_list = []
            for doc in docs:
                title = doc.get('title', ['无标题'])[0] if 'title' in doc and doc['title'] else '无标题'
                authors = ', '.join(doc.get('author', ['无作者'])) if 'author' in doc and doc['author'] else '无作者'
                year = doc.get('year', '无年份') if 'year' in doc else '无年份'
                pub = doc.get('pub', '无出版信息') if 'pub' in doc else '无出版信息'
                bibcode = doc.get('bibcode', '无bibcode') if 'bibcode' in doc else '无bibcode'
                doi = doc.get('doi', ['无DOI'])[0] if 'doi' in doc and doc['doi'] else '无DOI'
                
                reference = {
                    "external_id": "",  # 不使用external_id
                    "title": title,
                    "doi": doi if doi != '无DOI' else "",
                    "source": "ads",
                    "publication": pub if pub != '无出版信息' else "",
                    "data_source": "ads",
                    "year": year if year != '无年份' else ""
                }
                references_list.append(reference)
            
            return references_list
            
        except Exception as e:
            print(f"使用ADS API获取参考文献时出错: {e}")
            return None
    
    print(f"达到最大重试次数({max_retries})，未能获取参考文献")
    return None


def crawl_ads_page(url, max_retries=5, retry_delay=30):
    """爬取ADS页面内容，遇到429错误时自动重试
    
    Args:
        url: 要爬取的URL
        max_retries: 最大重试次数
        retry_delay: 重试前等待的秒数
    
    Returns:
        成功时返回页面内容，失败时返回None
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    retries = 0
    while retries <= max_retries:
        try:
            response = requests.get(url, headers=headers)
            
            # 检查是否为429错误（请求过多）
            if response.status_code == 429:
                retries += 1
                wait_time = retry_delay  # 等待指定秒数
                print(f"爬取页面时遇到API限流(429)，等待{wait_time}秒后重试... (尝试 {retries}/{max_retries})")
                time.sleep(wait_time)
                continue
            
            # 其他错误状态码
            if response.status_code != 200:
                print(f"爬取页面失败，状态码: {response.status_code}")
                return None
                
            response.raise_for_status()  # 如果请求失败则抛出异常
            return response.text
            
        except requests.exceptions.RequestException as e:
            if "429" in str(e):
                retries += 1
                wait_time = retry_delay
                print(f"爬取页面时遇到API限流(429)，等待{wait_time}秒后重试... (尝试 {retries}/{max_retries})")
                time.sleep(wait_time)
                continue
            print(f"爬取页面时出错: {e}")
            return None
    
    print(f"达到最大重试次数({max_retries})，放弃爬取页面")
    return None


def extract_bibcode_from_html(html_content):
    """从HTML中提取论文的bibcode或arXiv ID"""
    soup = BeautifulSoup(html_content, 'html.parser')
    meta_bibcode = soup.find('meta', {'name': 'citation_arxiv_id'})
    
    if not meta_bibcode:
        print("未在HTML中找到bibcode")
        return None
    
    bibcode_value = meta_bibcode.get('content', '')
    # 有时bibcode是完整的arXiv ID，例如 arXiv:2505.16672
    if bibcode_value.startswith('arXiv:'):
        # 只返回arXiv ID部分，去掉前缀
        return bibcode_value[6:]
    
    # 在<meta>标签中查找其他可能的bibcode
    for meta in soup.find_all('meta'):
        if 'name' in meta.attrs and meta['name'] == 'dc.identifier':
            value = meta.get('content', '')
            if value.startswith('doi:'):
                continue
            if value.startswith('arXiv:'):
                return value[6:]  # 去掉arXiv:前缀
            return value
    
    # 从URL中提取
    canonical = soup.find('link', {'rel': 'canonical'})
    if canonical and 'href' in canonical.attrs:
        match = re.search(r'abs/([^/]+)', canonical['href'])
        if match:
            return match.group(1)
    
    return None


if __name__ == "__main__":
    from datetime import date, timedelta

    today = date.today()

    exporter = PaperExporter(today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
    exporter.to_markdown()
    # exporter.to_csv(csv_config=dict(delimiter="\t", header=False))
    exporter.to_csv(header=False, csv_config=dict(delimiter="\t"))
