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
import re
import urllib3
import os
import time
from bs4 import BeautifulSoup
import tempfile
import io
import fitz  # PyMuPDF
from PIL import Image
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

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
    thumbnail_path: str | None = None

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
            thumbnail_path=row["thumbnail_path"] if "thumbnail_path" in row.keys() else None,
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
                    paper_references TEXT,
                    thumbnail_path TEXT
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
                    paper.thumbnail_path,
                    datetime.now(UTC).replace(tzinfo=None)
                )
                for paper in papers
            ]
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO papers 
                (url, authors, abstract, title, categories, first_submitted_date, first_announced_date, title_translated, abstract_translated, comments, paper_references, thumbnail_path, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        """将论文记录保存到图数据库，生成缩略图，但不获取引用和参考文献"""
        import requests
        import uuid
        import re
        import json
        import time
        import urllib3
        import os
        import tempfile
        from datetime import datetime
        from pathlib import Path
        
        api_base_url = "http://localhost:8080/neo4j/v1"  # 图数据库API地址
        http = urllib3.PoolManager()
        
        # AWS S3配置信息
        AWS_CONFIG = {
            "access_key": "AKIARLE6T3XC3DMAINFC",
            "secret_key": "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",
            "bucket_name": "dcnftmetadata",
            "endpoint": "https://s3.ap-southeast-1.amazonaws.com",
            "pub_url": "https://dcnftmetadata.s3.ap-southeast-1.amazonaws.com",
            "region": "ap-southeast-1",
            "upload_dir": "paper_thumbnails"  # 存储缩略图的目录
        }

        # 保存所有论文的映射 {标题: 论文ID}
        paper_title_to_id = self.load_title_mapping()  # 加载现有映射
        
        # 创建标准化映射（所有键转为小写）
        normalized_title_to_id = {title.lower().strip(): paper_id for title, paper_id in paper_title_to_id.items()}
        
        papers_with_references = []
        
        # 创建缓存目录
        cache_dir = Path("./cache/references")
        cache_dir.mkdir(exist_ok=True, parents=True)
        
        # 引用数据缓存目录
        citations_cache_dir = Path("./cache/citations")
        citations_cache_dir.mkdir(exist_ok=True, parents=True)
        
        # 缩略图缓存目录
        thumbnails_dir = Path("./cache/thumbnails")
        thumbnails_dir.mkdir(exist_ok=True, parents=True)
        
        # 全局限速设置
        PAPER_INTERVAL = 8  # 处理下一篇论文前等待的时间（秒）
        API_REQUEST_INTERVAL = 1  # 同一篇论文内两次API请求之间的间隔（秒）
        RETRY_INTERVAL = 30  # 遇到429错误时的重试间隔（秒）
        MAX_RETRIES = 99  # 最大重试次数
        last_paper_time = 0  # 上一篇论文处理的时间戳
        
        # 请求计数和成功率统计
        total_requests = 0
        successful_requests = 0
        
        def ensure_paper_rate_limit():
            """确保不同论文之间的请求间隔"""
            nonlocal last_paper_time
            current_time = time.time()
            elapsed = current_time - last_paper_time
            
            if last_paper_time > 0 and elapsed < PAPER_INTERVAL:
                wait_time = PAPER_INTERVAL - elapsed
                self.console.log(f"[yellow]论文处理间隔: 等待{wait_time:.1f}秒...")
                time.sleep(wait_time)
            
            last_paper_time = time.time()
        
        def upload_to_s3(image_bytes, filename, content_type='image/jpeg'):
            """上传图片到AWS S3
            
            Args:
                image_bytes: 图片的二进制数据
                filename: 文件名
                content_type: 内容类型，默认为image/jpeg
                
            Returns:
                str: 上传成功后的URL，失败则返回None
            """
            try:
                # 创建S3客户端
                s3_client = boto3.client(
                    's3',
                    region_name=AWS_CONFIG["region"],
                    endpoint_url=AWS_CONFIG["endpoint"],
                    aws_access_key_id=AWS_CONFIG["access_key"],
                    aws_secret_access_key=AWS_CONFIG["secret_key"]
                )
                
                # 生成S3存储路径
                file_key = f"{AWS_CONFIG['upload_dir']}/{filename}"
                
                # 上传文件
                self.console.log(f"[blue]开始上传缩略图到S3: {file_key}")
                s3_client.put_object(
                    Bucket=AWS_CONFIG["bucket_name"],
                    Key=file_key,
                    Body=image_bytes,
                    ContentType=content_type
                )
                
                # 生成公共URL
                file_url = f"{AWS_CONFIG['pub_url']}/{file_key}"
                self.console.log(f"[green]缩略图上传成功: {file_url}")
                return file_url
                
            except ClientError as e:
                self.console.log(f"[red]上传到S3失败: {str(e)}")
                return None
            except Exception as e:
                self.console.log(f"[red]上传过程中发生错误: {str(e)}")
                import traceback
                self.console.log(traceback.format_exc())
                return None

        def generate_pdf_thumbnail(pdf_url, arxiv_id, title, size=(300, 300), upload_to_aws=True, max_retries=3, retry_delay=10):
            """生成PDF首页缩略图并上传到AWS S3
            
            Args:
                pdf_url: PDF的URL
                arxiv_id: 论文的arxiv ID
                title: 论文标题
                size: 缩略图尺寸，默认300x300
                upload_to_aws: 是否上传到AWS S3
                max_retries: PDF下载最大重试次数
                retry_delay: 重试等待时间(秒)
                
            Returns:
                tuple: (成功标志, 缩略图URL或错误信息)
            """
            # 缓存文件路径（用于检查是否已生成过）
            cache_info_path = thumbnails_dir / f"{arxiv_id}_thumbnail_info.json"
            
            # 检查缓存，如果已上传过，直接返回URL
            if cache_info_path.exists():
                try:
                    with open(cache_info_path, 'r') as f:
                        cache_data = json.load(f)
                    if 's3_url' in cache_data:
                        self.console.log(f"[green]缩略图已上传到S3，跳过生成: {title}")
                        return True, cache_data['s3_url']
                    elif 'thumbnail_path' in cache_data and not upload_to_aws:
                        self.console.log(f"[green]缩略图已存在，跳过生成: {title}")
                        return True, cache_data['thumbnail_path']
                except Exception as e:
                    self.console.log(f"[yellow]读取缩略图缓存信息失败: {e}")
            
            retries = 0
            last_error = None
            
            while retries <= max_retries:
                try:
                    self.console.log(f"[blue]开始下载PDF用于生成缩略图: {pdf_url}" + (f" (尝试 {retries+1}/{max_retries+1})" if retries > 0 else ""))
                    
                    # 确保PDF URL以.pdf结尾
                    if not pdf_url.endswith('.pdf'):
                        pdf_url = f"{pdf_url}.pdf"
                        
                    # 下载PDF（带重试）
                    session = requests.Session()
                    # 设置超时时间和重试策略
                    retry_strategy = urllib3.Retry(
                        total=3,  # 内部重试3次
                        backoff_factor=1,
                        status_forcelist=[429, 500, 502, 503, 504],
                        allowed_methods=["GET"]
                    )
                    adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
                    session.mount("http://", adapter)
                    session.mount("https://", adapter)
                    
                    # 使用会话下载PDF
                    response = session.get(pdf_url, stream=True, timeout=(10, 30))  # 连接超时10秒，读取超时30秒
                    
                    if response.status_code != 200:
                        self.console.log(f"[yellow]下载PDF失败，状态码: {response.status_code}，尝试重试...")
                        retries += 1
                        time.sleep(retry_delay)
                        continue
                    
                    # 将PDF内容读入内存
                    pdf_bytes = io.BytesIO(response.content)
                    self.console.log(f"[green]PDF下载成功，准备生成缩略图")
                    
                    # 使用PyMuPDF打开PDF
                    try:
                        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                        if doc.page_count == 0:
                            self.console.log(f"[red]PDF没有页面")
                            return False, "PDF没有页面"
                        
                        # 获取首页
                        first_page = doc[0]
                        
                        # 获取首页图像
                        pix = first_page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 使用2倍缩放以提高质量
                        
                        # 将图像转换为PIL图像
                        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        
                        # 缩放到指定尺寸
                        img.thumbnail(size, Image.Resampling.LANCZOS)
                        
                        # 将图像保存到内存中
                        img_bytes = io.BytesIO()
                        img.save(img_bytes, format="JPEG", quality=85)
                        img_bytes.seek(0)
                        
                        # 缓存数据
                        cache_data = {
                            'title': title,
                            'arxiv_id': arxiv_id,
                            'generate_time': datetime.now().isoformat(),
                            'pdf_url': pdf_url
                        }
                        
                        if upload_to_aws:
                            # 生成唯一文件名
                            unique_filename = f"{arxiv_id}_{uuid.uuid4()}.jpg"
                            
                            # 上传到AWS S3
                            img_url = upload_to_s3(img_bytes.getvalue(), unique_filename)
                            if img_url:
                                # 更新缓存信息
                                cache_data['s3_url'] = img_url
                                
                                try:
                                    with open(cache_info_path, 'w') as f:
                                        json.dump(cache_data, f, ensure_ascii=False, indent=2)
                                except Exception as e:
                                    self.console.log(f"[yellow]保存缩略图缓存信息失败: {e}")
                                
                                return True, img_url
                            else:
                                return False, "上传到AWS S3失败"
                        else:
                            # 保存到本地
                            thumbnail_path = thumbnails_dir / f"{arxiv_id}_thumbnail.jpg"
                            
                            with open(thumbnail_path, "wb") as f:
                                f.write(img_bytes.getvalue())
                            
                            self.console.log(f"[green]缩略图生成成功: {thumbnail_path}")
                            
                            # 更新缓存信息
                            cache_data['thumbnail_path'] = str(thumbnail_path)
                            
                            try:
                                with open(cache_info_path, 'w') as f:
                                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                            except Exception as e:
                                self.console.log(f"[yellow]保存缩略图缓存信息失败: {e}")
                            
                            return True, str(thumbnail_path)
                        
                    except Exception as e:
                        self.console.log(f"[red]生成缩略图时出错: {str(e)}")
                        import traceback
                        self.console.log(traceback.format_exc())
                        return False, f"生成缩略图失败: {str(e)}"
                    
                except (requests.exceptions.SSLError, 
                        requests.exceptions.ConnectionError, 
                        requests.exceptions.Timeout,
                        requests.exceptions.RequestException) as e:
                    last_error = str(e)
                    retries += 1
                    if retries <= max_retries:
                        self.console.log(f"[yellow]下载PDF时遇到网络错误: {last_error}")
                        self.console.log(f"[yellow]等待{retry_delay}秒后重试... (尝试 {retries}/{max_retries})")
                        time.sleep(retry_delay)
                    else:
                        self.console.log(f"[red]达到最大重试次数({max_retries})，PDF下载失败")
                        break
                except Exception as e:
                    self.console.log(f"[red]下载PDF或生成缩略图过程中出错: {str(e)}")
                    import traceback
                    self.console.log(traceback.format_exc())
                    last_error = str(e)
                    retries += 1
                    if retries <= max_retries:
                        self.console.log(f"[yellow]等待{retry_delay}秒后重试... (尝试 {retries}/{max_retries})")
                        time.sleep(retry_delay)
                    else:
                        break
            
            return False, f"处理失败: {last_error}"
        
        def safe_api_call(url, params=None, retry_count=0):
            """安全调用API，处理429错误"""
            nonlocal total_requests, successful_requests
            
            if retry_count >= MAX_RETRIES:
                self.console.log(f"[red]达到最大重试次数({MAX_RETRIES})，放弃请求: {url}")
                return None
            
            # 增加请求计数
            total_requests += 1
            
            try:
                response = requests.get(url, params=params)
                
                # 处理429错误（请求过多）
                if response.status_code == 429:
                    retry_count += 1
                    self.console.log(f"[yellow]API限流(429)，等待{RETRY_INTERVAL}秒后重试... (尝试 {retry_count}/{MAX_RETRIES})")
                    time.sleep(RETRY_INTERVAL)
                    return safe_api_call(url, params, retry_count)
                
                # 如果请求成功
                if response.status_code == 200:
                    successful_requests += 1
                    return response.json()
                else:
                    self.console.log(f"[red]API请求失败，状态码: {response.status_code}，URL: {url}")
                    self.console.log(f"[red]响应内容: {response.text}")
                    return None
                
            except Exception as e:
                self.console.log(f"[red]API请求出错: {str(e)}")
                return None

        # 处理每一篇论文
        for paper_record in paper_records:
            paper = paper_record.paper
            paper_uuid = str(uuid.uuid4())
            
            self.console.log(f"[blue]===== 处理论文: {paper.title} =====")
            ensure_paper_rate_limit()
            
            # 检查标题映射中是否已有此论文（不区分大小写）
            paper_title_lower = paper.title.lower().strip()
            
            if paper_title_lower in normalized_title_to_id:
                paper_id = normalized_title_to_id[paper_title_lower]
                self.console.log(f"[green]论文已存在于映射中，跳过处理: {paper.title} (ID: {paper_id})")
                continue

            # 生成缩略图
            if paper.url and "arxiv.org/abs/" in paper.url:
                match = re.search(r'arxiv\.org/abs/([^/]+)', paper.url)
                if match:
                    arxiv_id = match.group(1)
                    
                    # 构建PDF URL
                    pdf_url = paper.url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"
                    
                    # 生成PDF首页缩略图并上传到AWS S3
                    thumbnail_success, thumbnail_result = generate_pdf_thumbnail(pdf_url, arxiv_id, paper.title, upload_to_aws=True)
                    if thumbnail_success:
                        paper.thumbnail_path = thumbnail_result
                        self.console.log(f"[green]缩略图处理成功，URL: {thumbnail_result}")
                    else:
                        self.console.log(f"[yellow]缩略图处理失败: {thumbnail_result}")
            
            # 构建DOI
            doi = f"10.48550/arXiv.{arxiv_id}" if 'arxiv_id' in locals() and arxiv_id else ""
            
            # 从categories中提取关键词
            keywords = []
            for category in paper.categories:
                keywords.append(category)

            # 处理作者列表
            authors_list = [author_name.strip() for author_name in paper.authors.split(',') if author_name.strip()]
            
            print(" paper.thumbnail_path = ", paper.thumbnail_path)

            # 创建论文数据
            paper_data = {
                "paper": {
                    "id": paper_uuid,  # 使用提前生成的UUID
                    "title": paper.title,
                    "abstract": paper.abstract,
                    "doi": doi,
                    "url": pdf_url if 'pdf_url' in locals() else paper.url,  # 使用PDF URL如果有的话
                    "citations": 0,  # 先设置为0
                    "referencescount": 0,  # 先设置为0
                    "published_at": paper.first_submitted_date.isoformat() + "Z",
                    "keywords": keywords,
                    "venue_id": "arxiv",
                    "venue_name": "arXiv",
                    "external_id": "",  # 外部ID暂时为空
                    "data_source": "arxiv",  # 设置数据来源为arxiv
                    "img_url": paper.thumbnail_path if paper.thumbnail_path else ""
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
            
            try:
                # 创建论文
                full_url = f"{api_base_url}/papers/create"
                encoded_data = json.dumps(paper_data).encode('utf-8')
                
                # 发送请求
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
                normalized_title_to_id[paper_title_lower] = paper_id
                
                # 不再需要单独更新缩略图URL，因为已经在创建时包含了
                
                # 每篇论文处理完成后立即更新映射文件
                self.save_title_mapping(normalized_title_to_id)
                self.console.log(f"[green]已更新论文映射 (当前总计: {len(normalized_title_to_id)}篇)")
                
            except Exception as e:
                self.console.log(f"[bold red]保存论文到图数据库失败: {str(e)}")
                import traceback
                self.console.log(traceback.format_exc())
                continue
            
            # 将此论文的ID和标题添加到papers_with_references列表，供后续处理
            papers_with_references.append({
                "id": paper_id,
                "title": paper.title,
                "arxiv_id": arxiv_id if 'arxiv_id' in locals() else None
            })
        
        # 在处理完所有论文后，保存论文ID映射到文件，以便后续引用和参考文献处理
        references_data_path = Path("./data/papers_for_references.json")
        try:
            # 检查文件是否存在
            existing_data = []
            if references_data_path.exists():
                with open(references_data_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            
            # 合并新数据，避免重复
            existing_ids = {paper["id"] for paper in existing_data}
            for paper in papers_with_references:
                if paper["id"] not in existing_ids:
                    existing_data.append(paper)
            
            # 保存到文件
            with open(references_data_path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            self.console.log(f"[green]已保存{len(papers_with_references)}篇论文信息到 {references_data_path}，供后续处理引用和参考文献")
        except Exception as e:
            self.console.log(f"[red]保存论文信息到引用处理文件时出错: {str(e)}")
            import traceback
            self.console.log(traceback.format_exc())
        
        # 在处理完所有论文后，保存更新后的论文数据到数据库
        self.console.log(f"[green]保存更新后的论文数据到数据库...")
        db = PaperDatabase()
        papers_to_update = []
        
        for paper_record in paper_records:
            papers_to_update.append(paper_record.paper)
        
        db.add_papers(papers_to_update)
        self.console.log(f"[green]数据库更新完成，共保存{len(papers_to_update)}篇论文")
        
        # 创建新的映射（从规范化的映射）
        new_mapping = {}
        for title_lower, paper_id in normalized_title_to_id.items():
            # 尝试找到原始大小写的标题
            original_title = None
            for paper_record in paper_records:
                if paper_record.paper.title.lower().strip() == title_lower:
                    original_title = paper_record.paper.title
                    break
            
            # 如果找不到原始标题，使用小写版本
            if not original_title:
                original_title = title_lower
            
            new_mapping[original_title] = paper_id
        
        # 保存标题映射
        self.save_title_mapping(new_mapping)
        
        # 打印统计信息
        if total_requests > 0:
            success_rate = (successful_requests / total_requests) * 100
            self.console.log(f"[blue]API请求统计: 总计{total_requests}次，成功{successful_requests}次，成功率{success_rate:.2f}%")
        
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



if __name__ == "__main__":
    from datetime import date, timedelta

    today = date.today()

    exporter = PaperExporter(today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
    exporter.to_markdown()
    # exporter.to_csv(csv_config=dict(delimiter="\t", header=False))
    exporter.to_csv(header=False, csv_config=dict(delimiter="\t"))
