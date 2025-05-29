from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.heart_flow.observation.observation import Observation
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
import time
import traceback
from src.common.logger_manager import get_logger
from src.individuality.individuality import individuality
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.utils.json_utils import safe_json_dumps
from src.chat.message_receive.chat_stream import chat_manager
from src.person_info.relationship_manager import relationship_manager
from .base_processor import BaseProcessor
from src.chat.focus_chat.info.mind_info import MindInfo
from typing import List, Optional
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from typing import Dict
from src.chat.focus_chat.info.info_base import InfoBase

logger = get_logger("processor")


def init_prompt():
    group_prompt = """
你的名字是{bot_name}
{memory_str}
{extra_info}
{relation_prompt}
{cycle_info_block}
现在是{time_now}，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
{chat_observe_info}

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
{extra_info}
{relation_prompt}
{cycle_info_block}
现在是{time_now}，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
{chat_observe_info}

以下是你之前对聊天的观察和规划，你的名字是{bot_name}：
{last_mind}

现在请你继续输出观察和规划，输出要求：
1. 先关注未读新消息的内容和近期回复历史
2. 根据新信息，修改和删除之前的观察和规划
3. 根据聊天内容继续输出观察和规划
4. 注意群聊的时间线索，话题由谁发起，进展状况如何，思考聊天的时间线。
6. 语言简洁自然，不要分点，不要浮夸，不要修辞，仅输出思考内容就好"""
    Prompt(private_prompt, "sub_heartflow_prompt_private_before")


class MindProcessor(BaseProcessor):
    log_prefix = "聊天思考"

    def __init__(self, subheartflow_id: str):
        super().__init__()

        self.subheartflow_id = subheartflow_id

        self.llm_model = LLMRequest(
            model=global_config.model.focus_chat_mind,
            temperature=global_config.model.focus_chat_mind["temp"],
            max_tokens=800,
            request_type="focus_chat_mind",
        )

        self.current_mind = ""
        self.past_mind = []
        self.structured_info = []
        self.structured_info_str = ""

        name = chat_manager.get_stream_name(self.subheartflow_id)
        self.log_prefix = f"[{name}] "
        self._update_structured_info_str()

    def _update_structured_info_str(self):
        """根据 structured_info 更新 structured_info_str"""
        if not self.structured_info:
            self.structured_info_str = ""
            return

        lines = ["【信息】"]
        for item in self.structured_info:
            # 简化展示，突出内容和类型，包含TTL供调试
            type_str = item.get("type", "未知类型")
            content_str = item.get("content", "")

            if type_str == "info":
                lines.append(f"刚刚: {content_str}")
            elif type_str == "memory":
                lines.append(f"{content_str}")
            elif type_str == "comparison_result":
                lines.append(f"数字大小比较结果: {content_str}")
            elif type_str == "time_info":
                lines.append(f"{content_str}")
            elif type_str == "lpmm_knowledge":
                lines.append(f"你知道：{content_str}")
            else:
                lines.append(f"{type_str}的信息: {content_str}")

        self.structured_info_str = "\n".join(lines)
        logger.debug(f"{self.log_prefix} 更新 structured_info_str: \n{self.structured_info_str}")

    async def process_info(
        self, observations: Optional[List[Observation]] = None, running_memorys: Optional[List[Dict]] = None, *infos
    ) -> List[InfoBase]:
        """处理信息对象

        Args:
            *infos: 可变数量的InfoBase类型的信息对象

        Returns:
            List[InfoBase]: 处理后的结构化信息列表
        """
        current_mind = await self.do_thinking_before_reply(observations, running_memorys)

        mind_info = MindInfo()
        mind_info.set_current_mind(current_mind)

        return [mind_info]

    async def do_thinking_before_reply(
        self, observations: Optional[List[Observation]] = None, running_memorys: Optional[List[Dict]] = None
    ):
        """
        在回复前进行思考，生成内心想法并收集工具调用结果

        参数:
            observations: 观察信息

        返回:
            如果return_prompt为False:
                tuple: (current_mind, past_mind) 当前想法和过去的想法列表
            如果return_prompt为True:
                tuple: (current_mind, past_mind, prompt) 当前想法、过去的想法列表和使用的prompt
        """

        # ---------- 0. 更新和清理 structured_info ----------
        if self.structured_info:
            updated_info = []
            for item in self.structured_info:
                item["ttl"] -= 1
                if item["ttl"] > 0:
                    updated_info.append(item)
                else:
                    logger.debug(f"{self.log_prefix} 移除过期的 structured_info 项: {item['id']}")
            self.structured_info = updated_info
            self._update_structured_info_str()
        logger.debug(
            f"{self.log_prefix} 当前完整的 structured_info: {safe_json_dumps(self.structured_info, ensure_ascii=False)}"
        )

        memory_str = ""
        if running_memorys:
            memory_str = "以下是当前在聊天中，你回忆起的记忆：\n"
            for running_memory in running_memorys:
                memory_str += f"{running_memory['topic']}: {running_memory['content']}\n"

        # ---------- 1. 准备基础数据 ----------
        # 获取现有想法和情绪状态
        previous_mind = self.current_mind if self.current_mind else ""

        if observations is None:
            observations = []
        for observation in observations:
            if isinstance(observation, ChattingObservation):
                # 获取聊天元信息
                is_group_chat = observation.is_group_chat
                chat_target_info = observation.chat_target_info
                chat_target_name = "对方"  # 私聊默认名称
                if not is_group_chat and chat_target_info:
                    # 优先使用person_name，其次user_nickname，最后回退到默认值
                    chat_target_name = (
                        chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or chat_target_name
                    )
                # 获取聊天内容
                chat_observe_info = observation.get_observe_info()
                person_list = observation.person_list
            if isinstance(observation, HFCloopObservation):
                hfcloop_observe_info = observation.get_observe_info()

        # ---------- 3. 准备个性化数据 ----------
        # 获取个性化信息

        relation_prompt = ""
        for person in person_list:
            relation_prompt += await relationship_manager.build_relationship_info(person, is_id=True)

        template_name = "sub_heartflow_prompt_before" if is_group_chat else "sub_heartflow_prompt_private_before"
        logger.debug(f"{self.log_prefix} 使用{'群聊' if is_group_chat else '私聊'}思考模板")

        prompt = (await global_prompt_manager.get_prompt_async(template_name)).format(
            bot_name=individuality.name,
            memory_str=memory_str,
            extra_info=self.structured_info_str,
            relation_prompt=relation_prompt,
            time_now=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            chat_observe_info=chat_observe_info,
            last_mind=previous_mind,
            cycle_info_block=hfcloop_observe_info,
            chat_target_name=chat_target_name,
        )

        content = "(不知道该想些什么...)"
        try:
            content, _ = await self.llm_model.generate_response_async(prompt=prompt)
            if not content:
                logger.warning(f"{self.log_prefix} LLM返回空结果，思考失败。")
        except Exception as e:
            # 处理总体异常
            logger.error(f"{self.log_prefix} 执行LLM请求或处理响应时出错: {e}")
            logger.error(traceback.format_exc())
            content = "思考过程中出现错误"

        # 记录初步思考结果
        logger.debug(f"{self.log_prefix} 思考prompt: \n{prompt}\n")
        logger.info(f"{self.log_prefix} 聊天规划: {content}")
        self.update_current_mind(content)

        return content

    def update_current_mind(self, response):
        if self.current_mind:  # 只有当 current_mind 非空时才添加到 past_mind
            self.past_mind.append(self.current_mind)
        self.current_mind = response


init_prompt()
