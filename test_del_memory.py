#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试del_memory函数的脚本
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from person_info.person_info import Person

def test_del_memory():
    """测试del_memory函数"""
    print("开始测试del_memory函数...")
    
    # 创建一个测试用的Person实例（不连接数据库）
    person = Person.__new__(Person)
    person.person_id = "test_person"
    person.memory_points = [
        "性格:这个人很友善:5.0",
        "性格:这个人很友善:4.0", 
        "爱好:喜欢打游戏:3.0",
        "爱好:喜欢打游戏:2.0",
        "工作:是一名程序员:1.0",
        "性格:这个人很友善:6.0"
    ]
    
    print(f"原始记忆点数量: {len(person.memory_points)}")
    print("原始记忆点:")
    for i, memory in enumerate(person.memory_points):
        print(f"  {i+1}. {memory}")
    
    # 测试删除"性格"分类中"这个人很友善"的记忆
    print("\n测试1: 删除'性格'分类中'这个人很友善'的记忆")
    deleted_count = person.del_memory("性格", "这个人很友善")
    print(f"删除了 {deleted_count} 个记忆点")
    print("删除后的记忆点:")
    for i, memory in enumerate(person.memory_points):
        print(f"  {i+1}. {memory}")
    
    # 测试删除"爱好"分类中"喜欢打游戏"的记忆
    print("\n测试2: 删除'爱好'分类中'喜欢打游戏'的记忆")
    deleted_count = person.del_memory("爱好", "喜欢打游戏")
    print(f"删除了 {deleted_count} 个记忆点")
    print("删除后的记忆点:")
    for i, memory in enumerate(person.memory_points):
        print(f"  {i+1}. {memory}")
    
    # 测试相似度匹配
    print("\n测试3: 测试相似度匹配")
    person.memory_points = [
        "性格:这个人非常友善:5.0",
        "性格:这个人很友善:4.0",
        "性格:这个人友善:3.0"
    ]
    print("原始记忆点:")
    for i, memory in enumerate(person.memory_points):
        print(f"  {i+1}. {memory}")
    
    # 删除"这个人很友善"（应该匹配"这个人很友善"和"这个人友善"）
    deleted_count = person.del_memory("性格", "这个人很友善", similarity_threshold=0.8)
    print(f"删除了 {deleted_count} 个记忆点")
    print("删除后的记忆点:")
    for i, memory in enumerate(person.memory_points):
        print(f"  {i+1}. {memory}")
    
    print("\n测试完成!")

if __name__ == "__main__":
    test_del_memory()
