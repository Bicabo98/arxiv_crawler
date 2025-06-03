# 导入必要的库
from semanticscholar import SemanticScholar
import requests
import time  # 导入time模块以便使用sleep

# 创建SemanticScholar客户端
sch = SemanticScholar()

# 论文标题
paper_title = "BPMN to Smart Contract by Business Analyst"

i = 0
max_attempts = 10

while i < max_attempts:
    # 使用计数器i+1来显示当前是第几次尝试
    print(f"--- 第 {i+1} 次尝试 ---")
    
    # 使用requests库直接调用match API
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search/match"
    search_params = {"query": paper_title}
    
    response = requests.get(base_url, params=search_params)

    # 检查状态码
    if response.status_code == 429:
        print("搜索API遇到429状态码，等待10秒后重试...")
        time.sleep(10)  # 等待10秒
        continue  # 继续当前循环，不增加计数器i

    # 打印搜索API的状态码
    print(f"搜索API状态码: {response.status_code}")

    try:
        match_data = response.json()

        # 检查返回的数据结构
        if "data" in match_data and match_data["data"]:
            paper_id = match_data["data"][0]["paperId"]
            print(f"匹配的论文ID: {paper_id}")
            
            # 使用获取到的ID通过SemanticScholar库获取完整论文信息
            paper_details_url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
            fields = "title,authors,abstract,citationCount,referenceCount,year,venue,fieldsOfStudy,openAccessPdf,s2FieldsOfStudy,citations,references"
            details_params = {"fields": fields}
            
            # 调用paper_details_url并检查状态码
            details_response = requests.get(paper_details_url, params=details_params)

            # 检查状态码
            if details_response.status_code == 429:
                print("详情API遇到429状态码，等待10秒后重试...")
                time.sleep(10)  # 等待10秒
                continue  # 继续当前循环，不增加计数器i

            # 打印状态码
            print(f"详情API状态码: {details_response.status_code}")
            
            try:
                paper_details = details_response.json()

                # 打印结果
                print("标题=", paper_details.get("title"))
                print("作者=", paper_details.get("authors"))
                abstract = paper_details.get("abstract")
                if abstract is None:
                    print("摘要= 无摘要信息")
                else:
                    print("摘要=", abstract[:100] + "...")
                print("引用数=", paper_details.get("citationCount"))
                print("参考数=", paper_details.get("referenceCount"))
                print("出版时间=", paper_details.get("year"))
                print("期刊=", paper_details.get("venue"))
                print("研究领域=", paper_details.get("fieldsOfStudy"))

                print("这篇论文引用的论文=", paper_details.get("citations"))
                print("这篇论文参考的其他论文=", paper_details.get("references"))
                
                # 成功获取所有数据后增加计数器
                i += 1
            except Exception as e:
                print(f"解析论文详情时出错: {str(e)}")
                i += 1  # 出错也算一次尝试
        else:
            print("没有找到匹配的论文或返回的数据格式不正确。")
            i += 1  # 没找到匹配也算一次尝试
    except Exception as e:
        print(f"处理搜索结果时出错: {str(e)}")
        i += 1  # 处理搜索结果出错也算一次尝试
        
    # 在每次迭代结束后添加一些间隔时间以避免频繁请求
    if i < max_attempts:  # 如果不是最后一次迭代
        time.sleep(2)  # 等待2秒再进行下一次调用