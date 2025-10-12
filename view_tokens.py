#!/usr/bin/env python3
"""
专门查看 expressor.pkl 文件中 token_counts 的脚本
"""

import pickle
import sys
import os

def view_token_counts(file_path):
    """查看 expressor.pkl 文件中的词汇统计"""
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        return
    
    try:
        with open(file_path, 'rb') as f:
            data = pickle.load(f)
        
        print(f"📁 文件: {file_path}")
        print("=" * 60)
        
        if 'nb' not in data or 'token_counts' not in data['nb']:
            print("❌ 这不是一个 expressor 模型文件")
            return
        
        token_counts = data['nb']['token_counts']
        candidates = data.get('candidates', {})
        
        print(f"🎯 找到 {len(token_counts)} 个风格")
        print("=" * 60)
        
        for style_id, tokens in token_counts.items():
            style_text = candidates.get(style_id, "未知风格")
            print(f"\n📝 {style_id}: {style_text}")
            print(f"📊 词汇数量: {len(tokens)}")
            
            if tokens:
                # 按词频排序
                sorted_tokens = sorted(tokens.items(), key=lambda x: x[1], reverse=True)
                
                print("🔤 词汇统计 (按频率排序):")
                for i, (word, count) in enumerate(sorted_tokens):
                    print(f"  {i+1:2d}. '{word}': {count}")
            else:
                print("  (无词汇数据)")
            
            print("-" * 40)
            
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")

def main():
    if len(sys.argv) != 2:
        print("用法: python view_tokens.py <expressor.pkl文件路径>")
        print("示例: python view_tokens.py data/test_style_models/chat_001_expressor.pkl")
        return
    
    file_path = sys.argv[1]
    view_token_counts(file_path)

if __name__ == "__main__":
    main()
