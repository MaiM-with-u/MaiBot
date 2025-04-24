# -*- coding: utf-8 -*-
import os
import sys
import time
from pathlib import Path
import datetime
from rich.console import Console
from Hippocampus import Hippocampus  # 海马体和记忆图



"""
我想 总有那么一个瞬间
你会想和某天才变态少女助手一样
往Bot的海马体里插上几个电极 不是吗

Let's do some dirty job.
"""

# 获取当前文件的目录
current_dir = Path(__file__).resolve().parent
# 获取项目根目录（上三层目录）
project_root = current_dir.parent.parent.parent
# env.dev文件路径
env_path = project_root / ".env.dev"

# from chat.config import global_config
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.append(root_path)

from src.common.logger import get_module_logger  # noqa E402
from src.common.database import db  # noqa E402

logger = get_module_logger("mem_alter")
console = Console()

# 查询节点信息
def query_mem_info(hippocampus: Hippocampus):
    while True:
        query = input("\n请输入新的查询概念（输入'退出'以结束）：")
        if query.lower() == "退出":
            break

        items_list = hippocampus.memory_graph.get_related_item(query)
        if items_list:
            have_memory = False
            first_layer, second_layer = items_list
            if first_layer:
                have_memory = True
                print("\n直接相关的记忆：")
                for item in first_layer:
                    print(f"- {item}")
            if second_layer:
                have_memory = True
                print("\n间接相关的记忆：")
                for item in second_layer:
                    print(f"- {item}")
            if not have_memory:
                print("\n未找到相关记忆。")
        else:
            print("未找到相关记忆。")


# 增加概念节点
def add_mem_node(hippocampus: Hippocampus):
    while True:
        concept = input("请输入节点概念名:\n")
        result = db.graph_data.nodes.count_documents({"concept": concept})

        if result != 0:
            console.print("[yellow]已存在名为“{concept}”的节点，行为已取消[/yellow]")
            continue

        memory_items = list()
        while True:
            context = input("请输入节点描述信息（输入'终止'以结束）")
            if context.lower() == "终止":
                break
            memory_items.append(context)

        current_time = datetime.datetime.now().timestamp()
        hippocampus.memory_graph.G.add_node(
            concept, memory_items=memory_items, created_time=current_time, last_modified=current_time
        )


# 删除概念节点（及连接到它的边）
def remove_mem_node(hippocampus: Hippocampus):
    concept = input("请输入节点概念名:\n")
    result = db.graph_data.nodes.count_documents({"concept": concept})

    if result == 0:
        console.print(f"[red]不存在名为“{concept}”的节点[/red]")

    edges = db.graph_data.edges.find({"$or": [{"source": concept}, {"target": concept}]})

    for edge in edges:
        console.print(f"[yellow]存在边“{edge['source']} -> {edge['target']}”, 请慎重考虑[/yellow]")

    console.print(f"[yellow]确定要移除名为“{concept}”的节点以及其相关边吗[/yellow]")
    destory = console.input(f"[red]请输入“{concept}”以删除节点 其他输入将被视为取消操作[/red]\n")
    if destory == concept:
        hippocampus.memory_graph.G.remove_node(concept)
    else:
        logger.info("[green]删除操作已取消[/green]")


# 增加节点间边
def add_mem_edge(hippocampus: Hippocampus):
    while True:
        source = input("请输入 **第一个节点** 名称（输入'退出'以结束）：\n")
        if source.lower() == "退出":
            break
        if db.graph_data.nodes.count_documents({"concept": source}) == 0:
            console.print(f"[yellow]“{source}”节点不存在，操作已取消。[/yellow]")
            continue

        target = input("请输入 **第二个节点** 名称：\n")
        if db.graph_data.nodes.count_documents({"concept": target}) == 0:
            console.print(f"[yellow]“{target}”节点不存在，操作已取消。[/yellow]")
            continue

        if source == target:
            console.print(f"[yellow]试图创建“{source} <-> {target}”自环，操作已取消。[/yellow]")
            continue

        hippocampus.memory_graph.connect_dot(source, target)
        edge = hippocampus.memory_graph.G.get_edge_data(source, target)
        if edge["strength"] == 1:
            console.print(f"[green]成功创建边“{source} <-> {target}”，默认权重1[/green]")
        else:
            console.print(
                f"[yellow]边“{source} <-> {target}”已存在，"
                f"更新权重: {edge['strength'] - 1} <-> {edge['strength']}[/yellow]"
            )


# 删除节点间边
def remove_mem_edge(hippocampus: Hippocampus):
    while True:
        source = input("请输入 **第一个节点** 名称（输入'退出'以结束）：\n")
        if source.lower() == "退出":
            break
        if db.graph_data.nodes.count_documents({"concept": source}) == 0:
            console.print("[yellow]“{source}”节点不存在，操作已取消。[/yellow]")
            continue

        target = input("请输入 **第二个节点** 名称：\n")
        if db.graph_data.nodes.count_documents({"concept": target}) == 0:
            console.print("[yellow]“{target}”节点不存在，操作已取消。[/yellow]")
            continue

        if source == target:
            console.print("[yellow]试图创建“{source} <-> {target}”自环，操作已取消。[/yellow]")
            continue

        edge = hippocampus.memory_graph.G.get_edge_data(source, target)
        if edge is None:
            console.print("[yellow]边“{source} <-> {target}”不存在，操作已取消。[/yellow]")
            continue
        else:
            accept = console.input("[orange]请输入“确认”以确认删除操作（其他输入视为取消）[/orange]\n")
            if accept.lower() == "确认":
                hippocampus.memory_graph.G.remove_edge(source, target)
                console.print(f"[green]边“{source} <-> {target}”已删除。[green]")


# 修改节点信息
def alter_mem_node(hippocampus: Hippocampus):
    batch_environment = dict()
    while True:
        concept = input("请输入节点概念名（输入'终止'以结束）:\n")
        if concept.lower() == "终止":
            break
        _, node = hippocampus.memory_graph.get_dot(concept)
        if node is None:
            console.print(f"[yellow]“{concept}”节点不存在，操作已取消。[/yellow]")
            continue

        console.print("[yellow]注意，请确保你知道自己在做什么[/yellow]")
        console.print("[yellow]你将获得一个执行任意代码的环境[/yellow]")
        console.print("[red]你已经被警告过了。[/red]\n")

        node_environment = {"concept": "<节点名>", "memory_items": "<记忆文本数组>"}
        console.print(
            "[green]环境变量中会有env与batchEnv两个dict, env在切换节点时会清空, batchEnv在操作终止时才会清空[/green]"
        )
        console.print(
            f"[green] env 会被初始化为[/green]\n{node_environment}\n[green]且会在用户代码执行完毕后被提交 [/green]"
        )
        console.print(
            "[yellow]为便于书写临时脚本，请手动在输入代码通过Ctrl+C等方式触发KeyboardInterrupt来结束代码执行[/yellow]"
        )

        # 拷贝数据以防操作炸了
        node_environment = dict(node)
        node_environment["concept"] = concept

        while True:

            def user_exec(script, env, batch_env):
                return eval(script, env, batch_env)

            try:
                command = console.input()
            except KeyboardInterrupt:
                # 稍微防一下小天才
                try:
                    if isinstance(node_environment["memory_items"], list):
                        node["memory_items"] = node_environment["memory_items"]
                    else:
                        raise Exception

                except Exception as e:
                    console.print(
                        f"[red]我不知道你做了什么，但显然nodeEnviroment['memory_items']已经不是个数组了，"
                        f"操作已取消: {str(e)}[/red]"
                    )
                break

            try:
                user_exec(command, node_environment, batch_environment)
            except Exception as e:
                console.print(e)
                console.print(
                    "[red]自定义代码执行时发生异常，已捕获，请重试（可通过 console.print(locals()) 检查环境状态）[/red]"
                )


# 修改边信息
def alter_mem_edge(hippocampus: Hippocampus):
    batch_enviroment = dict()
    while True:
        source = input("请输入 **第一个节点** 名称（输入'终止'以结束）：\n")
        if source.lower() == "终止":
            break
        if hippocampus.memory_graph.get_dot(source) is None:
            console.print(f"[yellow]“{source}”节点不存在，操作已取消。[/yellow]")
            continue

        target = input("请输入 **第二个节点** 名称：\n")
        if hippocampus.memory_graph.get_dot(target) is None:
            console.print(f"[yellow]“{target}”节点不存在，操作已取消。[/yellow]")
            continue

        edge = hippocampus.memory_graph.G.get_edge_data(source, target)
        if edge is None:
            console.print(f"[yellow]边“{source} <-> {target}”不存在，操作已取消。[/yellow]")
            continue

        console.print("[yellow]注意，请确保你知道自己在做什么[/yellow]")
        console.print("[yellow]你将获得一个执行任意代码的环境[/yellow]")
        console.print("[red]你已经被警告过了。[/red]\n")

        edge_environment = {"source": "<节点名>", "target": "<节点名>", "strength": "<强度值,装在一个list里>"}
        console.print(
            "[green]环境变量中会有env与batchEnv两个dict, env在切换节点时会清空, batchEnv在操作终止时才会清空[/green]"
        )
        console.print(
            f"[green] env 会被初始化为[/green]\n{edge_environment}\n[green]且会在用户代码执行完毕后被提交 [/green]"
        )
        console.print(
            "[yellow]为便于书写临时脚本，请手动在输入代码通过Ctrl+C等方式触发KeyboardInterrupt来结束代码执行[/yellow]"
        )

        # 拷贝数据以防操作炸了
        edge_environment["strength"] = [edge["strength"]]
        edge_environment["source"] = source
        edge_environment["target"] = target

        while True:

            def user_exec(script, env, batch_env):
                return eval(script, env, batch_env)

            try:
                command = console.input()
            except KeyboardInterrupt:
                # 稍微防一下小天才
                try:
                    if isinstance(edge_environment["strength"][0], int):
                        edge["strength"] = edge_environment["strength"][0]
                    else:
                        raise Exception

                except Exception as e:
                    console.print(
                        f"[red]我不知道你做了什么，但显然edgeEnviroment['strength']已经不是个int了，"
                        f"操作已取消: {str(e)}[/red]"
                    )
                break

            try:
                user_exec(command, edge_environment, batch_enviroment)
            except Exception as e:
                console.print(e)
                console.print(
                    "[red]自定义代码执行时发生异常，已捕获，请重试（可通过 console.print(locals()) 检查环境状态）[/red]"
                )


async def main():
    start_time = time.time()

    # 创建海马体
    hippocampus = Hippocampus()

    # 从数据库同步数据
    hippocampus.entorhinal_cortex.sync_memory_from_db()

    end_time = time.time()
    logger.info(f"\033[32m[加载海马体耗时: {end_time - start_time:.2f} 秒]\033[0m")

    while True:
        try:
            query = int(
                input(
                    """请输入操作类型
0 -> 查询节点; 1 -> 增加节点; 2 -> 移除节点; 3 -> 增加边; 4 -> 移除边;
5 -> 修改节点; 6 -> 修改边; 其他任意输入 -> 退出
"""
                )
            )
        except ValueError:
            query = -1

        if query == 0:
            query_mem_info(hippocampus.memory_graph)
        elif query == 1:
            add_mem_node(hippocampus)
        elif query == 2:
            remove_mem_node(hippocampus)
        elif query == 3:
            add_mem_edge(hippocampus)
        elif query == 4:
            remove_mem_edge(hippocampus)
        elif query == 5:
            alter_mem_node(hippocampus)
        elif query == 6:
            alter_mem_edge(hippocampus)
        else:
            print("已结束操作")
            break

        hippocampus.entorhinal_cortex.sync_memory_to_db()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
