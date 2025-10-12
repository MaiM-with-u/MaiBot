#!/usr/bin/env python3
"""
查看 .pkl 文件内容的工具脚本
"""

import pickle
import sys
import os
from pprint import pprint

def view_pkl_file(file_path):
    """查看 pkl 文件内容"""
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        return
    
    try:
        with open(file_path, 'rb') as f:
            data = pickle.load(f)
        
        print(f"📁 文件: {file_path}")
        print(f"📊 数据类型: {type(data)}")
        print("=" * 50)
        
        if isinstance(data, dict):
            print("🔑 字典键:")
            for key in data.keys():
                print(f"  - {key}: {type(data[key])}")
            print()
            
            print("📋 详细内容:")
            pprint(data, width=120, depth=10)
            
        elif isinstance(data, list):
            print(f"📝 列表长度: {len(data)}")
            if data:
                print(f"📊 第一个元素类型: {type(data[0])}")
                print("📋 前几个元素:")
                for i, item in enumerate(data[:3]):
                    print(f"  [{i}]: {item}")
        
        else:
            print("📋 内容:")
            pprint(data, width=120, depth=10)
        
        # 如果是 expressor 模型，特别显示 token_counts 的详细信息
        if isinstance(data, dict) and 'nb' in data and 'token_counts' in data['nb']:
            print("\n" + "="*50)
            print("🔍 详细词汇统计 (token_counts):")
            token_counts = data['nb']['token_counts']
            for style_id, tokens in token_counts.items():
                print(f"\n📝 {style_id}:")
                if tokens:
                    # 按词频排序显示前10个词
                    sorted_tokens = sorted(tokens.items(), key=lambda x: x[1], reverse=True)
                    for word, count in sorted_tokens[:10]:
                        print(f"  '{word}': {count}")
                    if len(sorted_tokens) > 10:
                        print(f"  ... 还有 {len(sorted_tokens) - 10} 个词")
                else:
                    print("  (无词汇数据)")
            
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")

def main():
    if len(sys.argv) != 2:
        print("用法: python view_pkl.py <pkl文件路径>")
        print("示例: python view_pkl.py data/test_style_models/chat_001_style_model.pkl")
        return
    
    file_path = sys.argv[1]
    view_pkl_file(file_path)

if __name__ == "__main__":
    main()
