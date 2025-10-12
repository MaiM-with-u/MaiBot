from typing import Tuple

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.utils.prompt_builder import Prompt
from src.llm_models.payload_content.tool_option import ToolParamType
from src.plugin_system import BaseAction, ActionActivationType
from src.chat.utils.utils import cut_key_words
from src.memory_system.Memory_chest import global_memory_chest
from src.plugin_system.base.base_tool import BaseTool
from typing import Any

logger = get_logger("memory")
class GetMemoryTool(BaseTool):
    """获取用户信息"""

    name = "get_memory"
    description = "在记忆中搜索，获取某个问题的答案"
    parameters = [
        ("question", ToolParamType.STRING, "需要获取答案的问题", True, None)
    ]
    
    available_for_llm = True

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行比较两个数的大小

        Args:
            function_args: 工具参数

        Returns:
            dict: 工具执行结果
        """
        question: str = function_args.get("question")  # type: ignore

        answer = await global_memory_chest.get_answer_by_question(question=question)
        if not answer:
            return {"content": f"问题：{question}，没有找到相关记忆"}
        
        return {"content": f"问题：{question}，答案：{answer}"}

class GetMemoryAction(BaseAction):
    """关系动作 - 获取记忆"""
    
    activation_type = ActionActivationType.LLM_JUDGE
    parallel_action = True
    
        # 动作基本信息
    action_name = "get_memory"
    action_description = (
        "在记忆中搜寻某个问题的答案"
    )

    # 动作参数定义
    action_parameters = {
        "question": "需要搜寻或回答的问题",
    }

    # 动作使用场景
    action_require = [
        "在记忆中搜寻某个问题的答案",
        "有你不了解的概念",
        "有人提问关于过去的事情"
        "你需要根据记忆回答某个问题",
    ]
    
    # 关联类型
    associated_types = ["text"]
    
    async def execute(self) -> Tuple[bool, str]:
        """执行关系动作"""
        
        question = self.action_data.get("question", "")
        answer = await global_memory_chest.get_answer_by_question(self.chat_id, question)
        if not answer:
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"你回忆了有关问题：{question}的记忆，但是没有找到相关记忆",
                action_done=True,
            )
            
            return False, f"问题：{question}，没有找到相关记忆"
        
        await self.store_action_info(
            action_build_into_prompt=True,
            action_prompt_display=f"你回忆了有关问题：{question}的记忆，答案是：{answer}",
            action_done=True,
        )
        
        return True, f"成功获取记忆: {answer}"
