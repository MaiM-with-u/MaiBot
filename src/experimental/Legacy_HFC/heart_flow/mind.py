import traceback
from typing import TYPE_CHECKING

from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from src.individuality.individuality import Individuality
from src.chat.utils.prompt_builder import global_prompt_manager
from src.config.config import global_config

# Need access to SubHeartflowManager to get minds and update them
if TYPE_CHECKING:
    from .subheartflow_manager import SubHeartflowManager
    from .mai_state_manager import MaiStateInfo


logger = get_logger("sub_heartflow_mind")


class Mind:
    """封装 Mai 的思考过程，包括生成内心独白和汇总想法。"""

    def __init__(self, subheartflow_manager: "SubHeartflowManager", llm_model: LLMRequest):
        self.subheartflow_manager = subheartflow_manager
        self.llm_model = llm_model
        self.individuality = Individuality.get_instance()

    async def do_a_thinking(self, current_main_mind: str, mai_state_info: "MaiStateInfo", schedule_info: str):
        """
        执行一次主心流思考过程，生成新的内心独白。

        Args:
            current_main_mind: 当前的主心流想法。
            mai_state_info: 当前的 Mai 状态信息 (用于获取 mood)。
            schedule_info: 当前的日程信息。

        Returns:
            str: 生成的新的内心独白，如果出错则返回提示信息。
        """
        logger.debug("Mind: 执行思考...")

        # --- 构建 Prompt --- #
        personality_info = (
            self.individuality.get_prompt_snippet()
            if hasattr(self.individuality, "get_prompt_snippet")
            else self.individuality.personality.personality_core
        )
        mood_info = mai_state_info.get_mood_prompt()
        related_memory_info = "memory"  # TODO: Implement memory retrieval

        # Get subflow minds summary via internal method
        try:
            sub_flows_info = await self._get_subflows_summary(current_main_mind, mai_state_info)
        except Exception as e:
            logger.error(f"[Mind Thinking] 获取子心流想法汇总失败: {e}")
            logger.error(traceback.format_exc())
            sub_flows_info = "(获取子心流想法时出错)"

        # Format prompt
        try:
            prompt = (await global_prompt_manager.get_prompt_async("thinking_prompt")).format(
                schedule_info=schedule_info,
                personality_info=personality_info,
                related_memory_info=related_memory_info,
                current_thinking_info=current_main_mind,  # Use passed current mind
                sub_flows_info=sub_flows_info,
                mood_info=mood_info,
            )
        except Exception as e:
            logger.error(f"[Mind Thinking] 格式化 thinking_prompt 失败: {e}")
            return "(思考时格式化Prompt出错...)"

        # --- 调用 LLM --- #
        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)
            if not response:
                logger.warning("[Mind Thinking] 内心独白 LLM 返回空结果。")
                response = "(暂时没什么想法...)"
            logger.info(f"Mind: 新想法生成: {response[:100]}...")  # Log truncated response
            return response
        except Exception as e:
            logger.error(f"[Mind Thinking] 内心独白 LLM 调用失败: {e}")
            logger.error(traceback.format_exc())
            return "(思考时调用LLM出错...)"

    async def _get_subflows_summary(self, current_main_mind: str, mai_state_info: "MaiStateInfo") -> str:
        """获取所有活跃子心流的想法，并使用 LLM 进行汇总。"""
        # 1. Get active minds from SubHeartflowManager
        sub_minds_list = self.subheartflow_manager.get_active_subflow_minds()

        if not sub_minds_list:
            return "(当前没有活跃的子心流想法)"

        minds_str = "\n".join([f"- {mind}" for mind in sub_minds_list])
        logger.debug(f"Mind: 获取到 {len(sub_minds_list)} 个子心流想法进行汇总。")

        # 2. Call LLM for summary
        # --- 构建 Prompt --- #
        personality_info = (
            self.individuality.get_prompt_snippet()
            if hasattr(self.individuality, "get_prompt_snippet")
            else self.individuality.personality.personality_core
        )
        mood_info = mai_state_info.get_mood_prompt()
        bot_name = global_config.BOT_NICKNAME

        try:
            prompt = (await global_prompt_manager.get_prompt_async("mind_summary_prompt")).format(
                personality_info=personality_info,
                bot_name=bot_name,
                current_mind=current_main_mind,  # Use main mind passed for context
                minds_str=minds_str,
                mood_info=mood_info,
            )
        except Exception as e:
            logger.error(f"[Mind Summary] 格式化 mind_summary_prompt 失败: {e}")
            return "(汇总想法时格式化Prompt出错...)"

        # --- 调用 LLM --- #
        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)
            if not response:
                logger.warning("[Mind Summary] 想法汇总 LLM 返回空结果。")
                return "(想法汇总失败...)"
            logger.debug(f"Mind: 子想法汇总完成: {response[:100]}...")
            return response
        except Exception as e:
            logger.error(f"[Mind Summary] 想法汇总 LLM 调用失败: {e}")
            logger.error(traceback.format_exc())
            return "(想法汇总时调用LLM出错...)"

    def update_subflows_with_main_mind(self, main_mind: str):
        """触发 SubHeartflowManager 更新所有子心流的主心流信息。"""
        logger.debug("Mind: 请求更新子心流的主想法信息。")
        self.subheartflow_manager.update_main_mind_in_subflows(main_mind)


# Note: update_current_mind (managing self.current_mind and self.past_mind)
# remains in Heartflow for now, as Heartflow is the central coordinator holding the main state.
# Mind class focuses solely on the *process* of thinking and summarizing.
