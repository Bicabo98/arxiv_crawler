# # 导入必要的库
from semanticscholar import SemanticScholar
import requests

# # 创建SemanticScholar客户端
# sch = SemanticScholar()

# # 论文标题
# paper_title = "Understanding the Evolution of Blockchain Ecosystems: A Longitudinal Measurement Study of Bitcoin, Ethereum, and EOSIO"

# # 使用requests库直接调用match API
# base_url = "https://api.semanticscholar.org/graph/v1/paper/search/match"
# params = {"query": paper_title}


# response = requests.get(base_url, params=params)
# match_data = response.json()

# # 获取匹配的论文ID
# if match_data["data"] and len(match_data["data"]) > 0:
#     paper_id = match_data["data"][0]["paperId"]
#     print(f"匹配的论文ID: {paper_id}")
    
#     # 使用获取到的ID通过SemanticScholar库获取完整论文信息
#     paper_details_url = f"https://api.semanticscholar.org/graph/v1/paper/e869047aa62754ae678dd81e260dfbfc2fee4d03"
#     fields = "title,authors,abstract,citationCount,referenceCount,year,venue,fieldsOfStudy,openAccessPdf,s2FieldsOfStudy,citations,references"
#     params = {"fields": fields}
#     details_response = requests.get(paper_details_url, params=params)
#     paper_details = details_response.json()

#     # 打印结果
#     print("标题=", paper_details.get("title"))
#     print("作者=", paper_details.get("authors"))
#     print("摘要=", paper_details.get("abstract"))
#     print("引用数=", paper_details.get("citationCount"))
#     print("参考数=", paper_details.get("referenceCount"))
#     print("开放pdf信息=", paper_details.get("openAccessPdf"))
#     print("出版时间=", paper_details.get("year"))
#     print("期刊=", paper_details.get("venue"))
#     print("研究领域=", paper_details.get("fieldsOfStudy"))
#     print("详细研究领域=", paper_details.get("s2FieldsOfStudy"))
#     print("引用这篇论文的其他论文列表=", paper_details.get("citations"))
#     print("这篇论文引用的其他论文列表=", paper_details.get("references"))
    
# else:
#     print("没有找到匹配的论文。")


# 使用获取到的ID通过SemanticScholar库获取完整论文信息
paper_details_url = f"https://api.semanticscholar.org/graph/v1/paper/5eb013aed632b9fadd574172ecefd8414fa7196c"
fields = "title,authors,abstract,citationCount,referenceCount,year,venue,fieldsOfStudy,openAccessPdf,s2FieldsOfStudy,citations,references"
params = {"fields": fields}
details_response = requests.get(paper_details_url, params=params)
paper_details = details_response.json()

# 打印结果
print("标题=", paper_details.get("title"))
print("作者=", paper_details.get("authors"))
print("摘要=", paper_details.get("abstract"))
print("引用数=", paper_details.get("citationCount"))
print("参考数=", paper_details.get("referenceCount"))
print("开放pdf信息=", paper_details.get("openAccessPdf"))
print("出版时间=", paper_details.get("year"))
print("期刊=", paper_details.get("venue"))
print("研究领域=", paper_details.get("fieldsOfStudy"))
print("详细研究领域=", paper_details.get("s2FieldsOfStudy"))
print("引用这篇论文的其他论文列表=", paper_details.get("citations"))
print("这篇论文引用的其他论文列表=", paper_details.get("references"))