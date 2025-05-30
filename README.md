# arXiv论文爬虫与图数据库存储工具

这是一个用于抓取arXiv论文信息，生成缩略图，并将信息存入图数据库的工具。该工具还支持获取论文的引用和参考文献信息。

## 项目架构

项目主要包含两个核心组件：

1. **主爬虫程序** (paper.py): 负责从arXiv获取论文基本信息，生成PDF首页缩略图并上传到AWS S3，将基本信息存入SQLite数据库和Neo4j图数据库。

2. **引用获取程序** (fetch_references.py): 独立于主爬虫的组件，负责从Semantic Scholar API获取论文的引用和参考文献，并更新到Neo4j图数据库。

这种分离设计的目的是防止API限流影响主爬虫的正常工作。Semantic Scholar API经常会返回429（请求过多）错误，影响整个爬取流程。

## 使用方法

### 基础爬虫

主爬虫程序用于获取论文基本信息和生成缩略图：

```bash
python paper.py
```

默认情况下，它会获取当天的论文。你也可以通过自定义代码设置日期范围：

```python
from datetime import date
from paper import PaperExporter

# 获取指定日期范围的论文
exporter = PaperExporter("2023-01-01", "2023-01-07")
exporter.to_markdown()  # 输出为Markdown格式
# 或者输出为CSV格式
exporter.to_csv()
```

### 引用获取

完成基础爬虫工作后，可以单独运行引用获取程序：

```bash
python fetch_references.py
```

该程序会：
1. 读取`./data/papers_for_references.json`中的论文信息
2. 对每篇论文，尝试从Semantic Scholar API获取引用和参考文献
3. 保存到缓存中，并更新到Neo4j图数据库

### 缓存机制

两个组件都使用缓存来避免重复请求：

- **缩略图缓存**: `./cache/thumbnails/`
- **参考文献缓存**: `./cache/references/`
- **引用缓存**: `./cache/citations/`

缓存使用JSON格式存储，按arxiv_id命名。

## 配置选项

### AWS S3配置

在`paper.py`中修改AWS_CONFIG字典：

```python
AWS_CONFIG = {
    "access_key": "YOUR_ACCESS_KEY",
    "secret_key": "YOUR_SECRET_KEY",
    "bucket_name": "YOUR_BUCKET_NAME",
    "endpoint": "https://s3.your-region.amazonaws.com",
    "pub_url": "https://your-bucket.s3.your-region.amazonaws.com",
    "region": "your-region",
    "upload_dir": "paper_thumbnails"
}
```

### API速率限制

在`fetch_references.py`中可以调整API访问速率：

```python
RETRY_INTERVAL = 60  # 遇到429错误时的重试间隔（秒）
MAX_RETRIES = 99     # 最大重试次数
PAPER_INTERVAL = 5   # 处理下一篇论文前等待的时间（秒）
API_REQUEST_INTERVAL = 2  # 同一篇论文内两次API请求之间的间隔（秒）
```

## 依赖项

- Python 3.9+
- PyMuPDF (fitz)
- Pillow
- boto3
- requests
- rich
- SQLite3 (Python内置)
- aiohttp (用于异步翻译)

可以通过以下命令安装依赖：

```bash
pip install pymupdf pillow boto3 requests rich aiohttp
```

## 注意事项

1. 主爬虫和引用获取程序可以独立运行，互不影响
2. 由于Semantic Scholar API限流严格，引用获取程序可能需要较长时间完成
3. 确保Neo4j图数据库服务在`http://localhost:8080/neo4j/v1`可访问 