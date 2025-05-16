from typing import List, Dict, Any
from .chat_observer import ChatObserver
from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from src.chat.person_info.person_info import person_info_manager
from src.chat.person_info.relationship_manager import relationship_manager
from src.chat.utils.chat_message_builder import build_readable_messages
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from .pfc_utils import get_items_from_json, adjust_relationship_value_nonlinear
from src.config.config import global_config  # 导入全局配置 (向上两级到 src/, 再到 config)


logger = get_logger("pfc_relationship")


class PfcRelationshipUpdater:
    def __init__(self, private_name: str, bot_name: str):
        """
        初始化关系更新器。

        Args:
            private_name (str): 当前私聊对象的名称 (用于日志)。
            bot_name (str): 机器人自己的名称。
        """
        self.private_name = private_name
        self.bot_name = bot_name
        self.person_info_mng = person_info_manager
        self.relationship_mng = relationship_manager  # 复用其实例方法

        # LLM 实例 (为关系评估创建一个新的)
        # 尝试读取 llm_PFC_relationship_eval 配置，如果不存在则回退
        llm_config_rel_eval = getattr(global_config, "llm_PFC_relationship_eval", None)
        if llm_config_rel_eval and isinstance(llm_config_rel_eval, dict):
            logger.info(f"[私聊][{self.private_name}] 使用 llm_PFC_relationship_eval 配置初始化关系评估LLM。")
            self.llm = LLMRequest(
                model=llm_config_rel_eval,
                temperature=llm_config_rel_eval.get("temp", 0.5),  # 判断任务通常用较低温度
                max_tokens=llm_config_rel_eval.get("max_tokens", 512),
                request_type="pfc_relationship_evaluation",
            )
        else:
            logger.warning(
                f"[私聊][{self.private_name}] 未找到 llm_PFC_relationship_eval 配置或配置无效，将回退使用 llm_PFC_action_planner 的配置。"
            )
            llm_config_action_planner = getattr(global_config, "llm_PFC_action_planner", None)
            if llm_config_action_planner and isinstance(llm_config_action_planner, dict):
                self.llm = LLMRequest(
                    model=llm_config_action_planner,  # 使用 action_planner 的模型配置
                    temperature=llm_config_action_planner.get("temp", 0.5),  # 但温度可以尝试低一些
                    max_tokens=llm_config_action_planner.get("max_tokens", 512),
                    request_type="pfc_relationship_evaluation_fallback",
                )
            else:  # 极端情况，连 action_planner 的配置都没有
                logger.error(f"[私聊][{self.private_name}] 无法找到任何有效的LLM配置用于关系评估！关系更新功能将受限。")
                self.llm = None  # LLM 未初始化

        # 从 global_config 读取参数，若无则使用默认值
        self.REL_INCREMENTAL_INTERVAL = getattr(global_config, "pfc_relationship_incremental_interval", 10)
        self.REL_INCREMENTAL_MSG_COUNT = getattr(global_config, "pfc_relationship_incremental_msg_count", 10)
        self.REL_INCREMENTAL_DEFAULT_CHANGE = getattr(global_config, "pfc_relationship_incremental_default_change", 1.0)
        self.REL_INCREMENTAL_MAX_CHANGE = getattr(global_config, "pfc_relationship_incremental_max_change", 5.0)

        self.REL_FINAL_MSG_COUNT = getattr(global_config, "pfc_relationship_final_msg_count", 30)
        self.REL_FINAL_DEFAULT_CHANGE = getattr(global_config, "pfc_relationship_final_default_change", 5.0)
        self.REL_FINAL_MAX_CHANGE = getattr(global_config, "pfc_relationship_final_max_change", 50.0)

    async def update_relationship_incremental(
        self,
        conversation_info: ConversationInfo,
        observation_info: ObservationInfo,
        chat_observer_for_history: ChatObserver,  # ChatObserver 实例
    ) -> None:
        if not self.llm:
            logger.error(f"[私聊][{self.private_name}] LLM未初始化，无法进行增量关系更新。")
            return
        if not conversation_info or not conversation_info.person_id or not observation_info:
            logger.debug(f"[私聊][{self.private_name}] 增量关系更新：缺少必要信息。")
            return

        if not (
            conversation_info.current_instance_message_count % self.REL_INCREMENTAL_INTERVAL == 0
            and conversation_info.current_instance_message_count > 0
        ):
            return

        logger.info(
            f"[私聊][{self.private_name}] 达到增量关系更新阈值 ({conversation_info.current_instance_message_count}条消息)，开始评估..."
        )

        messages_for_eval: List[Dict[str, Any]] = []
        if chat_observer_for_history:
            messages_for_eval = chat_observer_for_history.get_cached_messages(limit=self.REL_INCREMENTAL_MSG_COUNT)
        elif observation_info.chat_history:
            messages_for_eval = observation_info.chat_history[-self.REL_INCREMENTAL_MSG_COUNT :]

        if not messages_for_eval:
            logger.warning(f"[私聊][{self.private_name}] 增量关系更新：没有足够的消息进行评估。")
            return

        readable_history_for_llm = await build_readable_messages(
            messages_for_eval, replace_bot_name=True, merge_messages=False, timestamp_mode="relative"
        )

        current_relationship_value = await self.person_info_mng.get_value(
            conversation_info.person_id, "relationship_value"
        )
        current_relationship_value = self.relationship_mng.ensure_float(
            current_relationship_value, conversation_info.person_id
        )

        relationship_prompt = f"""你是{self.bot_name}。你正在与{self.private_name}私聊。
你们当前的关系值大约是 {current_relationship_value:.0f} (范围通常在-1000到1000，越高越代表关系越好)。
以下是你们最近的对话内容：
---
{readable_history_for_llm}
---
请基于以上对话，判断你与{self.private_name}的关系值应该如何“谨慎地”调整。
请输出一个JSON对象，包含一个 "adjustment" 字段，其值为一个介于 -{self.REL_INCREMENTAL_MAX_CHANGE} 和 +{self.REL_INCREMENTAL_MAX_CHANGE} 之间的整数，代表关系值的变化。
例如：{{ "adjustment": 3 }}。如果对话内容不明确或难以判断，请倾向于输出较小的调整值（如0, 1, -1）。"""

        raw_adjustment_val = self.REL_INCREMENTAL_DEFAULT_CHANGE
        try:
            logger.debug(f"[私聊][{self.private_name}] 增量关系评估Prompt:\n{relationship_prompt}")
            content, _ = await self.llm.generate_response_async(relationship_prompt)
            logger.debug(f"[私聊][{self.private_name}] 增量关系评估LLM原始返回: {content}")

            success, result = get_items_from_json(
                content,
                self.private_name,
                "adjustment",
                default_values={"adjustment": self.REL_INCREMENTAL_DEFAULT_CHANGE},
                required_types={"adjustment": (int, float)},
            )
            raw_adjustment = result.get("adjustment", self.REL_INCREMENTAL_DEFAULT_CHANGE)
            if not isinstance(raw_adjustment, (int, float)):
                raw_adjustment_val = self.REL_INCREMENTAL_DEFAULT_CHANGE
            else:
                raw_adjustment_val = float(raw_adjustment)
            raw_adjustment_val = max(
                -self.REL_INCREMENTAL_MAX_CHANGE, min(self.REL_INCREMENTAL_MAX_CHANGE, raw_adjustment_val)
            )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 增量关系评估LLM调用或解析失败: {e}")

        adjustment_val = await adjust_relationship_value_nonlinear(current_relationship_value, raw_adjustment_val)

        new_relationship_value = max(-1000.0, min(1000.0, current_relationship_value + adjustment_val))
        await self.person_info_mng.update_one_field(
            conversation_info.person_id, "relationship_value", new_relationship_value
        )
        logger.info(
            f"[私聊][{self.private_name}] 增量关系值更新：与【{self.private_name}】的关系值从 {current_relationship_value:.2f} 调整了 {adjustment_val:.2f}，变为 {new_relationship_value:.2f}"
        )

        if conversation_info.person_id:
            conversation_info.relationship_text = await self.relationship_mng.build_relationship_info(
                conversation_info.person_id, is_id=True
            )

    async def update_relationship_final(
        self,
        conversation_info: ConversationInfo,
        observation_info: ObservationInfo,
        chat_observer_for_history: ChatObserver,
    ) -> None:
        if not self.llm:
            logger.error(f"[私聊][{self.private_name}] LLM未初始化，无法进行最终关系更新。")
            return
        if not conversation_info or not conversation_info.person_id or not observation_info:
            logger.debug(f"[私聊][{self.private_name}] 最终关系更新：缺少必要信息。")
            return

        logger.info(f"[私聊][{self.private_name}] 私聊结束，开始最终关系评估...")

        messages_for_eval: List[Dict[str, Any]] = []
        if chat_observer_for_history:
            messages_for_eval = chat_observer_for_history.get_cached_messages(limit=self.REL_FINAL_MSG_COUNT)
        elif observation_info.chat_history:
            messages_for_eval = observation_info.chat_history[-self.REL_FINAL_MSG_COUNT :]

        if not messages_for_eval:
            logger.warning(f"[私聊][{self.private_name}] 最终关系更新：没有足够的消息进行评估。")
            return

        readable_history_for_llm = await build_readable_messages(
            messages_for_eval, replace_bot_name=True, merge_messages=False, timestamp_mode="relative"
        )

        current_relationship_value = await self.person_info_mng.get_value(
            conversation_info.person_id, "relationship_value"
        )
        current_relationship_value = self.relationship_mng.ensure_float(
            current_relationship_value, conversation_info.person_id
        )

        relationship_prompt = f"""你是{self.bot_name}。你与{self.private_name}的私聊刚刚结束。
你们当前的关系值大约是 {current_relationship_value:.0f} (范围通常在-1000到1000，越高越好)。
以下是你们本次私聊最后部分的对话内容：
---
{readable_history_for_llm}
---
请基于以上对话的整体情况，判断你与【{self.private_name}】的关系值应该如何进行一次总结性的调整。
请输出一个JSON对象，包含一个 "final_adjustment" 字段，其值为一个整数，代表关系值的变化量（例如，可以是 -{self.REL_FINAL_MAX_CHANGE} 到 +{self.REL_FINAL_MAX_CHANGE} 之间的一个值）。
请大胆评估，但也要合理。"""

        raw_adjustment_val = self.REL_FINAL_DEFAULT_CHANGE
        try:
            logger.debug(f"[私聊][{self.private_name}] 最终关系评估Prompt:\n{relationship_prompt}")
            content, _ = await self.llm.generate_response_async(relationship_prompt)
            logger.debug(f"[私聊][{self.private_name}] 最终关系评估LLM原始返回: {content}")

            success, result = get_items_from_json(
                content,
                self.private_name,
                "final_adjustment",
                default_values={"final_adjustment": self.REL_FINAL_DEFAULT_CHANGE},
                required_types={"final_adjustment": (int, float)},
            )
            raw_adjustment = result.get("final_adjustment", self.REL_FINAL_DEFAULT_CHANGE)
            if not isinstance(raw_adjustment, (int, float)):
                raw_adjustment_val = self.REL_FINAL_DEFAULT_CHANGE
            else:
                raw_adjustment_val = float(raw_adjustment)
            raw_adjustment_val = max(
                -self.REL_INCREMENTAL_MAX_CHANGE, min(self.REL_INCREMENTAL_MAX_CHANGE, raw_adjustment_val)
            )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 最终关系评估LLM调用或解析失败: {e}")

        adjustment_val = await adjust_relationship_value_nonlinear(current_relationship_value, raw_adjustment_val)

        new_relationship_value = max(-1000.0, min(1000.0, current_relationship_value + adjustment_val))
        await self.person_info_mng.update_one_field(
            conversation_info.person_id, "relationship_value", new_relationship_value
        )
        logger.info(
            f"[私聊][{self.private_name}] 最终关系值更新：与【{self.private_name}】的关系值从 {current_relationship_value:.2f} 调整了 {adjustment_val:.2f}，最终为 {new_relationship_value:.2f}"
        )

        if conversation_info.person_id:  # 虽然通常结束了，但更新一下无妨
            conversation_info.relationship_text = await self.relationship_mng.build_relationship_info(
                conversation_info.person_id, is_id=True
            )


class PfcRepationshipTranslator:
    """直接完整导入群聊的relationship_manager.py可能不可取
    因为对于PFC的planner来说
    其暗示了选择回复
    所以新建代码文件来适配PFC的决策层面"""

    def __init__(self, private_name: str):
        self.private_name = private_name

    async def translate_relationship_value_to_text(self, relationship_value: float) -> str:
        """
        将数值型的关系值转换为PFC私聊场景下简洁的关系描述文本。
        """
        level_num = self._calculate_relationship_level_num(relationship_value, self.private_name)

        relationship_descriptions = [
            "厌恶",  # level_num 0
            "冷漠",  # level_num 1
            "初识",  # level_num 2
            "友好",  # level_num 3
            "喜欢",  # level_num 4
            "依赖",  # level_num 5
        ]

        if 0 <= level_num < len(relationship_descriptions):
            description = relationship_descriptions[level_num]
        else:
            description = "普通"  # 默认或错误情况
            logger.warning(f"[私聊][{self.private_name}] 计算出的 level_num ({level_num}) 无效，关系描述默认为 '普通'")

        return f"{description}。"

    @staticmethod
    def _calculate_relationship_level_num(relationship_value: float, private_name: str) -> int:
        """
        根据关系值计算关系等级编号 (0-5)。
        这里的阈值应与 relationship_manager.py 中的保持一致
        """
        if not isinstance(relationship_value, (int, float)):
            logger.warning(
                f"[私聊][{private_name}] 传入的 relationship_value '{relationship_value}' 不是有效的数值类型，默认为0。"
            )
            relationship_value = 0.0

        if -1000 <= relationship_value < -227:
            level_num = 0  # 厌恶
        elif -227 <= relationship_value < -73:
            level_num = 1  # 冷漠
        elif -73 <= relationship_value < 227:
            level_num = 2  # 普通/认识
        elif 227 <= relationship_value < 587:
            level_num = 3  # 友好
        elif 587 <= relationship_value < 900:
            level_num = 4  # 喜欢
        elif 900 <= relationship_value <= 1000:
            level_num = 5  # 暧昧
        else:
            # 超出范围的值处理
            if relationship_value > 1000:
                level_num = 5
            elif relationship_value < -1000:
                level_num = 0
            else:  # 理论上不会到这里，除非前面的条件逻辑有误
                logger.warning(f"[私聊][{private_name}] 关系值 {relationship_value} 未落入任何预设范围，默认为普通。")
                level_num = 2
        return level_num
