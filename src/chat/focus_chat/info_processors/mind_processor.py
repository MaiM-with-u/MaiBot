你的名字是{bot_name}
{memory_str}

{relation_prompt}
{cycle_info_block}
现在是{time_now}，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
{chat_observe_info}
以下是你执行工具调用后的信息，结果较为准确：
{extra_info}
以下是你之前对聊天的观察和规划，你的名字是{bot_name}：
{last_mind}

现在请你继续输出观察和规划，输出要求：
1. 先关注未读新消息的内容和近期回复历史
2. 根据新信息，修改和删除之前的观察和规划
3. 根据聊天内容继续输出观察和规划
4. 注意群聊的时间线索，话题由谁发起，进展状况如何，思考聊天的时间线。
6. 语言简洁自然，不要分点，不要浮夸，不要修辞，仅输出思考内容就好"""
    Prompt(group_prompt, "sub_heartflow_prompt_before")

    private_prompt = """
你的名字是{bot_name}
{memory_str}
=
{relation_prompt}
{cycle_info_block}
现在是{time_now}，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
{chat_observe_info}
以下是你执行工具调用后的信息，结果较为准确：
{extra_info}
以下是你之前对聊天的观察和规划，你的名字是{bot_name}：
{last_mind}

现在请你继续输出观察和规划，输出要求：
1. 先关注未读新消息的内容和近期回复历史
2. 根据新信息，修改和删除之前的观察和规划
3. 根据聊天内容继续输出观察和规划
4. 注意群聊的时间线索，话题由谁发起，进展状况如何，思考聊天的时间线。
6. 语言简洁自然，不要分点，不要浮夸，不要修辞，仅输出思考内容就好"""
    Prompt(private_prompt, "sub_heartflow_prompt_private_before")
