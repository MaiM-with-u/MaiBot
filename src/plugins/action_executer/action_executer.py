class ResponseAction:
    def __init__(self):
        self.tags = []

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

    def __bool__(self):
        # 这是为了直接嵌入到llm_generator.py的处理流里 便于跳过消息处理流程
        return False