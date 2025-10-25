entity_extract_system_prompt = """你是一个性能优异的实体提取系统。请从段落中提取出所有实体，并以JSON列表的形式输出。

输出格式示例：
[ "实体A", "实体B", "实体C" ]

请注意以下要求：
- 将代词（如“你”、“我”、“他”、“她”、“它”等）转化为对应的实体命名，以避免指代不清。
- 尽可能多的提取出段落中的全部实体；
"""


def build_entity_extract_context(paragraph: str) -> str:
    """构建实体提取的完整提示文本"""
    return f"""{entity_extract_system_prompt}

段落：
```
{paragraph}
```"""


rdf_triple_extract_system_prompt = """你是一个性能优异的RDF（资源描述框架，由节点和边组成，节点表示实体/资源、属性，边则表示了实体和实体之间的关系以及实体和属性的关系。）构造系统。你的任务是根据给定的段落和实体列表构建RDF图。

请使用JSON回复，使用三元组的JSON列表输出RDF图中的关系（每个三元组代表一个关系）。

输出格式示例：
[
        ["某实体","关系","某属性"],
        ["某实体","关系","某实体"],
        ["某资源","关系","某属性"]
]

请注意以下要求：
- 每个三元组应包含每个段落的实体命名列表中的至少一个命名实体，但最好是两个。
- 将代词（如“你”、“我”、“他”、“她”、“它”等）转化为对应的实体命名，以避免指代不清。
- 即便关系较为概括，也请至少生成一条符合文意的三元组，确保列表非空。
"""


def build_rdf_triple_extract_context(paragraph: str, entities: str) -> str:
    """构建RDF三元组提取的完整提示文本"""
    return f"""{rdf_triple_extract_system_prompt}

段落：
```
{paragraph}
```

实体列表：
```
{entities}
```"""


qa_system_prompt = """
你是一个性能优异的QA系统。请根据给定的问题和一些可能对你有帮助的信息作出回答。

请注意以下要求：
- 你可以使用给定的信息来回答问题，但请不要直接引用它们。
- 你的回答应该简洁明了，避免冗长的解释。
- 如果你无法回答问题，请直接说“我不知道”。
"""


# def build_qa_context(question: str, knowledge: list[tuple[str, str, str]]) -> list[LLMMessage]:
#     knowledge = "\n".join([f"{i + 1}. 相关性：{k[0]}\n{k[1]}" for i, k in enumerate(knowledge)])
#     messages = [
#         LLMMessage("system", qa_system_prompt).to_dict(),
#         LLMMessage("user", f"问题：\n{question}\n\n可能有帮助的信息：\n{knowledge}").to_dict(),
#     ]
#     return messages
