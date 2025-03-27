from src.common.logger import get_module_logger
logger = get_module_logger("action_executer")

class ResponseAction:
    def __init__(self):
        self.tags = []
        self.msgs = []

    def parse_action(self, msg: str, action: str) -> str:
        if action in msg:
            logger.info(f"从消息中解析到{action}标签")
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

    
from .actions import usable_action_description

extern_prompt = f"""
`<UseableAction>`
{usable_action_description}
`</UseableAction>`
你可以使用**`<UseableAction>`**中给出的标签来执行特定动作，请参考对应部分的描述。
注意，标签一定是以“[内容]”形式输出的，你可以在一次响应中执行多个动作。
"""

logger.info("成功加载可用动作")