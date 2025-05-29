#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import sys
import json
import argparse
import time
from bs4 import BeautifulSoup
import uuid


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


def get_references_from_ads_api(bibcode, api_token, get_citations=False):
    """使用ADS API获取参考文献或引用列表
    
    Args:
        bibcode (str): 论文的bibcode
        api_token (str): ADS API令牌
        get_citations (bool): 是否获取引用(True)还是参考文献(False)
    
    Returns:
        list: 参考文献或引用列表，失败则返回None
    """
    if get_citations:
        api_url = "https://api.adsabs.harvard.edu/v1/search/query"
        query_type = f'citations(bibcode:{bibcode})'
    else:
        api_url = "https://api.adsabs.harvard.edu/v1/search/query"
        query_type = f'references(bibcode:{bibcode})'
    
    params = {
        'q': query_type,
        'fl': 'title,author,year,pub,bibcode,doi',
        'rows': 50,
        'sort': 'year desc'
    }
    
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(api_url, params=params, headers=headers)
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
            print(f"未找到{'引用' if get_citations else '参考文献'}")
            return None
        
        result_list = []
        for doc in docs:
            item = {
                "external_id": "",
                "title": doc.get('title', ['无标题'])[0] if 'title' in doc and doc['title'] else '无标题',
                "doi": doc.get('doi', [''])[0] if 'doi' in doc and doc['doi'] else '',
                "source": "ads",
                "publication": doc.get('pub', '') if 'pub' in doc else '',
                "data_source": "ads",
                "year": doc.get('year', '') if 'year' in doc else ''
            }
            result_list.append(item)
        
        return result_list
        
    except Exception as e:
        print(f"使用ADS API获取{'引用' if get_citations else '参考文献'}时出错: {e}")
        return None


def get_references_from_arxiv(arxiv_id):
    """直接从arXiv获取参考文献"""
    # 构造arXiv API URL
    api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code != 200:
            print(f"arXiv API请求失败，状态码: {response.status_code}")
            return None
        
        # 解析XML响应
        from xml.etree import ElementTree as ET
        root = ET.fromstring(response.text)
        
        # 提取文章信息
        namespaces = {
            'atom': 'http://www.w3.org/2005/Atom',
            'arxiv': 'http://arxiv.org/schemas/atom'
        }
        
        # 查找条目
        entry = root.find('.//atom:entry', namespaces)
        if not entry:
            print("未找到文章条目")
            return None
        
        # 提取标题、作者、摘要等信息
        title_elem = entry.find('./atom:title', namespaces)
        title = title_elem.text.strip() if title_elem is not None else "无标题"
        
        summary_elem = entry.find('./atom:summary', namespaces)
        summary = summary_elem.text.strip() if summary_elem is not None else "无摘要"
        
        authors = []
        for author_elem in entry.findall('./atom:author/atom:name', namespaces):
            if author_elem.text:
                authors.append(author_elem.text.strip())
        
        authors_str = ", ".join(authors) if authors else "无作者信息"
        
        # 查找PDF URL
        pdf_url = None
        for link in entry.findall('./atom:link', namespaces):
            if link.get('title') == 'pdf' or link.get('type') == 'application/pdf':
                pdf_url = link.get('href')
                break
        
        pdf_info = f"PDF链接: {pdf_url}" if pdf_url else "未找到PDF链接"
        
        # 查找分类信息
        categories = []
        for category in entry.findall('./arxiv:primary_category', namespaces):
            cat = category.get('term')
            if cat:
                categories.append(cat)
        
        for category in entry.findall('./atom:category', namespaces):
            cat = category.get('term')
            if cat and cat not in categories:
                categories.append(cat)
        
        categories_str = ", ".join(categories) if categories else "无分类信息"
        
        # 查找发布日期
        published_elem = entry.find('./atom:published', namespaces)
        published = published_elem.text.strip() if published_elem is not None else "无发布日期"
        
        # 查找更新日期
        updated_elem = entry.find('./atom:updated', namespaces)
        updated = updated_elem.text.strip() if updated_elem is not None else "无更新日期"
        
        # 构造arXiv文章信息
        article_info = f"标题: {title}\n作者: {authors_str}\n分类: {categories_str}\n发布日期: {published}\n更新日期: {updated}\n\n摘要:\n{summary}\n\n{pdf_info}\n\n注意: arXiv API不直接提供参考文献列表，请访问原文获取: https://arxiv.org/abs/{arxiv_id}"
        
        return article_info
        
    except Exception as e:
        print(f"从arXiv获取参考文献时出错: {e}")
        return None


def scrape_references_from_html(references_html, arxiv_id=None):
    """从HTML中尝试提取参考文献信息"""
    soup = BeautifulSoup(references_html, 'html.parser')
    
    # 查找参考文献数量
    ref_count_elem = soup.select_one('a[href*="/references"] .num-items')
    ref_count = ref_count_elem.text.strip('()') if ref_count_elem else "未知"
    
    # 查找数据源信息，如果数据是通过JavaScript加载的
    scripts = soup.find_all('script')
    refs_data = None
    
    # 从引用原始内容中构建参考文献
    direct_references = []
    
    # 从页面元数据中提取信息
    meta_references = []
    meta_tags = soup.find_all('meta')
    for meta in meta_tags:
        if 'name' in meta.attrs and meta['name'] == 'citation_reference':
            content = meta.get('content', '')
            if content:
                meta_references.append(f"引用: {content}")
    
    # 如果找到元数据中的参考文献
    if meta_references:
        return f"参考文献列表({len(meta_references)})：\n\n" + "\n\n".join(meta_references)
    
    # 如果以上方法都失败，提供一个替代方案
    arxiv_link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "https://arxiv.org"
    return f"在页面中找到{ref_count}个参考文献，但需要在完整的ADS网站上查看，可通过页面上的'Enable full ADS'按钮访问。\n\n或访问arXiv原始论文获取参考文献：{arxiv_link}"


def get_references_via_selenium(bibcode):
    """使用Selenium从ADS网站获取参考文献"""
    try:
        # 尝试导入selenium包
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print("未安装Selenium库，请使用pip install selenium安装")
        return None
    
    try:
        # 配置Chrome选项
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # 无头模式
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        # 初始化WebDriver
        driver = webdriver.Chrome(options=chrome_options)
        
        # 构造参考文献URL
        url = f"https://ui.adsabs.harvard.edu/abs/{bibcode}/references"
        
        print(f"正在使用Selenium访问: {url}")
        driver.get(url)
        
        # 等待页面加载
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "s-results-list"))
        )
        
        # 获取页面内容
        page_source = driver.page_source
        driver.quit()
        
        # 解析HTML
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # 提取参考文献
        references = []
        results_list = soup.find('div', class_='s-results-list')
        if not results_list:
            print("未找到参考文献列表")
            return None
        
        ref_items = results_list.find_all('div', class_='s-item')
        for item in ref_items:
            # 提取标题
            title_elem = item.find('a', class_='s-results-title-link')
            title = title_elem.text.strip() if title_elem else "无标题"
            
            # 提取作者
            authors_elem = item.find('div', class_='s-results-authors')
            authors = authors_elem.text.strip() if authors_elem else "无作者信息"
            
            # 提取出版信息
            pub_elem = item.find('div', class_='s-results-journal')
            pub_info = pub_elem.text.strip() if pub_elem else "无出版信息"
            
            ref_text = f"标题: {title}\n作者: {authors}\n出版信息: {pub_info}\n"
            references.append(ref_text)
        
        if references:
            return f"参考文献列表({len(references)})：\n\n" + "\n\n".join(references)
        
        return None
    
    except Exception as e:
        print(f"使用Selenium获取参考文献时出错: {e}")
        return None


def crawl_ads_page(url, max_retries=5, retry_delay=30):
    """爬取ADS页面内容，遇到429错误时自动重试"""
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


def save_to_file(content, filename="output.json"):
    """将内容保存到文件"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            if isinstance(content, (dict, list)):
                json.dump(content, f, ensure_ascii=False, indent=2)
            else:
                f.write(content)
        print(f"内容已成功保存到 {filename}")
        return True
    except IOError as e:
        print(f"保存文件时出错: {e}")
        return False


def extract_text_content(html_content):
    """从HTML中提取文本内容"""
    soup = BeautifulSoup(html_content, 'html.parser')
    return soup.get_text(separator='\n', strip=True)


def parse_authors(authors_str):
    """解析作者字符串"""
    if not authors_str:
        return []
    
    # 移除可能的方括号并分割
    authors_str = authors_str.strip('[]')
    authors = [a.strip().strip("'\"") for a in authors_str.split(',')]
    return authors


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


def main():
    parser = argparse.ArgumentParser(description='获取论文的参考文献和引用')
    parser.add_argument('--arxiv_id', '-a', type=str, required=True, help='arXiv论文ID或URL，例如：2505.16672 或 https://arxiv.org/abs/2505.16672')
    parser.add_argument('--authors', '-u', type=str, required=True, help='作者列表，例如："Yun-Cheng Tsai,Samuel Yen-Chi Chen"')
    parser.add_argument('--output', '-o', type=str, default="output.json", help='输出文件名')
    parser.add_argument('--ads_token', '-k', type=str, required=True, help='ADS API令牌')
    parser.add_argument('--only_citations', action='store_true', help='仅获取引用')
    parser.add_argument('--only_references', action='store_true', help='仅获取参考文献')
    
    args = parser.parse_args()
    
    # 验证参数
    if args.only_citations and args.only_references:
        print("错误：--only_citations 和 --only_references 不能同时指定")
        return
    
    # 解析作者列表
    author_list = [a.strip() for a in args.authors.split(',')]
    
    # 提取arXiv ID
    arxiv_id = args.arxiv_id
    if arxiv_id.startswith(('http://', 'https://')):
        extracted_id = extract_arxiv_id_from_url(arxiv_id)
        if extracted_id:
            arxiv_id = extracted_id
    
    print(f"使用arXiv ID: {arxiv_id}")
    
    # 确定需要获取的数据类型
    get_references = not args.only_citations
    get_citations = not args.only_references
    
    if get_references and get_citations:
        print("同时获取论文的参考文献和引用...")
    elif get_references:
        print("仅获取论文的参考文献...")
    else:
        print("仅获取论文的引用...")
    
    # 构造摘要页面URL
    abstract_url, base_id = construct_ads_url(arxiv_id, author_list, "abstract")
    if not abstract_url:
        print(f"无法构造有效的URL, arXiv ID: {arxiv_id}, 作者: {author_list}")
        return
    
    print(f"摘要页面URL: {abstract_url}")
    
    # 爬取摘要页面以获取bibcode
    abstract_html = crawl_ads_page(abstract_url)
    if not abstract_html:
        print("未能爬取摘要页面")
        return
    
    # 从HTML中提取arxiv ID
    extracted_arxiv_id = extract_bibcode_from_html(abstract_html)
    
    if extracted_arxiv_id:
        print(f"从HTML中提取到arXiv ID: {extracted_arxiv_id}")
        
        # 使用提取的arxiv ID和作者列表重新构造正确的bibcode
        _, new_bibcode = construct_ads_url(extracted_arxiv_id, author_list, "abstract")
        
        if new_bibcode:
            print(f"构造ADS bibcode: {new_bibcode}")
            bibcode = new_bibcode
        else:
            print(f"无法构造新的bibcode，使用原始基础ID: {base_id}")
            bibcode = base_id
    else:
        print(f"未能从HTML中提取arXiv ID，使用原始基础ID: {base_id}")
        bibcode = base_id
    
    # 初始化结果对象
    result_obj = {
        "uuid": str(uuid.uuid4()),
        "title": f"Data for arXiv:{arxiv_id}"
    }
    
    # 获取参考文献（如果需要）
    if get_references:
        print("获取参考文献中...")
        references = get_references_from_ads_api(bibcode, args.ads_token, False)
        if references:
            print(f"成功获取{len(references)}条参考文献")
            result_obj["references"] = references
        else:
            print("未能获取参考文献")
            result_obj["references"] = []
    
    # 获取引用（如果需要）
    if get_citations:
        print("获取引用中...")
        # 为了避免API限流，在两次请求之间添加短暂延迟
        time.sleep(2)
        citations = get_references_from_ads_api(bibcode, args.ads_token, True)
        if citations:
            print(f"成功获取{len(citations)}条引用")
            result_obj["citations"] = citations
        else:
            print("未能获取引用")
            result_obj["citations"] = []
    
    # 保存到文件
    save_to_file(result_obj, args.output)
    print(f"完成！数据已保存到 {args.output}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        # 示例使用
        print("使用方法示例:")
        print("python3 test2.py --arxiv_id 2505.16672 --authors \"Yun-Cheng Tsai,Samuel Yen-Chi Chen\" --ads_token YOUR_API_TOKEN")
        print("默认情况下同时获取参考文献和引用，使用以下参数选择:")
        print("  --only_citations  仅获取引用")
        print("  --only_references  仅获取参考文献")
