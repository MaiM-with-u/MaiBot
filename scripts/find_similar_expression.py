import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from typing import List, Dict, Tuple
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import glob
import sqlite3
import re
from datetime import datetime
import random
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config

def clean_group_name(name: str) -> str:
    """清理群组名称，只保留中文和英文字符"""
    cleaned = re.sub(r'[^\u4e00-\u9fa5a-zA-Z]', '', name)
    if not cleaned:
        cleaned = datetime.now().strftime("%Y%m%d")
    return cleaned

def get_group_name(stream_id: str) -> str:
    """从数据库中获取群组名称"""
    conn = sqlite3.connect("data/maibot.db")
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT group_name, user_nickname, platform 
        FROM chat_streams 
        WHERE stream_id = ?
    """,
        (stream_id,),
    )

    result = cursor.fetchone()
    conn.close()

    if result:
        group_name, user_nickname, platform = result
        if group_name:
            return clean_group_name(group_name)
        if user_nickname:
            return clean_group_name(user_nickname)
        if platform:
            return clean_group_name(f"{platform}{stream_id[:8]}")
    return stream_id

def load_expressions(chat_id: str) -> List[Dict]:
    """加载指定群聊的表达方式"""
    style_file = os.path.join("data", "expression", "learnt_style", str(chat_id), "expressions.json")
    
    style_exprs = []
    
    if os.path.exists(style_file):
        with open(style_file, "r", encoding="utf-8") as f:
            style_exprs = json.load(f)
            
    # 如果表达方式超过10个，随机选择10个
    if len(style_exprs) > 50:
        style_exprs = random.sample(style_exprs, 50)
        print(f"\n从 {len(style_exprs)} 个表达方式中随机选择了 10 个进行匹配")
            
    return style_exprs

def find_similar_expressions_tfidf(input_text: str, expressions: List[Dict], mode: str = "both", top_k: int = 10) -> List[Tuple[str, str, float]]:
    """使用TF-IDF方法找出与输入文本最相似的top_k个表达方式"""
    if not expressions:
        return []
        
    # 准备文本数据
    if mode == "style":
        texts = [expr['style'] for expr in expressions]
    elif mode == "situation":
        texts = [expr['situation'] for expr in expressions]
    else:  # both
        texts = [f"{expr['situation']} {expr['style']}" for expr in expressions]
        
    texts.append(input_text)  # 添加输入文本
    
    # 使用TF-IDF向量化
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(texts)
    
    # 计算余弦相似度
    similarity_matrix = cosine_similarity(tfidf_matrix)
    
    # 获取输入文本的相似度分数（最后一行）
    scores = similarity_matrix[-1][:-1]  # 排除与自身的相似度
    
    # 获取top_k的索引
    top_indices = np.argsort(scores)[::-1][:top_k]
    
    # 获取相似表达
    similar_exprs = []
    for idx in top_indices:
        if scores[idx] > 0:  # 只保留有相似度的
            similar_exprs.append((
                expressions[idx]['style'],
                expressions[idx]['situation'],
                scores[idx]
            ))
            
    return similar_exprs

async def find_similar_expressions_embedding(input_text: str, expressions: List[Dict], mode: str = "both", top_k: int = 5) -> List[Tuple[str, str, float]]:
    """使用嵌入模型找出与输入文本最相似的top_k个表达方式"""
    if not expressions:
        return []
        
    # 准备文本数据
    if mode == "style":
        texts = [expr['style'] for expr in expressions]
    elif mode == "situation":
        texts = [expr['situation'] for expr in expressions]
    else:  # both
        texts = [f"{expr['situation']} {expr['style']}" for expr in expressions]
        
    # 获取嵌入向量
    llm_request = LLMRequest(global_config.model.embedding)
    text_embeddings = []
    for text in texts:
        embedding = await llm_request.get_embedding(text)
        if embedding:
            text_embeddings.append(embedding)
            
    input_embedding = await llm_request.get_embedding(input_text)
    if not input_embedding or not text_embeddings:
        return []
        
    # 计算余弦相似度
    text_embeddings = np.array(text_embeddings)
    similarities = np.dot(text_embeddings, input_embedding) / (
        np.linalg.norm(text_embeddings, axis=1) * np.linalg.norm(input_embedding)
    )
    
    # 获取top_k的索引
    top_indices = np.argsort(similarities)[::-1][:top_k]
    
    # 获取相似表达
    similar_exprs = []
    for idx in top_indices:
        if similarities[idx] > 0:  # 只保留有相似度的
            similar_exprs.append((
                expressions[idx]['style'],
                expressions[idx]['situation'],
                similarities[idx]
            ))
            
    return similar_exprs

async def main():
    # 获取所有群聊ID
    style_dirs = glob.glob(os.path.join("data", "expression", "learnt_style", "*"))
    chat_ids = [os.path.basename(d) for d in style_dirs]
    
    if not chat_ids:
        print("没有找到任何群聊的表达方式数据")
        return
        
    print("可用的群聊:")
    for i, chat_id in enumerate(chat_ids, 1):
        group_name = get_group_name(chat_id)
        print(f"{i}. {group_name}")
        
    while True:
        try:
            choice = int(input("\n请选择要分析的群聊编号 (输入0退出): "))
            if choice == 0:
                break
            if 1 <= choice <= len(chat_ids):
                chat_id = chat_ids[choice-1]
                break
            print("无效的选择，请重试")
        except ValueError:
            print("请输入有效的数字")
            
    if choice == 0:
        return
        
    # 加载表达方式
    style_exprs = load_expressions(chat_id)
    
    group_name = get_group_name(chat_id)
    print(f"\n已选择群聊：{group_name}")
    
    # 选择匹配模式
    print("\n请选择匹配模式：")
    print("1. 匹配表达方式")
    print("2. 匹配情景")
    print("3. 两者都考虑")
    
    while True:
        try:
            mode_choice = int(input("\n请选择匹配模式 (1-3): "))
            if 1 <= mode_choice <= 3:
                break
            print("无效的选择，请重试")
        except ValueError:
            print("请输入有效的数字")
            
    mode_map = {
        1: "style",
        2: "situation",
        3: "both"
    }
    mode = mode_map[mode_choice]
    
    # 选择匹配方法
    print("\n请选择匹配方法：")
    print("1. TF-IDF方法")
    print("2. 嵌入模型方法")
    
    while True:
        try:
            method_choice = int(input("\n请选择匹配方法 (1-2): "))
            if 1 <= method_choice <= 2:
                break
            print("无效的选择，请重试")
        except ValueError:
            print("请输入有效的数字")
            
    while True:
        input_text = input("\n请输入要匹配的文本（输入q退出）: ")
        if input_text.lower() == 'q':
            break
            
        if not input_text.strip():
            continue
            
        if method_choice == 1:
            similar_exprs = find_similar_expressions_tfidf(input_text, style_exprs, mode)
        else:
            similar_exprs = await find_similar_expressions_embedding(input_text, style_exprs, mode)
        
        if similar_exprs:
            print("\n找到以下相似表达：")
            for style, situation, score in similar_exprs:
                print(f"\n\033[33m表达方式：{style}\033[0m")
                print(f"\033[32m对应情景：{situation}\033[0m")
                print(f"相似度：{score:.3f}")
                print("-" * 20)
        else:
            print("\n没有找到相似的表达方式")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main()) 