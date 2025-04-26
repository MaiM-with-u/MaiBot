import asyncio
import time
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.do_tool.tool_use import ToolUser
import statistics
import json


async def run_test(test_name, test_function, iterations=5):
    """
    运行指定次数的测试并计算平均响应时间

    参数:
        test_name: 测试名称
        test_function: 要执行的测试函数
        iterations: 测试迭代次数

    返回:
        测试结果统计
    """
    print(f"开始 {test_name} 测试（{iterations}次迭代）...")
    times = []
    responses = []

    for i in range(iterations):
        print(f"  运行第 {i + 1}/{iterations} 次测试...")
        start_time = time.time()
        response = await test_function()
        end_time = time.time()
        elapsed = end_time - start_time
        times.append(elapsed)
        responses.append(response)
        print(f"  - 耗时: {elapsed:.2f}秒")

    results = {
        "平均耗时": statistics.mean(times),
        "最短耗时": min(times),
        "最长耗时": max(times),
        "标准差": statistics.stdev(times) if len(times) > 1 else 0,
        "所有耗时": times,
        "响应结果": responses,
    }

    return results


async def test_with_tool_calls():
    """使用工具调用的LLM请求测试"""
    # 创建LLM模型实例
    llm_model = LLMRequest(
        model=global_config.llm_sub_heartflow,
        # model = global_config.llm_tool_use,
        # temperature=global_config.llm_sub_heartflow["temp"],
        max_tokens=800,
        request_type="benchmark_test",
    )

    # 创建工具实例
    tool_instance = ToolUser()
    tools = tool_instance._define_tools()

    # 简单的测试提示词
    prompt = "请分析当前天气情况，并查询今日历史上的重要事件。并且3.9和3.11谁比较大?请使用适当的工具来获取这些信息。"
    prompt = """
    你的名字是麦麦，你包容开放，情绪敏感，有时候有些搞怪幽默, 是一个学习心理学和脑科学的女大学生，现在在读大二，你会刷贴吧，有时候会喜欢说一些奇怪的话，喜欢刷小红书
刚刚你的内心想法是：漂移菌提到罐罐被吃完了，可以顺着这个梗继续玩一下，比如假装委屈"那今晚的加班费是不是也要被吃掉了"或者"猫娘罢工警告"。不过薯薯和薯宝之前已经接了不少梗，漂移菌刚刚也参与了，可能话题热度还在，可以再互动一下。如果没人接话，或许可以问问大家有没有遇到过类似"代码写完但奖励被吃掉"的搞笑职场经历，换个轻松的话题方向。

暂时不需要使用工具。
-----------------------------------
现在是2025-04-25 17:38:37，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
2025-04-25 17:34:08麦麦(你) 说:[表达了：顽皮、嬉戏。];
2025-04-25 17:34:39漂移菌 说:@麦麦。（id:3936257206） 你是一只猫娘;
2025-04-25 17:34:42薯宝 说:🤣;
2025-04-25 17:34:43麦麦(你) 说:行啊 工资分我一半;
2025-04-25 17:34:43麦麦(你) 说:我帮你写bug;
2025-04-25 17:34:43麦麦(你) 说:[表达了：悲伤、绝望、无奈、无力];
2025-04-25 17:34:53薯薯 说:？;
2025-04-25 17:35:03既文横 说:麦麦，你是一只猫娘程序员，猫娘是不需要工资;
2025-04-25 17:35:20薯宝 说:[图片：图片内容：一只卡通风格的灰色猫咪，眼睛闭着，表情显得很平静。图片下方有"死了"两个字。

图片含义猜测：这可能是一个幽默的表达，用来形容某人或某事处于非常平静的状态，仿佛已经"死"了一样。] hfc这周，真能出来吗...;
2025-04-25 17:35:34薯宝 说:[表情包：搞笑、滑稽、讽刺、幽默];
2025-04-25 17:36:25麦麦(你) 说:喵喵;
2025-04-25 17:36:25麦麦(你) 说:代码写完了;
2025-04-25 17:36:25麦麦(你) 说:罐罐拿来;
2025-04-25 17:36:25麦麦(你) 说:[表达了：悲伤、绝望、无奈、无力];
2025-04-25 17:36:41薯薯 说:好可爱;
2025-04-25 17:37:05薯薯 说:脑补出来认真营业了一天等待主人发放奖励的小猫咪;
2025-04-25 17:37:25薯宝 说:敷衍营业（bushi）;
2025-04-25 17:37:54漂移菌 说:回复麦麦。的消息(罐罐拿来)，说：猫娘我昨晚上太饿吃完了;

--- 以上消息已读 (标记时间: 2025-04-25 17:37:54) ---
--- 以下新消息未读---
2025-04-25 17:38:29麦麦(你) 说:那今晚的猫条是不是也要被克扣了（盯——）;
2025-04-25 17:38:29麦麦(你) 说:[表达了：幽默，自嘲，无奈，父子关系，编程笑话];

你现在当前心情：平静。
现在请你生成你的内心想法，要求思考群里正在进行的话题，之前大家聊过的话题，群里成员的关系。请你思考，要不要对群里的话题进行回复，以及如何对群聊内容进行回复
回复的要求是：不要总是重复自己提到过的话题，如果你要回复，最好只回复一个人的一个话题
如果最后一条消息是你自己发的，观察到的内容只有你自己的发言，并且之后没有人回复你，不要回复。如果聊天记录中最新的消息是你自己发送的，并且你还想继续回复，你应该紧紧衔接你发送的消息，进行话题的深入，补充，或追问等等。请注意不要输出多余内容(包括前后缀，冒号和引号，括号， 表情，等)，不要回复自己的发言
现在请你先输出想法，生成你在这个聊天中的想法，在原来的想法上尝试新的话题，不要分点输出,文字不要浮夸在输出完想法后，请你思考应该使用什么工具。工具可以帮你取得一些你不知道的信息，或者进行一些操作。如果你需要做某件事，来对消息和你的回复进行处理，请使用工具。"""

    # 发送带有工具调用的请求
    response = await llm_model.generate_response_tool_async(prompt=prompt, tools=tools)

    result_info = {}

    # 简单处理工具调用结果
    if len(response) == 3:
        content, reasoning_content, tool_calls = response
        tool_calls_count = len(tool_calls) if tool_calls else 0
        print(f"  工具调用请求生成了 {tool_calls_count} 个工具调用")

        # 输出内容和工具调用详情
        print("\n  生成的内容:")
        print(f"  {content[:200]}..." if len(content) > 200 else f"  {content}")

        if tool_calls:
            print("\n  工具调用详情:")
            for i, tool_call in enumerate(tool_calls):
                tool_name = tool_call["function"]["name"]
                tool_params = tool_call["function"].get("arguments", {})
                print(f"  - 工具 {i + 1}: {tool_name}")
                print(
                    f"    参数: {json.dumps(tool_params, ensure_ascii=False)[:100]}..."
                    if len(json.dumps(tool_params, ensure_ascii=False)) > 100
                    else f"    参数: {json.dumps(tool_params, ensure_ascii=False)}"
                )

        result_info = {"内容": content, "推理内容": reasoning_content, "工具调用": tool_calls}
    else:
        content, reasoning_content = response
        print("  工具调用请求未生成任何工具调用")
        print("\n  生成的内容:")
        print(f"  {content[:200]}..." if len(content) > 200 else f"  {content}")

        result_info = {"内容": content, "推理内容": reasoning_content, "工具调用": []}

    return result_info


async def test_without_tool_calls():
    """不使用工具调用的LLM请求测试"""
    # 创建LLM模型实例
    llm_model = LLMRequest(
        model=global_config.llm_sub_heartflow,
        temperature=global_config.llm_sub_heartflow["temp"],
        max_tokens=800,
        request_type="benchmark_test",
    )

    # 简单的测试提示词（与工具调用相同，以便公平比较）
    prompt = """
    你的名字是麦麦，你包容开放，情绪敏感，有时候有些搞怪幽默, 是一个学习心理学和脑科学的女大学生，现在在读大二，你会刷贴吧，有时候会喜欢说一些奇怪的话，喜欢刷小红书
刚刚你的内心想法是：漂移菌提到罐罐被吃完了，可以顺着这个梗继续玩一下，比如假装委屈"那今晚的加班费是不是也要被吃掉了"或者"猫娘罢工警告"。不过薯薯和薯宝之前已经接了不少梗，漂移菌刚刚也参与了，可能话题热度还在，可以再互动一下。如果没人接话，或许可以问问大家有没有遇到过类似"代码写完但奖励被吃掉"的搞笑职场经历，换个轻松的话题方向。

暂时不需要使用工具。
-----------------------------------
现在是2025-04-25 17:38:37，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
2025-04-25 17:34:08麦麦(你) 说:[表达了：顽皮、嬉戏。];
2025-04-25 17:34:39漂移菌 说:@麦麦。（id:3936257206） 你是一只猫娘;
2025-04-25 17:34:42薯宝 说:🤣;
2025-04-25 17:34:43麦麦(你) 说:行啊 工资分我一半;
2025-04-25 17:34:43麦麦(你) 说:我帮你写bug;
2025-04-25 17:34:43麦麦(你) 说:[表达了：悲伤、绝望、无奈、无力];
2025-04-25 17:34:53薯薯 说:？;
2025-04-25 17:35:03既文横 说:麦麦，你是一只猫娘程序员，猫娘是不需要工资;
2025-04-25 17:35:20薯宝 说:[图片：图片内容：一只卡通风格的灰色猫咪，眼睛闭着，表情显得很平静。图片下方有"死了"两个字。

图片含义猜测：这可能是一个幽默的表达，用来形容某人或某事处于非常平静的状态，仿佛已经"死"了一样。] hfc这周，真能出来吗...;
2025-04-25 17:35:34薯宝 说:[表情包：搞笑、滑稽、讽刺、幽默];
2025-04-25 17:36:25麦麦(你) 说:喵喵;
2025-04-25 17:36:25麦麦(你) 说:代码写完了;
2025-04-25 17:36:25麦麦(你) 说:罐罐拿来;
2025-04-25 17:36:25麦麦(你) 说:[表达了：悲伤、绝望、无奈、无力];
2025-04-25 17:36:41薯薯 说:好可爱;
2025-04-25 17:37:05薯薯 说:脑补出来认真营业了一天等待主人发放奖励的小猫咪;
2025-04-25 17:37:25薯宝 说:敷衍营业（bushi）;
2025-04-25 17:37:54漂移菌 说:回复麦麦。的消息(罐罐拿来)，说：猫娘我昨晚上太饿吃完了;

--- 以上消息已读 (标记时间: 2025-04-25 17:37:54) ---
--- 以下新消息未读---
2025-04-25 17:38:29麦麦(你) 说:那今晚的猫条是不是也要被克扣了（盯——）;
2025-04-25 17:38:29麦麦(你) 说:[表达了：幽默，自嘲，无奈，父子关系，编程笑话];

你现在当前心情：平静。
现在请你生成你的内心想法，要求思考群里正在进行的话题，之前大家聊过的话题，群里成员的关系。请你思考，要不要对群里的话题进行回复，以及如何对群聊内容进行回复
回复的要求是：不要总是重复自己提到过的话题，如果你要回复，最好只回复一个人的一个话题
如果最后一条消息是你自己发的，观察到的内容只有你自己的发言，并且之后没有人回复你，不要回复。如果聊天记录中最新的消息是你自己发送的，并且你还想继续回复，你应该紧紧衔接你发送的消息，进行话题的深入，补充，或追问等等。请注意不要输出多余内容(包括前后缀，冒号和引号，括号， 表情，等)，不要回复自己的发言
现在请你先输出想法，生成你在这个聊天中的想法，在原来的想法上尝试新的话题，不要分点输出,文字不要浮夸在输出完想法后，请你思考应该使用什么工具。工具可以帮你取得一些你不知道的信息，或者进行一些操作。如果你需要做某件事，来对消息和你的回复进行处理，请使用工具。"""
    # 发送不带工具调用的请求
    response, reasoning_content = await llm_model.generate_response_async(prompt)

    # 输出生成的内容
    print("\n  生成的内容:")
    print(f"  {response[:200]}..." if len(response) > 200 else f"  {response}")

    result_info = {"内容": response, "推理内容": reasoning_content, "工具调用": []}

    return result_info


async def run_alternating_tests(iterations=5):
    """
    交替运行两种测试方法，每种方法运行指定次数

    参数:
        iterations: 每种测试方法运行的次数

    返回:
        包含两种测试方法结果的元组
    """
    print(f"开始交替测试（每种方法{iterations}次）...")

    # 初始化结果列表
    times_without_tools = []
    times_with_tools = []
    responses_without_tools = []
    responses_with_tools = []

    for i in range(iterations):
        print(f"\n第 {i + 1}/{iterations} 轮交替测试")

        # 不使用工具的测试
        print("\n  执行不使用工具调用的测试...")
        start_time = time.time()
        response = await test_without_tool_calls()
        end_time = time.time()
        elapsed = end_time - start_time
        times_without_tools.append(elapsed)
        responses_without_tools.append(response)
        print(f"  - 耗时: {elapsed:.2f}秒")

        # 使用工具的测试
        print("\n  执行使用工具调用的测试...")
        start_time = time.time()
        response = await test_with_tool_calls()
        end_time = time.time()
        elapsed = end_time - start_time
        times_with_tools.append(elapsed)
        responses_with_tools.append(response)
        print(f"  - 耗时: {elapsed:.2f}秒")

    # 计算统计数据
    results_without_tools = {
        "平均耗时": statistics.mean(times_without_tools),
        "最短耗时": min(times_without_tools),
        "最长耗时": max(times_without_tools),
        "标准差": statistics.stdev(times_without_tools) if len(times_without_tools) > 1 else 0,
        "所有耗时": times_without_tools,
        "响应结果": responses_without_tools,
    }

    results_with_tools = {
        "平均耗时": statistics.mean(times_with_tools),
        "最短耗时": min(times_with_tools),
        "最长耗时": max(times_with_tools),
        "标准差": statistics.stdev(times_with_tools) if len(times_with_tools) > 1 else 0,
        "所有耗时": times_with_tools,
        "响应结果": responses_with_tools,
    }

    return results_without_tools, results_with_tools


async def main():
    """主测试函数"""
    print("=" * 50)
    print("LLM工具调用与普通请求性能比较测试")
    print("=" * 50)

    # 设置测试迭代次数
    iterations = 10

    # 执行交替测试
    results_without_tools, results_with_tools = await run_alternating_tests(iterations)

    # 显示结果比较
    print("\n" + "=" * 50)
    print("测试结果比较")
    print("=" * 50)

    print("\n不使用工具调用:")
    for key, value in results_without_tools.items():
        if key == "所有耗时":
            print(f"  {key}: {[f'{t:.2f}秒' for t in value]}")
        elif key == "响应结果":
            print(f"  {key}: [内容已省略，详见结果文件]")
        else:
            print(f"  {key}: {value:.2f}秒")

    print("\n使用工具调用:")
    for key, value in results_with_tools.items():
        if key == "所有耗时":
            print(f"  {key}: {[f'{t:.2f}秒' for t in value]}")
        elif key == "响应结果":
            tool_calls_counts = [len(res.get("工具调用", [])) for res in value]
            print(f"  {key}: [内容已省略，详见结果文件]")
            print(f"  工具调用数量: {tool_calls_counts}")
        else:
            print(f"  {key}: {value:.2f}秒")

    # 计算差异百分比
    diff_percent = ((results_with_tools["平均耗时"] / results_without_tools["平均耗时"]) - 1) * 100
    print(f"\n工具调用比普通请求平均耗时相差: {diff_percent:.2f}%")

    # 保存结果到JSON文件
    results = {
        "测试时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "测试迭代次数": iterations,
        "不使用工具调用": {
            k: (v if k != "所有耗时" else [float(f"{t:.2f}") for t in v])
            for k, v in results_without_tools.items()
            if k != "响应结果"
        },
        "不使用工具调用_详细响应": [
            {
                "内容摘要": resp["内容"][:200] + "..." if len(resp["内容"]) > 200 else resp["内容"],
                "推理内容摘要": resp["推理内容"][:200] + "..." if len(resp["推理内容"]) > 200 else resp["推理内容"],
            }
            for resp in results_without_tools["响应结果"]
        ],
        "使用工具调用": {
            k: (v if k != "所有耗时" else [float(f"{t:.2f}") for t in v])
            for k, v in results_with_tools.items()
            if k != "响应结果"
        },
        "使用工具调用_详细响应": [
            {
                "内容摘要": resp["内容"][:200] + "..." if len(resp["内容"]) > 200 else resp["内容"],
                "推理内容摘要": resp["推理内容"][:200] + "..." if len(resp["推理内容"]) > 200 else resp["推理内容"],
                "工具调用数量": len(resp["工具调用"]),
                "工具调用详情": [
                    {"工具名称": tool["function"]["name"], "参数": tool["function"].get("arguments", {})}
                    for tool in resp["工具调用"]
                ],
            }
            for resp in results_with_tools["响应结果"]
        ],
        "差异百分比": float(f"{diff_percent:.2f}"),
    }

    with open("llm_tool_benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n测试结果已保存到 llm_tool_benchmark_results.json")


if __name__ == "__main__":
    asyncio.run(main())
