import os
import json
from typing import List, Dict, Tuple
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import glob
import sqlite3
import re
from datetime import datetime

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

def format_timestamp(timestamp: float) -> str:
    """将时间戳转换为可读的时间格式"""
    if not timestamp:
        return "未知"
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return "未知"

def load_expressions(chat_id: str) -> List[Dict]:
    """加载指定群聊的表达方式"""
    style_file = os.path.join("data", "expression", "learnt_style", str(chat_id), "expressions.json")
    
    style_exprs = []
    
    if os.path.exists(style_file):
        with open(style_file, "r", encoding="utf-8") as f:
            style_exprs = json.load(f)
            
    return style_exprs

def find_similar_expressions(expressions: List[Dict], top_k: int = 5) -> Dict[str, List[Tuple[str, float]]]:
    """找出每个表达方式最相似的top_k个表达方式"""
    if not expressions:
        return {}
        
    # 分别准备情景和表达方式的文本数据
    situations = [expr['situation'] for expr in expressions]
    styles = [expr['style'] for expr in expressions]
    
    # 使用TF-IDF向量化
    vectorizer = TfidfVectorizer()
    situation_matrix = vectorizer.fit_transform(situations)
    style_matrix = vectorizer.fit_transform(styles)
    
    # 计算余弦相似度
    situation_similarity = cosine_similarity(situation_matrix)
    style_similarity = cosine_similarity(style_matrix)
    
    # 对每个表达方式找出最相似的top_k个
    similar_expressions = {}
    for i, expr in enumerate(expressions):
        # 获取相似度分数
        situation_scores = situation_similarity[i]
        style_scores = style_similarity[i]
        
        # 获取top_k的索引（排除自己）
        situation_indices = np.argsort(situation_scores)[::-1][1:top_k+1]
        style_indices = np.argsort(style_scores)[::-1][1:top_k+1]
        
        similar_situations = []
        similar_styles = []
        
        # 处理相似情景
        for idx in situation_indices:
            if situation_scores[idx] > 0:  # 只保留有相似度的
                similar_situations.append((
                    expressions[idx]['situation'],
                    expressions[idx]['style'],  # 添加对应的原始表达
                    situation_scores[idx]
                ))
                
        # 处理相似表达
        for idx in style_indices:
            if style_scores[idx] > 0:  # 只保留有相似度的
                similar_styles.append((
                    expressions[idx]['style'],
                    expressions[idx]['situation'],  # 添加对应的原始情景
                    style_scores[idx]
                ))
        
        if similar_situations or similar_styles:
            similar_expressions[i] = {
                'situations': similar_situations,
                'styles': similar_styles
            }
            
    return similar_expressions

def main():
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
    print(f"\n分析群聊 {group_name} 的表达方式:")
    
    similar_styles = find_similar_expressions(style_exprs)
    for i, expr in enumerate(style_exprs):
        if i in similar_styles:
            print("\n" + "-" * 20)
            print(f"表达方式：{expr['style']} <---> 情景：{expr['situation']}")
            
            if similar_styles[i]['styles']:
                print("\n\033[33m相似表达：\033[0m")
                for similar_style, original_situation, score in similar_styles[i]['styles']:
                    print(f"\033[33m{similar_style},score:{score:.3f},对应情景：{original_situation}\033[0m")
                    
            if similar_styles[i]['situations']:
                print("\n\033[32m相似情景：\033[0m")
                for similar_situation, original_style, score in similar_styles[i]['situations']:
                    print(f"\033[32m{similar_situation},score:{score:.3f},对应表达：{original_style}\033[0m")
                    
            print(f"\n激活值：{expr.get('count', 1):.3f}，上次激活时间：{format_timestamp(expr.get('last_active_time'))}")
            print("-" * 20)

if __name__ == "__main__":
    main() 