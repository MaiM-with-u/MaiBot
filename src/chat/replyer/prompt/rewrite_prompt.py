from src.chat.utils.prompt_builder import Prompt
# from src.memory_system.memory_activator import MemoryActivator


def init_rewrite_prompt():
    Prompt("你正在qq群里聊天，下面是群里正在聊的内容:", "chat_target_group1")
    Prompt("你正在和{sender_name}聊天，这是你们之前聊的内容：", "chat_target_private1")
    Prompt("正在群里聊天", "chat_target_group2")
    Prompt("和{sender_name}聊天", "chat_target_private2")

    Prompt(
        """
{expression_habits_block}
{chat_target}
{chat_info}
{identity}

你正在{chat_target_2},{reply_target_block}
现在请你对这句内容进行改写，请你参考上述内容进行改写，原句是：{raw_reply}：
原因是：{reason}
现在请你将这条具体内容改写成一条适合在群聊中发送的回复消息。
你需要使用合适的语法和句法，参考聊天内容，组织一条日常且口语化的回复。请你修改你想表达的原句，符合你的表达风格和语言习惯
{reply_style}
你可以完全重组回复，保留最基本的表达含义就好，但重组后保持语意通顺。
{keywords_reaction_prompt}
{moderation_prompt}
不要输出多余内容(包括冒号和引号，表情包，emoji,at或 @等 )，只输出一条回复就好。
改写后的回复：
""",
        "default_expressor_prompt",
    )
