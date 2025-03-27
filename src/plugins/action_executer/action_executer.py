class ResponseAction:
    def __init__(self):
        self.tags = []
        self.msgs = []

    def parse_action(self, msg: str, action: str) -> str:
        if action in msg:
            self.tags.append(action)
            return msg.replace(action, '')
        return msg

    def empty(self):
        return len(self.tags) == 0

    def __contains__(self, other: str):
        if isinstance(other, str):
            return other in self.tags
        else:
            # 非str输入直接抛异常
            raise TypeError

    


from ....config.actions import usable_action_description

extern_prompt = f"""
`<UseableAction>`
{'\n'.join(usable_action_description)}
`</UseableAction>`
你可以使用**`<UseableAction>`**中给出的标签来执行特定动作，请参考对应部分的描述。
注意，标签一定是以“[内容]”形式输出的，你可以在一次响应中执行多个动作。
"""