#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试修复后的memory_points处理
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from person_info.person_info import Person

def test_memory_points_with_none():
    """测试包含None值的memory_points处理"""
    print("测试包含None值的memory_points处理...")
    
    # 创建一个测试Person实例
    person = Person(person_id="test_user_123")
    
    # 模拟包含None值的memory_points
    person.memory_points = [
        "喜好:喜欢咖啡:1.0",
        None,  # 模拟None值
        "性格:开朗:1.0",
        None,  # 模拟另一个None值
        "兴趣:编程:1.0"
    ]
    
    print(f"原始memory_points: {person.memory_points}")
    
    # 测试get_all_category方法
    try:
        categories = person.get_all_category()
        print(f"获取到的分类: {categories}")
        print("✓ get_all_category方法正常工作")
    except Exception as e:
        print(f"✗ get_all_category方法出错: {e}")
        return False
    
    # 测试get_memory_list_by_category方法
    try:
        memories = person.get_memory_list_by_category("喜好")
        print(f"获取到的喜好记忆: {memories}")
        print("✓ get_memory_list_by_category方法正常工作")
    except Exception as e:
        print(f"✗ get_memory_list_by_category方法出错: {e}")
        return False
    
    # 测试del_memory方法
    try:
        deleted_count = person.del_memory("喜好", "喜欢咖啡")
        print(f"删除的记忆点数量: {deleted_count}")
        print(f"删除后的memory_points: {person.memory_points}")
        print("✓ del_memory方法正常工作")
    except Exception as e:
        print(f"✗ del_memory方法出错: {e}")
        return False
    
    return True

def test_memory_points_empty():
    """测试空的memory_points处理"""
    print("\n测试空的memory_points处理...")
    
    person = Person(person_id="test_user_456")
    person.memory_points = []
    
    try:
        categories = person.get_all_category()
        print(f"空列表的分类: {categories}")
        print("✓ 空列表处理正常")
    except Exception as e:
        print(f"✗ 空列表处理出错: {e}")
        return False
    
    try:
        memories = person.get_memory_list_by_category("测试分类")
        print(f"空列表的记忆: {memories}")
        print("✓ 空列表分类查询正常")
    except Exception as e:
        print(f"✗ 空列表分类查询出错: {e}")
        return False
    
    return True

def test_memory_points_all_none():
    """测试全部为None的memory_points处理"""
    print("\n测试全部为None的memory_points处理...")
    
    person = Person(person_id="test_user_789")
    person.memory_points = [None, None, None]
    
    try:
        categories = person.get_all_category()
        print(f"全None列表的分类: {categories}")
        print("✓ 全None列表处理正常")
    except Exception as e:
        print(f"✗ 全None列表处理出错: {e}")
        return False
    
    try:
        memories = person.get_memory_list_by_category("测试分类")
        print(f"全None列表的记忆: {memories}")
        print("✓ 全None列表分类查询正常")
    except Exception as e:
        print(f"✗ 全None列表分类查询出错: {e}")
        return False
    
    return True

if __name__ == "__main__":
    print("开始测试修复后的memory_points处理...")
    
    success = True
    success &= test_memory_points_with_none()
    success &= test_memory_points_empty()
    success &= test_memory_points_all_none()
    
    if success:
        print("\n🎉 所有测试通过！memory_points的None值处理已修复。")
    else:
        print("\n❌ 部分测试失败，需要进一步检查。")
