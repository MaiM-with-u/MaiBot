# -*- coding: utf-8 -*-
import asyncio
import time
import sys
import os
# 添加项目根目录到系统路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.plugins.config.config import global_config

async def test_memory_system():
    """测试记忆系统的主要功能"""
    try:
        # 初始化记忆系统
        print("开始初始化记忆系统...")
        hippocampus_manager = HippocampusManager.get_instance()
        hippocampus_manager.initialize(global_config=global_config)
        print("记忆系统初始化完成")

        # 测试记忆构建
        # print("开始测试记忆构建...")
        # await hippocampus_manager.build_memory()
        # print("记忆构建完成")

        # 测试记忆检索
        test_text = "千石可乐在群里聊天"
        test_text = '''[03-24 10:39:37] 麦麦(ta的id:2814567326): 早说散步结果下雨改成室内运动啊
[03-24 10:39:37] 麦麦(ta的id:2814567326): [回复：变量] 变量就像今天计划总变
[03-24 10:39:44] 状态异常(ta的id:535554838): 要把本地文件改成弹出来的路径吗
[03-24 10:40:35] 状态异常(ta的id:535554838): [图片：这张图片显示的是Windows系统的环境变量设置界面。界面左侧列出了多个环境变量的值，包括Intel Dev Redist、Windows、Windows PowerShell、OpenSSH、NVIDIA Corporation的目录等。右侧有新建、编辑、浏览、删除、上移、下移和编辑文本等操作按钮。图片下方有一个错误提示框，显示"Windows找不到文件'mongodb\\bin\\mongod.exe'。请确定文件名是否正确后，再试一次。"这意味着用户试图运行MongoDB的mongod.exe程序时，系统找不到该文件。这可能是因为MongoDB的安装路径未正确添加到系统环境变量中，或者文件路径有误。
图片的含义可能是用户正在尝试设置MongoDB的环境变量，以便在命令行或其他程序中使用MongoDB。如果用户正确设置了环境变量，那么他们应该能够通过命令行或其他方式启动MongoDB服务。]
[03-24 10:41:08] 一根猫(ta的id:108886006): [回复 麦麦 的消息:  [回复某人消息] 改系统变量或者删库重配 ] [@麦麦] 我中途修改人格，需要重配吗
[03-24 10:41:54] 麦麦(ta的id:2814567326): [回复：[回复 麦麦 的消息:  [回复某人消息] 改系统变量或者删库重配 ] [@麦麦] 我中途修改人格，需要重配吗] 看情况
[03-24 10:41:54] 麦麦(ta的id:2814567326): 难
[03-24 10:41:54] 麦麦(ta的id:2814567326): 小改变量就行，大动骨安排重配像游戏副本南度改太大会崩
[03-24 10:45:33] 霖泷(ta的id:1967075066): 话说现在思考高达一分钟
[03-24 10:45:38] 霖泷(ta的id:1967075066): 是不是哪里出问题了
[03-24 10:45:39] 艾卡(ta的id:1786525298): [表情包：这张表情包展示了一个动漫角色，她有着紫色的头发和大大的眼睛，表情显得有些困惑或不解。她的头上有一个问号，进一步强调了她的疑惑。整体情感表达的是困惑或不解。]
[03-24 10:46:12] (ta的id:3229291803): [表情包：这张表情包显示了一只手正在做"点赞"的动作，通常表示赞同、喜欢或支持。这个表情包所表达的情感是积极的、赞同的或支持的。]
[03-24 10:46:37] 星野風禾(ta的id:2890165435): 还能思考高达
[03-24 10:46:39] 星野風禾(ta的id:2890165435): 什么知识库
[03-24 10:46:49] ❦幻凌慌てない(ta的id:2459587037): 为什么改了回复系数麦麦还是不怎么回复？大佬们'''  # noqa: E501


        # test_text = '''千石可乐：分不清AI的陪伴和人类的陪伴,是这样吗？'''
        print(f"开始测试记忆检索，测试文本: {test_text}\n")
        memories = await hippocampus_manager.get_memory_from_text(
            text=test_text,
            max_memory_num=3,
            max_memory_length=2,
            max_depth=3,
            fast_retrieval=False
        )
        
        await asyncio.sleep(1)
        
        print("检索到的记忆:")
        for topic, memory_items in memories:
            print(f"主题: {topic}")
            print(f"- {memory_items}")



        # 测试记忆遗忘
        # forget_start_time = time.time()
        # # print("开始测试记忆遗忘...")
        # await hippocampus_manager.forget_memory(percentage=0.005)
        # # print("记忆遗忘完成")
        # forget_end_time = time.time()
        # print(f"记忆遗忘耗时: {forget_end_time - forget_start_time:.2f} 秒")

        # 获取所有节点
        # nodes = hippocampus_manager.get_all_node_names()
        # print(f"当前记忆系统中的节点数量: {len(nodes)}")
        # print("节点列表:")
        # for node in nodes:
        #     print(f"- {node}")

    except Exception as e:
        print(f"测试过程中出现错误: {e}")
        raise

async def main():
    """主函数"""
    try:
        start_time = time.time()
        await test_memory_system()
        end_time = time.time()
        print(f"测试完成，总耗时: {end_time - start_time:.2f} 秒")
    except Exception as e:
        print(f"程序执行出错: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main()) 