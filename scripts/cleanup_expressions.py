import os
import json
import random
from typing import List, Dict, Tuple
import glob
from datetime import datetime

MAX_EXPRESSION_COUNT = 300  # 每个群最多保留的表达方式数量
MIN_COUNT_THRESHOLD = 0.01  # 最小使用次数阈值

def load_expressions(chat_id: str) -> Tuple[List[Dict], List[Dict]]:
    """加载指定群聊的表达方式"""
    style_file = os.path.join("data", "expression", "learnt_style", str(chat_id), "expressions.json")
    grammar_file = os.path.join("data", "expression", "learnt_grammar", str(chat_id), "expressions.json")
    
    style_exprs = []
    grammar_exprs = []
    
    if os.path.exists(style_file):
        with open(style_file, "r", encoding="utf-8") as f:
            style_exprs = json.load(f)
            
    if os.path.exists(grammar_file):
        with open(grammar_file, "r", encoding="utf-8") as f:
            grammar_exprs = json.load(f)
            
    return style_exprs, grammar_exprs

def save_expressions(chat_id: str, style_exprs: List[Dict], grammar_exprs: List[Dict]) -> None:
    """保存表达方式到文件"""
    style_file = os.path.join("data", "expression", "learnt_style", str(chat_id), "expressions.json")
    grammar_file = os.path.join("data", "expression", "learnt_grammar", str(chat_id), "expressions.json")
    
    os.makedirs(os.path.dirname(style_file), exist_ok=True)
    os.makedirs(os.path.dirname(grammar_file), exist_ok=True)
    
    with open(style_file, "w", encoding="utf-8") as f:
        json.dump(style_exprs, f, ensure_ascii=False, indent=2)
        
    with open(grammar_file, "w", encoding="utf-8") as f:
        json.dump(grammar_exprs, f, ensure_ascii=False, indent=2)

def cleanup_expressions(expressions: List[Dict]) -> List[Dict]:
    """清理表达方式列表"""
    if not expressions:
        return []
        
    # 1. 移除使用次数过低的表达方式
    expressions = [expr for expr in expressions if expr.get("count", 0) > MIN_COUNT_THRESHOLD]
    
    # 2. 如果数量超过限制，随机删除多余的
    if len(expressions) > MAX_EXPRESSION_COUNT:
        # 按使用次数排序
        expressions.sort(key=lambda x: x.get("count", 0), reverse=True)
        
        # 保留前50%的高频表达方式
        keep_count = MAX_EXPRESSION_COUNT // 2
        keep_exprs = expressions[:keep_count]
        
        # 从剩余的表达方式中随机选择
        remaining_exprs = expressions[keep_count:]
        random.shuffle(remaining_exprs)
        keep_exprs.extend(remaining_exprs[:MAX_EXPRESSION_COUNT - keep_count])
        
        expressions = keep_exprs
    
    return expressions

def main():
    # 获取所有群聊ID
    style_dirs = glob.glob(os.path.join("data", "expression", "learnt_style", "*"))
    chat_ids = [os.path.basename(d) for d in style_dirs]
    
    if not chat_ids:
        print("没有找到任何群聊的表达方式数据")
        return
        
    print(f"开始清理 {len(chat_ids)} 个群聊的表达方式数据...")
    
    total_style_before = 0
    total_style_after = 0
    total_grammar_before = 0
    total_grammar_after = 0
    
    for chat_id in chat_ids:
        print(f"\n处理群聊 {chat_id}:")
        
        # 加载表达方式
        style_exprs, grammar_exprs = load_expressions(chat_id)
        
        # 记录清理前的数量
        style_count_before = len(style_exprs)
        grammar_count_before = len(grammar_exprs)
        total_style_before += style_count_before
        total_grammar_before += grammar_count_before
        
        # 清理表达方式
        style_exprs = cleanup_expressions(style_exprs)
        grammar_exprs = cleanup_expressions(grammar_exprs)
        
        # 记录清理后的数量
        style_count_after = len(style_exprs)
        grammar_count_after = len(grammar_exprs)
        total_style_after += style_count_after
        total_grammar_after += grammar_count_after
        
        # 保存清理后的表达方式
        save_expressions(chat_id, style_exprs, grammar_exprs)
        
        print(f"语言风格: {style_count_before} -> {style_count_after}")
        print(f"句法特点: {grammar_count_before} -> {grammar_count_after}")
    
    print("\n清理完成！")
    print(f"语言风格总数: {total_style_before} -> {total_style_after}")
    print(f"句法特点总数: {total_grammar_before} -> {total_grammar_after}")
    print(f"总共清理了 {total_style_before + total_grammar_before - total_style_after - total_grammar_after} 条表达方式")

if __name__ == "__main__":
    main() 