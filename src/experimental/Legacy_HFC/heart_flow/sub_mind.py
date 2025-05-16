from .observation import ChattingObservation
from src.chat.knowledge.knowledge_lib import qa_manager
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from ..schedule.schedule_generator import bot_schedule
from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat, build_readable_messages
from src.plugins.group_nickname.nickname_manager import nickname_manager
import time
import re
import traceback
from src.common.logger_manager import get_logger
from src.individuality.individuality import Individuality
import random
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.tools.tool_use import ToolUser
from src.chat.utils.json_utils import safe_json_dumps, process_llm_tool_calls
from .chat_state_info import ChatStateInfo
from src.chat.message_receive.chat_stream import chat_manager
from ..heartFC_Cycleinfo import CycleInfo
import difflib
from src.chat.person_info.relationship_manager import relationship_manager
from src.chat.memory_system.Hippocampus import HippocampusManager
import jieba


logger = get_logger("sub_heartflow")


def init_prompt():
    # --- Group Chat Prompt ---
    group_prompt = """
<identity>
    <bot_name>你的名字是{bot_name}。</bot_name>
    <personality_profile>{prompt_personality}</personality_profile>
</identity>

<group_nicknames>
{nickname_info}
</group_nicknames>

<knowledge_base>
    <structured_information>{extra_info}</structured_information>
    <social_relationships>{relation_prompt}</social_relationships>
</knowledge_base>

<recent_internal_state>
    <previous_thoughts_and_actions>{last_loop_prompt}</previous_thoughts_and_actions>
    <recent_reply_history>{cycle_info_block}</recent_reply_history>
    <current_schedule>你现在正在做的事情是：{schedule_info}</current_schedule>
    <current_mood>你现在{mood_info}</current_mood>
</recent_internal_state>

<live_chat_context>
    <timestamp>现在是{time_now}。</timestamp>
    <chat_log>你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
{chat_observe_info}</chat_log>
</live_chat_context>

<thinking_guidance>
请仔细阅读当前聊天内容，分析讨论话题和群成员关系，分析你刚刚发言和别人对你的发言的反应，思考你要不要回复或发言。然后思考你是否需要使用函数工具。
请特别留意对话的节奏。如果你发送消息后没有得到回应，那么在考虑发言或追问时，请务必谨慎。优先考虑是否你的上一条信息已经结束了当前话题，或者对方暂时不方便回复。除非你有非常重要且有时效性的新事情，否则避免在对方无明显回应意愿时进行追问。
思考并输出你真实的内心想法。
</thinking_guidance>


<output_requirements_for_inner_thought>
1. 根据聊天内容生成你的想法（如果你现在很忙或没时间，可以倾向选择不回复），{hf_do_next}
2. 不要分点、不要使用表情符号
3. 避免多余符号(冒号、引号、括号等)
4. 语言简洁自然，不要浮夸
5. 当你发送消息后没人理你，你的内心想法应倾向于“耐心等待对方回复”或“思考是否对方正在忙”，而不是立即产生追问的想法。只有当你认为追问确实必要且不会打扰对方时，才考虑生成追问的意图。
6. 不要把注意力放在别人发的表情包上，它们只是一种辅助表达方式
7. 注意分辨群里谁在跟谁说话，你不一定是当前聊天的主角，消息中的“你”不一定指的是你（{bot_name}），也可能是别人
8. 思考要不要回复或发言，如果要，必须**明确写出**你准备发送的消息的具体内容是什么
9. 默认使用中文
</output_requirements_for_inner_thought>

<tool_usage_instructions>
1. 输出想法后考虑是否需要使用工具
2. 工具可获取信息或执行操作
3. 如需处理消息或回复，请使用工具。
</tool_usage_instructions>

"""
    Prompt(group_prompt, "sub_heartflow_prompt_before")

    # --- Private Chat Prompt ---
    private_prompt = """
{extra_info}
{relation_prompt}
你的名字是{bot_name},{prompt_personality}
{last_loop_prompt}
{cycle_info_block}
现在是{time_now}，你正在上网，和 {chat_target_name} 私聊，以下是你们的聊天内容：
{chat_observe_info}

你现在正在做的事情是：{schedule_info}

你现在{mood_info}
请仔细阅读聊天内容，想想你和 {chat_target_name} 的关系，回顾你们刚刚的交流,你刚刚发言和对方的反应，思考聊天的主题。
请思考你要不要回复以及如何回复对方。然后思考你是否需要使用函数工具。
思考并输出你的内心想法
输出要求：
1. 根据聊天内容生成你的想法，{hf_do_next}
2. 不要分点、不要使用表情符号
3. 避免多余符号(冒号、引号、括号等)
4. 语言简洁自然，不要浮夸
5. 如果你刚发言，对方没有回复你，请谨慎回复
6. 不要把注意力放在别人发的表情包上，它们只是一种辅助表达方式
工具使用说明：
1. 输出想法后考虑是否需要使用工具
2. 工具可获取信息或执行操作
3. 如需处理消息或回复，请使用工具。"""
    Prompt(private_prompt, "sub_heartflow_prompt_private_before")  # New template name

    # --- Last Loop Prompt (remains the same) ---
    last_loop_t = """
刚刚你的内心想法是：{current_thinking_info}
{if_replan_prompt}
"""
    Prompt(last_loop_t, "last_loop")


def parse_knowledge_and_get_max_relevance(knowledge_str: str) -> str | float:
    """
    解析 qa_manager.get_knowledge 返回的字符串，提取所有知识的文本和最高的相关性得分。
    返回: (原始知识字符串, 最高相关性得分)，如果无有效相关性则返回 (原始知识字符串, 0.0)
    """
    if not knowledge_str:
        return None, 0.0

    max_relevance = 0.0
    # 正则表达式匹配 "该条知识对于问题的相关性：数字"
    # 我们需要捕获数字部分
    relevance_scores = re.findall(r"该条知识对于问题的相关性：([0-9.]+)", knowledge_str)

    if relevance_scores:
        try:
            max_relevance = max(float(score) for score in relevance_scores)
        except ValueError:
            logger.warning(f"解析相关性得分时出错: {relevance_scores}")
            return knowledge_str, 0.0  # 出错时返回0.0
    else:
        # 如果没有找到 "该条知识对于问题的相关性：" 这样的模式，
        # 说明可能 qa_manager 返回的格式有变，或者没有有效的知识。
        # 在这种情况下，我们无法确定相关性，保守起见返回0.0
        logger.debug(f"在知识字符串中未找到明确的相关性得分标记: '{knowledge_str[:100]}...'")
        return knowledge_str, 0.0

    return knowledge_str, max_relevance


def calculate_similarity(text_a: str, text_b: str) -> float:
    """
    计算两个文本字符串的相似度。
    """
    if not text_a or not text_b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, text_a, text_b)
    return matcher.ratio()


def calculate_replacement_probability(similarity: float) -> float:
    """
    根据相似度计算替换的概率。
    规则：
    - 相似度 <= 0.4: 概率 = 0
    - 相似度 >= 0.9: 概率 = 1
    - 相似度 == 0.6: 概率 = 0.7
    - 0.4 < 相似度 <= 0.6: 线性插值 (0.4, 0) 到 (0.6, 0.7)
    - 0.6 < 相似度 < 0.9: 线性插值 (0.6, 0.7) 到 (0.9, 1.0)
    """
    if similarity <= 0.4:
        return 0.0
    elif similarity >= 0.9:
        return 1.0
    elif 0.4 < similarity <= 0.6:
        # p = 3.5 * s - 1.4
        probability = 3.5 * similarity - 1.4
        return max(0.0, probability)
    else:  # 0.6 < similarity < 0.9
        # p = s + 0.1
        probability = similarity + 0.1
        return min(1.0, max(0.0, probability))


class SubMind:
    def __init__(self, subheartflow_id: str, chat_state: ChatStateInfo, observations: ChattingObservation):
        self.last_active_time = None
        self.subheartflow_id = subheartflow_id

        self.llm_model = LLMRequest(
            model=global_config.llm_sub_heartflow,
            temperature=global_config.llm_sub_heartflow["temp"],
            max_tokens=1000,
            request_type="sub_heart_flow",
        )

        self.chat_state = chat_state
        self.observations = observations

        self.current_mind = ""
        self.past_mind = []
        self.structured_info = []
        self.structured_info_str = ""

        name = chat_manager.get_stream_name(self.subheartflow_id)
        self.log_prefix = f"[{name}] "
        self._update_structured_info_str()
        # 阶梯式筛选
        self.knowledge_retrieval_steps = self.knowledge_retrieval_steps = [
            {"name": "latest_1_msg", "limit": 1, "relevance_threshold": 0.075},  # 新增：最新1条，极高阈值
            {"name": "latest_2_msgs", "limit": 2, "relevance_threshold": 0.065},  # 新增：最新2条，较高阈值
            {"name": "short_window_3_msgs", "limit": 3, "relevance_threshold": 0.050},  # 原有的3条，阈值可保持或微调
            {"name": "medium_window_8_msgs", "limit": 8, "relevance_threshold": 0.030},  # 原有的8条，阈值可保持或微调
            # 完整窗口的回退逻辑保持不变
        ]

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

    async def do_thinking_before_reply(self, history_cycle: list[CycleInfo] = None):
        """
        在回复前进行思考，生成内心想法并收集工具调用结果

        返回:
            tuple: (current_mind, past_mind) 当前想法和过去的想法列表
        """
        # 更新活跃时间
        self.last_active_time = time.time()

        # ---------- 0. 更新和清理 structured_info ----------
        if self.structured_info:
            logger.debug(
                f"{self.log_prefix} 清理前 structured_info 中包含的lpmm_knowledge数量: "
                f"{len([item for item in self.structured_info if item.get('type') == 'lpmm_knowledge'])}"
            )
            # 筛选出所有不是 lpmm_knowledge 类型的条目，或者其他需要保留的条目
            info_to_keep = [item for item in self.structured_info if item.get("type") != "lpmm_knowledge"]

            # 针对我们仅希望 lpmm_knowledge "用完即弃" 的情况：
            processed_info_to_keep = []
            for item in info_to_keep:  # info_to_keep 已经不包含 lpmm_knowledge
                item["ttl"] -= 1
                if item["ttl"] > 0:
                    processed_info_to_keep.append(item)
                else:
                    logger.debug(f"{self.log_prefix} 移除过期的非lpmm_knowledge项: {item.get('id', '未知ID')}")

            self.structured_info = processed_info_to_keep
            logger.debug(
                f"{self.log_prefix} 清理后 structured_info (仅保留非lpmm_knowledge且TTL有效项): "
                f"{safe_json_dumps(self.structured_info, ensure_ascii=False)}"
            )

        # ---------- 1. 准备基础数据 ----------
        # 获取现有想法和情绪状态
        previous_mind = self.current_mind if self.current_mind else ""
        mood_info = self.chat_state.mood

        # 获取观察对象
        observation: ChattingObservation = self.observations[0] if self.observations else None
        if not observation or not hasattr(observation, "is_group_chat"):  # Ensure it's ChattingObservation or similar
            logger.error(f"{self.log_prefix} 无法获取有效的观察对象或缺少聊天类型信息")
            self.update_current_mind("(观察出错了...)")
            return self.current_mind, self.past_mind

        is_group_chat = observation.is_group_chat
        # logger.debug(f"is_group_chat: {is_group_chat}")

        chat_target_info = observation.chat_target_info
        chat_target_name = "对方"  # Default for private
        if not is_group_chat and chat_target_info:
            chat_target_name = (
                chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or chat_target_name
            )
        # --- End getting observation info ---

        # 获取观察内容
        chat_observe_info = observation.get_observe_info()
        person_list = observation.person_list

        try:
            # 获取当前正在做的一件事情，不包含时间信息，以保持简洁
            # 你可以根据需要调整 num 和 time_info 参数
            current_schedule_info = bot_schedule.get_current_num_task(num=1, time_info=False)
            if not current_schedule_info:  # 如果日程为空，给一个默认提示
                current_schedule_info = "当前没有什么特别的安排。"
        except Exception as e:
            logger.error(f"{self.log_prefix} 获取日程信息时出错: {e}")
            current_schedule_info = "摸鱼发呆。"

        # ---------- 2. 获取记忆 ----------
        try:
            # 从聊天内容中提取关键词
            chat_words = set(jieba.cut(chat_observe_info))
            # 过滤掉停用词和单字词
            keywords = [word for word in chat_words if len(word) > 1]
            # 去重并限制数量
            keywords = list(set(keywords))[:5]

            logger.debug(f"{self.log_prefix} 提取的关键词: {keywords}")
            # 检查已有记忆，过滤掉已存在的主题
            existing_topics = set()
            for item in self.structured_info:
                if item["type"] == "memory":
                    existing_topics.add(item["id"])

            # 过滤掉已存在的主题
            filtered_keywords = [k for k in keywords if k not in existing_topics]

            if not filtered_keywords:
                logger.debug(f"{self.log_prefix} 所有关键词对应的记忆都已存在，跳过记忆提取")
            else:
                # 调用记忆系统获取相关记忆
                related_memory = await HippocampusManager.get_instance().get_memory_from_topic(
                    valid_keywords=filtered_keywords, max_memory_num=3, max_memory_length=2, max_depth=3
                )

                logger.debug(f"{self.log_prefix} 获取到的记忆: {related_memory}")

                if related_memory:
                    for topic, memory in related_memory:
                        new_item = {"type": "memory", "id": topic, "content": memory, "ttl": 3}
                        self.structured_info.append(new_item)
                        logger.debug(f"{self.log_prefix} 添加新记忆: {topic} - {memory}")
                else:
                    logger.debug(f"{self.log_prefix} 没有找到相关记忆")

        except Exception as e:
            logger.error(f"{self.log_prefix} 获取记忆时出错: {e}")
            logger.error(traceback.format_exc())

        # ---------- 2.5 阶梯式获取知识库信息 ----------
        final_knowledge_to_add = None
        retrieval_source_info = "未进行知识检索"

        # 确保 observation 对象存在且可用
        if not observation:
            logger.warning(f"{self.log_prefix} Observation 对象不可用，跳过知识库检索。")
        else:
            # 阶段1和阶段2的阶梯检索
            for step_config in self.knowledge_retrieval_steps:
                step_name = step_config["name"]
                limit = step_config["limit"]
                threshold = step_config["relevance_threshold"]

                logger.info(f"{self.log_prefix} 尝试阶梯检索 - 阶段: {step_name} (最近{limit}条, 阈值>{threshold})")

                try:
                    # 1. 获取当前阶段的聊天记录上下文
                    # 我们需要从 observation 中获取原始消息列表来构建特定长度的上下文
                    # get_raw_msg_before_timestamp_with_chat 在 observation.py 中被导入
                    # from src.plugins.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat, build_readable_messages

                    # 需要确保 ChattingObservation 的实例 (self.observations[0]) 能提供 chat_id
                    # 并且 build_readable_messages 可用
                    context_messages_dicts = get_raw_msg_before_timestamp_with_chat(
                        chat_id=observation.chat_id, timestamp=time.time(), limit=limit
                    )

                    if not context_messages_dicts:
                        logger.debug(f"{self.log_prefix} 阶段 '{step_name}' 未获取到聊天记录，跳过此阶段。")
                        continue

                    current_context_text = await build_readable_messages(
                        messages=context_messages_dicts,
                        timestamp_mode="lite",  # 或者您认为适合知识检索的模式
                    )

                    if not current_context_text:
                        logger.debug(f"{self.log_prefix} 阶段 '{step_name}' 构建的上下文为空，跳过此阶段。")
                        continue

                    logger.debug(f"{self.log_prefix} 阶段 '{step_name}' 使用上下文: '{current_context_text[:150]}...'")

                    # 2. 调用知识库进行检索
                    raw_knowledge_str = qa_manager.get_knowledge(current_context_text)

                    if raw_knowledge_str:
                        # 3. 解析知识并检查相关性
                        knowledge_content, max_relevance = parse_knowledge_and_get_max_relevance(raw_knowledge_str)
                        logger.info(f"{self.log_prefix} 阶段 '{step_name}' 检索到知识，最高相关性: {max_relevance:.4f}")

                        if max_relevance >= threshold:
                            logger.info(
                                f"{self.log_prefix} 阶段 '{step_name}' 满足阈值 ({max_relevance:.4f} >= {threshold})，采纳此知识。"
                            )
                            final_knowledge_to_add = knowledge_content
                            retrieval_source_info = f"阶段 '{step_name}' (最近{limit}条, 相关性 {max_relevance:.4f})"
                            break  # 找到符合条件的知识，跳出阶梯循环
                        else:
                            logger.info(
                                f"{self.log_prefix} 阶段 '{step_name}' 未满足阈值 ({max_relevance:.4f} < {threshold})，继续下一阶段。"
                            )
                    else:
                        logger.debug(f"{self.log_prefix} 阶段 '{step_name}' 未从知识库检索到任何内容。")

                except Exception as e_step:
                    logger.error(f"{self.log_prefix} 阶梯检索阶段 '{step_name}' 发生错误: {e_step}")
                    logger.error(traceback.format_exc())
                    continue  # 当前阶段出错，尝试下一阶段

            # 阶段3: 如果前面的阶梯都没有成功，则使用完整的 chat_observe_info (即您配置的20条)
            if not final_knowledge_to_add and chat_observe_info:  # 确保 chat_observe_info 可用
                logger.info(
                    f"{self.log_prefix} 前序阶梯均未满足条件，尝试使用完整观察窗口 ('{observation.max_now_obs_len}'条)进行检索。"
                )
                try:
                    raw_knowledge_str = qa_manager.get_knowledge(chat_observe_info)
                    if raw_knowledge_str:
                        # 对于完整窗口，我们可能不强制要求阈值，或者使用一个较低的阈值
                        # 或者，您可以选择在这里仍然应用一个阈值，例如 self.knowledge_retrieval_steps 中最后一个的阈值，或一个特定值
                        knowledge_content, max_relevance = parse_knowledge_and_get_max_relevance(raw_knowledge_str)
                        logger.info(
                            f"{self.log_prefix} 完整窗口检索到知识，（此处未设阈值，或相关性: {max_relevance:.4f}）。"
                        )
                        final_knowledge_to_add = knowledge_content  # 默认采纳
                        retrieval_source_info = (
                            f"完整窗口 (最多{observation.max_now_obs_len}条, 相关性 {max_relevance:.4f})"
                        )
                    else:
                        logger.debug(f"{self.log_prefix} 完整窗口检索也未找到知识。")
                except Exception as e_full:
                    logger.error(f"{self.log_prefix} 完整窗口知识检索发生错误: {e_full}")
                    logger.error(traceback.format_exc())

            # 将最终选定的知识（如果有）添加到 structured_info
            if final_knowledge_to_add:
                knowledge_item = {
                    "type": "lpmm_knowledge",
                    "id": f"lpmm_knowledge_{time.time()}",
                    "content": final_knowledge_to_add,
                    "ttl": 1,  # 由于是当轮精心选择的，可以让TTL短一些，下次重新评估（或者按照您的意愿设为3）
                }
                # 我们在方法开头已经清理了旧的 lpmm_knowledge，这里直接添加新的
                self.structured_info.append(knowledge_item)
                logger.info(
                    f"{self.log_prefix} 添加了来自 '{retrieval_source_info}' 的知识到 structured_info (ID: {knowledge_item['id']})"
                )
                self._update_structured_info_str()  # 更新字符串表示
            else:
                logger.info(f"{self.log_prefix} 经过所有阶梯检索后，没有最终采纳的知识。")

        # ---------- 3. 准备工具和个性化数据 ----------
        # 初始化工具
        tool_instance = ToolUser()
        tools = tool_instance._define_tools()

        # 获取个性化信息
        individuality = Individuality.get_instance()

        relation_prompt = ""
        # print(f"person_list: {person_list}")
        for person in person_list:
            relation_prompt += await relationship_manager.build_relationship_info(person, is_id=True)

        # print(f"relat22222ion_prompt: {relation_prompt}")

        # 构建个性部分
        prompt_personality = individuality.get_prompt(x_person=2, level=3)

        # 获取当前时间
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # ---------- 4. 构建思考指导部分 ----------
        # 创建本地随机数生成器，基于分钟数作为种子
        local_random = random.Random()
        current_minute = int(time.strftime("%M"))
        local_random.seed(current_minute)

        # 思考指导选项和权重
        hf_options = [
            (
                "可以参考之前的想法，在原来想法的基础上继续思考，但是也要注意话题的推进，**不要在一个话题上停留太久或揪着一个话题不放，除非你觉得真的有必要**",
                0.3,
            ),
            ("可以参考之前的想法，在原来的想法上**尝试新的话题**", 0.3),
            ("不要太深入，注意话题的推进，**不要在一个话题上停留太久或揪着一个话题不放，除非你觉得真的有必要**", 0.2),
            (
                "进行深入思考，但是注意话题的推进，**不要在一个话题上停留太久或揪着一个话题不放，除非你觉得真的有必要**",
                0.2,
            ),
            ("可以参考之前的想法继续思考，并结合你自身的人设，知识，信息，回忆等等", 0.08),
        ]

        last_cycle = history_cycle[-1] if history_cycle else None
        # 上一次决策信息
        if last_cycle is not None:
            last_action = last_cycle.action_type
            last_reasoning = last_cycle.reasoning
            is_replan = last_cycle.replanned
            if is_replan:
                if_replan_prompt = f"但是你有了上述想法之后，有了新消息，你决定重新思考后，你做了：{last_action}\n因为：{last_reasoning}\n"
            else:
                if_replan_prompt = f"出于这个想法，你刚才做了：{last_action}\n因为：{last_reasoning}\n"
        else:
            last_action = ""
            last_reasoning = ""
            is_replan = False
            if_replan_prompt = ""
        if previous_mind:
            last_loop_prompt = (await global_prompt_manager.get_prompt_async("last_loop")).format(
                current_thinking_info=previous_mind, if_replan_prompt=if_replan_prompt
            )
        else:
            last_loop_prompt = ""

        # 准备循环信息块 (分析最近的活动循环)
        recent_active_cycles = []
        for cycle in reversed(history_cycle):
            # 只关心实际执行了动作的循环
            if cycle.action_taken:
                recent_active_cycles.append(cycle)
                # 最多找最近的3个活动循环
                if len(recent_active_cycles) == 3:
                    break

        cycle_info_block = ""
        consecutive_text_replies = 0
        responses_for_prompt = []

        # 检查这最近的活动循环中有多少是连续的文本回复 (从最近的开始看)
        for cycle in recent_active_cycles:
            if cycle.action_type == "text_reply":
                consecutive_text_replies += 1
                # 获取回复内容，如果不存在则返回'[空回复]'
                response_text = cycle.response_info.get("response_text", [])
                # 使用简单的 join 来格式化回复内容列表
                formatted_response = "[空回复]" if not response_text else " ".join(response_text)
                responses_for_prompt.append(formatted_response)
            else:
                # 一旦遇到非文本回复，连续性中断
                break

        # 根据连续文本回复的数量构建提示信息
        # 注意: responses_for_prompt 列表是从最近到最远排序的
        if consecutive_text_replies >= 3:  # 如果最近的三个活动都是文本回复
            cycle_info_block = f'你已经连续回复了三条消息（最近: "{responses_for_prompt[0]}"，第二近: "{responses_for_prompt[1]}"，第三近: "{responses_for_prompt[2]}"）。你回复的有点多了，请注意'
        elif consecutive_text_replies == 2:  # 如果最近的两个活动是文本回复
            cycle_info_block = f'你已经连续回复了两条消息（最近: "{responses_for_prompt[0]}"，第二近: "{responses_for_prompt[1]}"），请注意'
        elif consecutive_text_replies == 1:  # 如果最近的一个活动是文本回复
            cycle_info_block = f'你刚刚已经回复一条消息（内容: "{responses_for_prompt[0]}"）'

        # 包装提示块，增加可读性，即使没有连续回复也给个标记
        if cycle_info_block:
            cycle_info_block = f"\n【近期回复历史】\n{cycle_info_block}\n"
        else:
            # 如果最近的活动循环不是文本回复，或者没有活动循环
            cycle_info_block = "\n【近期回复历史】\n(最近没有连续文本回复)\n"

        # 加权随机选择思考指导
        hf_do_next = local_random.choices(
            [option[0] for option in hf_options], weights=[option[1] for option in hf_options], k=1
        )[0]

        # ---------- 5. 构建最终提示词 ----------
        # --- Choose template based on chat type ---
        nickname_injection_str = ""  # 初始化为空字符串

        if is_group_chat:
            template_name = "sub_heartflow_prompt_before"

            chat_stream = chat_manager.get_stream(self.subheartflow_id)
            if not chat_stream:
                logger.error(f"{self.log_prefix} 无法获取 chat_stream，无法生成绰号信息。")
                nickname_injection_str = "[获取群成员绰号信息失败]"
            else:
                message_list_for_nicknames = get_raw_msg_before_timestamp_with_chat(
                    chat_id=self.subheartflow_id,
                    timestamp=time.time(),
                    limit=global_config.observation_context_size,
                )
                nickname_injection_str = await nickname_manager.get_nickname_prompt_injection(
                    chat_stream, message_list_for_nicknames
                )

            prompt = (await global_prompt_manager.get_prompt_async(template_name)).format(
                extra_info=self.structured_info_str,
                prompt_personality=prompt_personality,
                relation_prompt=relation_prompt,
                bot_name=individuality.name,
                time_now=time_now,
                chat_observe_info=chat_observe_info,
                mood_info=mood_info,
                hf_do_next=hf_do_next,
                last_loop_prompt=last_loop_prompt,
                cycle_info_block=cycle_info_block,
                nickname_info=nickname_injection_str,
                schedule_info=current_schedule_info,
                # chat_target_name is not used in group prompt
            )
        else:  # Private chat
            template_name = "sub_heartflow_prompt_private_before"
            prompt = (await global_prompt_manager.get_prompt_async(template_name)).format(
                extra_info=self.structured_info_str,
                prompt_personality=prompt_personality,
                relation_prompt=relation_prompt,  # Might need adjustment for private context
                bot_name=individuality.name,
                time_now=time_now,
                chat_target_name=chat_target_name,  # Pass target name
                chat_observe_info=chat_observe_info,
                mood_info=mood_info,
                hf_do_next=hf_do_next,
                last_loop_prompt=last_loop_prompt,
                cycle_info_block=cycle_info_block,
                schedule_info=current_schedule_info,
            )
        # --- End choosing template ---

        # ---------- 6. 执行LLM请求并处理响应 ----------
        content = ""  # 初始化内容变量
        _reasoning_content = ""  # 初始化推理内容变量

        try:
            # 调用LLM生成响应
            response, _reasoning_content, tool_calls = await self.llm_model.generate_response_tool_async(
                prompt=prompt, tools=tools
            )

            logger.debug(f"{self.log_prefix} 子心流输出的原始LLM响应: {response}")

            # 直接使用LLM返回的文本响应作为 content
            content = response if response else ""

            if tool_calls:
                # 直接将 tool_calls 传递给处理函数
                success, valid_tool_calls, error_msg = process_llm_tool_calls(
                    tool_calls, log_prefix=f"{self.log_prefix} "
                )

                if success and valid_tool_calls:
                    # 记录工具调用信息
                    tool_calls_str = ", ".join(
                        [call.get("function", {}).get("name", "未知工具") for call in valid_tool_calls]
                    )
                    logger.info(f"{self.log_prefix} 模型请求调用{len(valid_tool_calls)}个工具: {tool_calls_str}")

                    # 收集工具执行结果
                    await self._execute_tool_calls(valid_tool_calls, tool_instance)
                elif not success:
                    logger.warning(f"{self.log_prefix} 处理工具调用时出错: {error_msg}")
            else:
                logger.info(f"{self.log_prefix} 心流未使用工具")

        except Exception as e:
            # 处理总体异常
            logger.error(f"{self.log_prefix} 执行LLM请求或处理响应时出错: {e}")
            logger.error(traceback.format_exc())
            content = "思考过程中出现错误"

        # 记录初步思考结果
        logger.debug(f"{self.log_prefix} 初步心流思考结果: {content}\nprompt: {prompt}\n")

        # 处理空响应情况
        if not content:
            content = "(不知道该想些什么...)"
            logger.warning(f"{self.log_prefix} LLM返回空结果，思考失败。")

        # ---------- 7. 应用概率性去重和修饰 ----------
        if global_config.allow_remove_duplicates:
            new_content = content  # 保存 LLM 直接输出的结果
            try:
                similarity = calculate_similarity(previous_mind, new_content)
                replacement_prob = calculate_replacement_probability(similarity)
                logger.debug(f"{self.log_prefix} 新旧想法相似度: {similarity:.2f}, 替换概率: {replacement_prob:.2f}")

                # 定义词语列表 (移到判断之前)
                yu_qi_ci_liebiao = ["嗯", "哦", "啊", "唉", "哈", "唔"]
                zhuan_zhe_liebiao = ["但是", "不过", "然而", "可是", "只是"]
                cheng_jie_liebiao = ["然后", "接着", "此外", "而且", "另外"]
                zhuan_jie_ci_liebiao = zhuan_zhe_liebiao + cheng_jie_liebiao

                if random.random() < replacement_prob:
                    # 相似度非常高时，尝试去重或特殊处理
                    if similarity == 1.0:
                        logger.debug(f"{self.log_prefix} 想法完全重复 (相似度 1.0)，执行特殊处理...")
                        # 随机截取大约一半内容
                        if len(new_content) > 1:  # 避免内容过短无法截取
                            split_point = max(
                                1, len(new_content) // 2 + random.randint(-len(new_content) // 4, len(new_content) // 4)
                            )
                            truncated_content = new_content[:split_point]
                        else:
                            truncated_content = new_content  # 如果只有一个字符或者为空，就不截取了

                        # 添加语气词和转折/承接词
                        yu_qi_ci = random.choice(yu_qi_ci_liebiao)
                        zhuan_jie_ci = random.choice(zhuan_jie_ci_liebiao)
                        content = f"{yu_qi_ci}{zhuan_jie_ci}，{truncated_content}"
                        logger.debug(f"{self.log_prefix} 想法重复，特殊处理后: {content}")

                    else:
                        # 相似度较高但非100%，执行标准去重逻辑
                        logger.debug(f"{self.log_prefix} 执行概率性去重 (概率: {replacement_prob:.2f})...")
                        matcher = difflib.SequenceMatcher(None, previous_mind, new_content)
                        deduplicated_parts = []
                        last_match_end_in_b = 0
                        for _i, j, n in matcher.get_matching_blocks():
                            if last_match_end_in_b < j:
                                deduplicated_parts.append(new_content[last_match_end_in_b:j])
                            last_match_end_in_b = j + n

                        deduplicated_content = "".join(deduplicated_parts).strip()

                        if deduplicated_content:
                            # 根据概率决定是否添加词语
                            prefix_str = ""
                            if random.random() < 0.3:  # 30% 概率添加语气词
                                prefix_str += random.choice(yu_qi_ci_liebiao)
                            if random.random() < 0.7:  # 70% 概率添加转折/承接词
                                prefix_str += random.choice(zhuan_jie_ci_liebiao)

                            # 组合最终结果
                            if prefix_str:
                                content = f"{prefix_str}，{deduplicated_content}"  # 更新 content
                                logger.debug(f"{self.log_prefix} 去重并添加引导词后: {content}")
                            else:
                                content = deduplicated_content  # 更新 content
                                logger.debug(f"{self.log_prefix} 去重后 (未添加引导词): {content}")
                        else:
                            logger.warning(f"{self.log_prefix} 去重后内容为空，保留原始LLM输出: {new_content}")
                            content = new_content  # 保留原始 content
                else:
                    logger.debug(f"{self.log_prefix} 未执行概率性去重 (概率: {replacement_prob:.2f})")
                    # content 保持 new_content 不变

            except Exception as e:
                logger.error(f"{self.log_prefix} 应用概率性去重或特殊处理时出错: {e}")
                logger.error(traceback.format_exc())
                # 出错时保留原始 content
                content = new_content

        # ---------- 8. 更新思考状态并返回结果 ----------
        logger.info(f"{self.log_prefix} 最终心流思考结果: {content}")
        # 更新当前思考内容
        self.update_current_mind(content)

        return self.current_mind, self.past_mind

    async def _execute_tool_calls(self, tool_calls, tool_instance):
        """
        执行一组工具调用并收集结果

        参数:
            tool_calls: 工具调用列表
            tool_instance: 工具使用器实例
        """
        tool_results = []
        new_structured_items = []  # 收集新产生的结构化信息

        # 执行所有工具调用
        for tool_call in tool_calls:
            try:
                result = await tool_instance._execute_tool_call(tool_call)
                if result:
                    tool_results.append(result)
                    # 创建新的结构化信息项
                    new_item = {
                        "type": result.get("type", "unknown_type"),  # 使用 'type' 键
                        "id": result.get("id", f"fallback_id_{time.time()}"),  # 使用 'id' 键
                        "content": result.get("content", ""),  # 'content' 键保持不变
                        "ttl": 3,
                    }
                    new_structured_items.append(new_item)

            except Exception as tool_e:
                logger.error(f"[{self.subheartflow_id}] 工具执行失败: {tool_e}")
                logger.error(traceback.format_exc())  # 添加 traceback 记录

        # 如果有新的工具结果，记录并更新结构化信息
        if new_structured_items:
            self.structured_info.extend(new_structured_items)  # 添加到现有列表
            logger.debug(f"工具调用收集到新的结构化信息: {safe_json_dumps(new_structured_items, ensure_ascii=False)}")
            # logger.debug(f"当前完整的 structured_info: {safe_json_dumps(self.structured_info, ensure_ascii=False)}") # 可以取消注释以查看完整列表
            self._update_structured_info_str()  # 添加新信息后，更新字符串表示

    def update_current_mind(self, response):
        if self.current_mind:  # 只有当 current_mind 非空时才添加到 past_mind
            self.past_mind.append(self.current_mind)
            # 可以考虑限制 past_mind 的大小，例如:
            # max_past_mind_size = 10
            # if len(self.past_mind) > max_past_mind_size:
            #     self.past_mind.pop(0) # 移除最旧的

        self.current_mind = response


init_prompt()
