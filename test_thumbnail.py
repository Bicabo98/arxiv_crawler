#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试PDF缩略图生成功能并上传到AWS S3
"""

import os
import io
import sys
import time
import uuid
from pathlib import Path
import requests
from PIL import Image
import fitz  # PyMuPDF
from rich.console import Console
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

console = Console()

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
        console.log(f"[blue]开始上传缩略图到S3: {file_key}")
        s3_client.put_object(
            Bucket=AWS_CONFIG["bucket_name"],
            Key=file_key,
            Body=image_bytes,
            ContentType=content_type
        )
        
        # 生成公共URL
        file_url = f"{AWS_CONFIG['pub_url']}/{file_key}"
        console.log(f"[green]缩略图上传成功: {file_url}")
        return file_url
        
    except ClientError as e:
        console.log(f"[red]上传到S3失败: {str(e)}")
        return None
    except Exception as e:
        console.log(f"[red]上传过程中发生错误: {str(e)}")
        import traceback
        console.log(traceback.format_exc())
        return None

def generate_pdf_thumbnail(pdf_url, size=(300, 300), upload_to_aws=True):
    """生成PDF首页缩略图并上传到AWS S3
    
    Args:
        pdf_url: PDF的URL
        size: 缩略图尺寸，默认300x300
        upload_to_aws: 是否上传到AWS S3
        
    Returns:
        tuple: (成功标志, 图片URL或错误信息)
    """
    try:
        console.log(f"[blue]开始下载PDF: {pdf_url}")
        
        # 确保PDF URL以.pdf结尾
        if not pdf_url.endswith('.pdf'):
            pdf_url = f"{pdf_url}.pdf"
        
        # 下载PDF
        response = requests.get(pdf_url, stream=True)
        if response.status_code != 200:
            console.log(f"[red]下载PDF失败，状态码: {response.status_code}")
            return False, f"下载失败: {response.status_code}"
        
        # 将PDF内容读入内存
        pdf_bytes = io.BytesIO(response.content)
        console.log(f"[green]PDF下载成功，准备生成缩略图")
        
        # 使用PyMuPDF打开PDF
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.page_count == 0:
                console.log(f"[red]PDF没有页面")
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
            
            # 提取arxiv ID
            arxiv_id = pdf_url.split("/")[-1].replace(".pdf", "")
            
            if upload_to_aws:
                # 生成唯一文件名
                unique_filename = f"{arxiv_id}_{uuid.uuid4()}.jpg"
                
                # 上传到AWS S3
                img_url = upload_to_s3(img_bytes.getvalue(), unique_filename)
                if img_url:
                    return True, img_url
                else:
                    return False, "上传到AWS S3失败"
            else:
                # 保存到本地（作为备选）
                output_dir = Path("./cache/thumbnails")
                output_dir.mkdir(exist_ok=True, parents=True)
                output_path = output_dir / f"{arxiv_id}_thumbnail.jpg"
                
                with open(output_path, "wb") as f:
                    f.write(img_bytes.getvalue())
                
                console.log(f"[green]缩略图生成成功并保存到本地: {output_path}")
                return True, str(output_path)
            
        except Exception as e:
            console.log(f"[red]生成缩略图时出错: {str(e)}")
            import traceback
            console.log(traceback.format_exc())
            return False, f"生成缩略图失败: {str(e)}"
        
    except Exception as e:
        console.log(f"[red]下载PDF或生成缩略图过程中出错: {str(e)}")
        import traceback
        console.log(traceback.format_exc())
        return False, f"处理失败: {str(e)}"

def main():
    # 测试论文PDF链接
    pdf_url = "https://arxiv.org/pdf/2505.21388"
    
    # 生成缩略图并上传到AWS S3
    success, result = generate_pdf_thumbnail(pdf_url, upload_to_aws=True)
    
    if success:
        console.log(f"[green]测试成功! 缩略图URL: {result}")
    else:
        console.log(f"[red]测试失败! 错误信息: {result}")

if __name__ == "__main__":
    main() 