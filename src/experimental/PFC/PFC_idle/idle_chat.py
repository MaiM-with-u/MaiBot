# TODO: 开机自启，遍历所有可发起的聊天流，而不是等待 PFC 实例结束
# TODO: 优化 idle 逻辑 增强其与 PFC 模式的联动
from typing import Optional, Dict, Set
import asyncio
import time
import random
import traceback
from datetime import datetime
from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.chat.models.utils_model import LLMRequest

# from src.plugins.utils.prompt_builder import global_prompt_manager
from src.chat.person_info.person_info import person_info_manager
from src.chat.person_info.relationship_manager import relationship_manager
from src.chat.utils.chat_message_builder import build_readable_messages

# from ...schedule.schedule_generator import bot_schedule
from ..chat_observer import ChatObserver
from ..message_sender import DirectMessageSender
from src.chat.message_receive.chat_stream import ChatStream, chat_manager
from maim_message import UserInfo, Seg
from ..pfc_relationship import PfcRepationshipTranslator
from rich.traceback import install

install(extra_lines=3)

logger = get_logger("pfc_idle_chat")


class IdleChat:
    """主动聊天组件（测试中）

    在以下条件都满足时触发主动聊天：
    1. 当前没有任何活跃的对话实例
    2. 在指定的活动时间内（7:00-23:00）
    3. 根据关系值动态调整触发概率
    4. 上次触发后已经过了足够的冷却时间
    """

    # 单例模式实现
    _instances: Dict[str, "IdleChat"] = {}

    # 全局共享状态，用于跟踪未回复的用户
    _pending_replies: Dict[str, float] = {}  # 用户名 -> 发送时间
    _tried_users: Set[str] = set()  # 已尝试过的用户集合
    _global_lock = asyncio.Lock()  # 保护共享状态的全局锁

    @classmethod
    def get_instance(cls, stream_id: str, private_name: str) -> "IdleChat":
        """获取IdleChat实例（单例模式）

        Args:
            stream_id: 聊天流ID
            private_name: 私聊用户名称

        Returns:
            IdleChat: IdleChat实例
        """
        key = f"{private_name}:{stream_id}"
        if key not in cls._instances:
            cls._instances[key] = cls(stream_id, private_name)
            # 创建实例时自动启动检测
            cls._instances[key].start()
            logger.info(f"[私聊][{private_name}]创建新的IdleChat实例并启动")
        return cls._instances[key]

    @classmethod
    async def register_user_response(cls, private_name: str) -> None:
        """注册用户已回复

        当用户回复消息时调用此方法，将用户从待回复列表中移除

        Args:
            private_name: 私聊用户名称
        """
        async with cls._global_lock:
            if private_name in cls._pending_replies:
                del cls._pending_replies[private_name]
                logger.info(f"[私聊][{private_name}]已回复主动聊天消息，从待回复列表中移除")

    @classmethod
    async def get_next_available_user(cls) -> Optional[str]:
        """获取下一个可用于主动聊天的用户

        优先选择未尝试过的用户，其次是已尝试但超时未回复的用户

        Returns:
            Optional[str]: 下一个可用的用户名，如果没有则返回None
        """
        async with cls._global_lock:
            current_time = time.time()
            timeout_threshold = 7200  # 2小时未回复视为超时

            # 清理超时未回复的用户
            for user, send_time in list(cls._pending_replies.items()):
                if current_time - send_time > timeout_threshold:
                    logger.info(f"[私聊][{user}]超过{timeout_threshold}秒未回复，标记为超时")
                    del cls._pending_replies[user]

            # 获取所有实例中的用户
            all_users = set()
            for key in cls._instances:
                user = key.split(":", 1)[0]
                all_users.add(user)

            # 优先选择未尝试过的用户
            untried_users = all_users - cls._tried_users
            if untried_users:
                next_user = random.choice(list(untried_users))
                cls._tried_users.add(next_user)
                return next_user

            # 如果所有用户都已尝试过，重置尝试集合，从头开始
            if len(cls._tried_users) >= len(all_users):
                cls._tried_users.clear()
                logger.info("[私聊]所有用户都已尝试过，重置尝试列表")
                # 随机选择一个不在待回复列表中的用户
                available_users = all_users - set(cls._pending_replies.keys())
                if available_users:
                    next_user = random.choice(list(available_users))
                    cls._tried_users.add(next_user)
                    return next_user

            return None

    def __init__(self, stream_id: str, private_name: str):
        """初始化主动聊天组件

        Args:
            stream_id: 聊天流ID
            private_name: 私聊用户名称
        """
        self.stream_id = stream_id
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.message_sender = DirectMessageSender(private_name)

        # 添加异步锁，保护对共享变量的访问
        self._lock: asyncio.Lock = asyncio.Lock()

        # LLM请求对象，用于生成主动对话内容
        self.llm = LLMRequest(model=global_config.llm_normal, temperature=0.5, max_tokens=500, request_type="idle_chat")

        # 工作状态
        self.active_instances_count: int = 0
        self.last_trigger_time: float = time.time() - 1500  # 初始化时减少等待时间
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

        # 配置参数 - 从global_config加载
        self.min_cooldown = global_config.min_cooldown  # 最短冷却时间（默认2小时）
        self.max_cooldown = global_config.max_cooldown  # 最长冷却时间（默认5小时）
        self.check_interval = global_config.idle_check_interval * 60  # 检查间隔（默认10分钟，转换为秒）
        self.active_hours_start = 7  # 活动开始时间
        self.active_hours_end = 23  # 活动结束时间

        # 关系值相关
        self.base_trigger_probability = 0.3  # 基础触发概率
        self.relationship_factor = 0.0003  # 关系值影响因子

    def start(self) -> None:
        """启动主动聊天检测"""
        # 检查是否启用了主动聊天功能
        if not global_config.enable_idle_chat:
            logger.info(f"[私聊][{self.private_name}]主动聊天功能已禁用（配置enable_idle_chat=False）")
            return

        if self._running:
            logger.debug(f"[私聊][{self.private_name}]主动聊天功能已在运行中")
            return

        self._running = True
        self._task = asyncio.create_task(self._check_idle_loop())
        logger.info(f"[私聊][{self.private_name}]启动主动聊天检测")

    def stop(self) -> None:
        """停止主动聊天检测"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info(f"[私聊][{self.private_name}]停止主动聊天检测")

    async def increment_active_instances(self) -> None:
        """增加活跃实例计数

        当创建新的对话实例时调用此方法
        """
        async with self._lock:
            self.active_instances_count += 1
            logger.debug(f"[私聊][{self.private_name}]活跃实例数+1，当前：{self.active_instances_count}")

    async def decrement_active_instances(self) -> None:
        """减少活跃实例计数

        当对话实例结束时调用此方法
        """
        async with self._lock:
            self.active_instances_count = max(0, self.active_instances_count - 1)
            logger.debug(f"[私聊][{self.private_name}]活跃实例数-1，当前：{self.active_instances_count}")

    async def update_last_message_time(self, message_time: Optional[float] = None) -> None:
        """更新最后一条消息的时间

        Args:
            message_time: 消息时间戳，如果为None则使用当前时间
        """
        async with self._lock:
            self.last_trigger_time = message_time or time.time()
            logger.debug(f"[私聊][{self.private_name}]更新最后消息时间: {self.last_trigger_time:.2f}")

        # 当用户发送消息时，也应该注册响应
        await self.__class__.register_user_response(self.private_name)

    def _is_active_hours(self) -> bool:
        """检查是否在活动时间内"""
        current_hour = datetime.now().hour
        return self.active_hours_start <= current_hour < self.active_hours_end

    async def _should_trigger(self) -> bool:
        """检查是否应该触发主动聊天"""
        async with self._lock:
            # 确保计数不会出错，重置为0如果发现是负数
            if self.active_instances_count < 0:
                logger.warning(f"[私聊][{self.private_name}]检测到活跃实例数为负数，重置为0")
                self.active_instances_count = 0

            # 检查是否有活跃实例
            if self.active_instances_count > 0:
                logger.debug(f"[私聊][{self.private_name}]存在活跃实例({self.active_instances_count})，不触发主动聊天")
                return False

            # 检查是否在活动时间内
            if not self._is_active_hours():
                logger.debug(f"[私聊][{self.private_name}]不在活动时间内，不触发主动聊天")
                return False

            # 检查冷却时间
            current_time = time.time()
            time_since_last_trigger = current_time - self.last_trigger_time
            if time_since_last_trigger < self.min_cooldown:
                time_left = self.min_cooldown - time_since_last_trigger
                logger.debug(
                    f"[私聊][{self.private_name}]冷却时间未到(已过{time_since_last_trigger:.0f}秒/需要{self.min_cooldown}秒)，还需等待{time_left:.0f}秒，不触发主动聊天"
                )
                return False

            # 强制触发检查 - 如果超过最大冷却时间，增加触发概率
            force_trigger = False
            if time_since_last_trigger > self.max_cooldown * 2:  # 如果超过最大冷却时间的两倍
                force_probability = min(0.6, self.base_trigger_probability * 2)  # 增加概率但不超过0.6
                random_force = random.random()
                force_trigger = random_force < force_probability
                if force_trigger:
                    logger.info(
                        f"[私聊][{self.private_name}]超过最大冷却时间({time_since_last_trigger:.0f}秒)，强制触发主动聊天"
                    )
                    return True

            # 获取关系值
            relationship_value = 0
            try:
                # 尝试获取person_id
                person_id = None
                try:
                    # 先尝试通过昵称获取person_id
                    platform = "qq"  # 默认平台
                    person_id = person_info_manager.get_person_id(platform, self.private_name)

                    # 如果通过昵称获取失败，尝试通过stream_id解析
                    if not person_id:
                        parts = self.stream_id.split("_")
                        if len(parts) >= 2 and parts[0] == "private":
                            user_id = parts[1]
                            platform = parts[2] if len(parts) >= 3 else "qq"
                            try:
                                person_id = person_info_manager.get_person_id(platform, int(user_id))
                            except ValueError:
                                # 如果user_id不是整数，尝试作为字符串使用
                                person_id = person_info_manager.get_person_id(platform, user_id)
                except Exception as e2:
                    logger.warning(f"[私聊][{self.private_name}]尝试获取person_id失败: {str(e2)}")

                # 获取关系值
                if person_id:
                    raw_value = await person_info_manager.get_value(person_id, "relationship_value")
                    relationship_value = relationship_manager.ensure_float(raw_value, person_id)
                    logger.debug(f"[私聊][{self.private_name}]成功获取关系值: {relationship_value}")
                else:
                    logger.warning(f"[私聊][{self.private_name}]无法获取person_id，使用默认关系值0")

                # 使用PfcRepationshipTranslator获取关系描述
                relationship_translator = PfcRepationshipTranslator(self.private_name)
                relationship_level = relationship_translator._calculate_relationship_level_num(
                    relationship_value, self.private_name
                )

                # 基于关系等级调整触发概率
                # 关系越好，主动聊天概率越高
                level_probability_factors = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5]  # 每个等级对应的基础概率因子
                base_probability = level_probability_factors[relationship_level]

                # 基础概率因子
                trigger_probability = base_probability
                trigger_probability = max(0.05, min(0.6, trigger_probability))  # 限制在0.05-0.6之间

                # 最大冷却时间调整 - 随着冷却时间增加，逐渐增加触发概率
                if time_since_last_trigger > self.max_cooldown:
                    # 计算额外概率 - 每超过最大冷却时间的10%，增加1%的概率，最多增加30%
                    extra_time_factor = min(
                        0.3, (time_since_last_trigger - self.max_cooldown) / (self.max_cooldown * 10)
                    )
                    trigger_probability += extra_time_factor
                    logger.debug(f"[私聊][{self.private_name}]超过标准冷却时间，额外增加概率: +{extra_time_factor:.2f}")

                # 随机判断是否触发
                random_value = random.random()
                should_trigger = random_value < trigger_probability
                logger.debug(
                    f"[私聊][{self.private_name}]触发概率计算: 基础({base_probability:.2f}) + 关系值({relationship_value})影响 = {trigger_probability:.2f}，随机值={random_value:.2f}, 结果={should_trigger}"
                )

                # 如果决定触发，记录详细日志
                if should_trigger:
                    logger.info(
                        f"[私聊][{self.private_name}]决定触发主动聊天: 触发概率={trigger_probability:.2f}, 距上次已过{time_since_last_trigger:.0f}秒"
                    )

                return should_trigger

            except Exception as e:
                logger.error(f"[私聊][{self.private_name}]获取关系值失败: {str(e)}")
                logger.error(traceback.format_exc())

                # 即使获取关系值失败，仍有一个基础的几率触发
                # 这确保即使数据库有问题，主动聊天功能仍然可用
                base_fallback_probability = 0.1  # 较低的基础几率
                random_fallback = random.random()
                fallback_trigger = random_fallback < base_fallback_probability
                if fallback_trigger:
                    logger.info(
                        f"[私聊][{self.private_name}]获取关系值失败，使用后备触发机制: 概率={base_fallback_probability:.2f}, 决定={fallback_trigger}"
                    )
                return fallback_trigger

    async def _check_idle_loop(self) -> None:
        """检查空闲状态的循环"""
        try:
            while self._running:
                # 检查是否启用了主动聊天功能
                if not global_config.enable_idle_chat:
                    # 如果禁用了功能，等待一段时间后再次检查配置
                    await asyncio.sleep(60)  # 每分钟检查一次配置变更
                    continue

                # 检查当前用户是否应该触发主动聊天
                should_trigger = await self._should_trigger()

                # 如果当前用户不触发，检查是否有其他用户已经超时未回复
                if not should_trigger:
                    async with self.__class__._global_lock:
                        current_time = time.time()
                        pending_timeout = 1800  # 30分钟未回复检查

                        # 检查此用户是否在等待回复列表中
                        if self.private_name in self.__class__._pending_replies:
                            logger.debug(f"[私聊][{self.private_name}]当前用户在等待回复列表中，不进行额外检查")
                        else:
                            # 查找所有超过30分钟未回复的用户
                            timed_out_users = []
                            for user, send_time in self.__class__._pending_replies.items():
                                if current_time - send_time > pending_timeout:
                                    timed_out_users.append(user)

                            # 如果有超时未回复的用户，尝试找下一个用户
                            if timed_out_users:
                                logger.info(f"[私聊]发现{len(timed_out_users)}个用户超过{pending_timeout}秒未回复")
                                next_user = await self.__class__.get_next_available_user()

                                if next_user and next_user != self.private_name:
                                    logger.info(f"[私聊]选择下一个用户[{next_user}]进行主动聊天")
                                    # 查找该用户的实例并触发聊天
                                    for key, instance in self.__class__._instances.items():
                                        if key.startswith(f"{next_user}:"):
                                            logger.info(f"[私聊]为用户[{next_user}]触发主动聊天")
                                            # 触发该实例的主动聊天
                                            asyncio.create_task(instance._initiate_chat())
                                            break

                # 如果当前用户应该触发主动聊天
                if should_trigger:
                    try:
                        await self._initiate_chat()
                        # 更新上次触发时间
                        async with self._lock:
                            self.last_trigger_time = time.time()

                        # 将此用户添加到等待回复列表中
                        async with self.__class__._global_lock:
                            self.__class__._pending_replies[self.private_name] = time.time()
                            self.__class__._tried_users.add(self.private_name)
                            logger.info(f"[私聊][{self.private_name}]已添加到等待回复列表中")
                    except Exception as e:
                        logger.error(f"[私聊][{self.private_name}]执行主动聊天过程出错: {str(e)}")
                        logger.error(traceback.format_exc())

                # 等待下一次检查
                check_interval = self.check_interval  # 使用配置的检查间隔
                logger.debug(f"[私聊][{self.private_name}]等待{check_interval}秒后进行下一次主动聊天检查")
                await asyncio.sleep(check_interval)

        except asyncio.CancelledError:
            logger.debug(f"[私聊][{self.private_name}]主动聊天检测任务被取消")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]主动聊天检测出错: {str(e)}")
            logger.error(traceback.format_exc())
            # 尝试重新启动检测循环
            if self._running:
                logger.info(f"[私聊][{self.private_name}]尝试重新启动主动聊天检测")
                self._task = asyncio.create_task(self._check_idle_loop())

    async def _get_chat_stream(self) -> Optional[ChatStream]:
        """获取聊天流实例"""
        try:
            existing_chat_stream = chat_manager.get_stream(self.stream_id)
            if existing_chat_stream:
                logger.debug(f"[私聊][{self.private_name}]从chat_manager找到现有聊天流")
                return existing_chat_stream

            # 如果没有现有聊天流，则创建新的
            logger.debug(f"[私聊][{self.private_name}]未找到现有聊天流，创建新聊天流")
            # 创建用户信息对象
            user_info = UserInfo(
                user_id=self.private_name,  # 使用私聊用户的ID
                user_nickname=self.private_name,  # 使用私聊用户的名称
                platform="qq",
            )
            # 创建聊天流
            new_stream = ChatStream(self.stream_id, "qq", user_info)
            # 将新创建的聊天流添加到管理器中
            chat_manager.register_stream(new_stream)
            logger.debug(f"[私聊][{self.private_name}]成功创建并注册新聊天流")
            return new_stream
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]创建/获取聊天流失败: {str(e)}")
            logger.error(traceback.format_exc())
            return None

    async def _initiate_chat(self) -> None:
        """生成并发送主动聊天消息"""
        try:
            # 获取聊天历史记录
            messages = self.chat_observer.get_cached_messages(limit=12)
            chat_history_text = await build_readable_messages(
                messages, replace_bot_name=True, merge_messages=False, timestamp_mode="relative", read_mark=0.0
            )

            # 获取关系值
            relationship_value = 0
            try:
                platform = "qq"
                person_id = person_info_manager.get_person_id(platform, self.private_name)
                if person_id:
                    raw_value = await person_info_manager.get_value(person_id, "relationship_value")
                    relationship_value = relationship_manager.ensure_float(raw_value, person_id)
            except Exception as e:
                logger.warning(f"[私聊][{self.private_name}]获取关系值失败，使用默认值: {e}")

            # 使用PfcRepationshipTranslator获取关系描述
            relationship_translator = PfcRepationshipTranslator(self.private_name)
            full_relationship_text = await relationship_translator.translate_relationship_value_to_text(
                relationship_value
            )

            # 提取纯关系描述（去掉"你们的关系是："前缀）
            relationship_description = "普通"  # 默认值
            if "：" in full_relationship_text:
                relationship_description = full_relationship_text.split("：")[1].replace("。", "")

            # 暂不使用
            # if global_config.ENABLE_SCHEDULE_GEN:
            #     schedule_prompt = await global_prompt_manager.format_prompt(
            #         "schedule_prompt", schedule_info=bot_schedule.get_current_num_task(num=1, time_info=False)
            #     )
            # else:
            #     schedule_prompt = ""

            # 构建提示词，暂存废弃部分这是你的日程{schedule_prompt}
            current_time = datetime.now().strftime("%H:%M")
            prompt = f"""你是{global_config.BOT_NICKNAME}。
            你正在与用户{self.private_name}进行QQ私聊，你们的关系是{relationship_description}
            现在时间{current_time}

            你想要主动发起对话。
            请基于以下之前的对话历史，生成一条自然、友好、符合关系程度的主动对话消息。
            这条消息应能够引起用户的兴趣，重新开始对话。
            最近的对话历史（并不是现在的对话）：
            {chat_history_text}
            请你严格根据对话历史决定是告诉对方你正在做的事情，还是询问对方正在做的事情
            请直接输出一条消息，不要有任何额外的解释或引导文字
            消息内容尽量简短
            """

            # 生成回复
            logger.debug(f"[私聊][{self.private_name}]开始生成主动聊天内容")
            try:
                content, _ = await asyncio.wait_for(self.llm.generate_response_async(prompt), timeout=30)
                logger.debug(f"[私聊][{self.private_name}]成功生成主动聊天内容: {content}")
            except asyncio.TimeoutError:
                logger.error(f"[私聊][{self.private_name}]生成主动聊天内容超时")
                return
            except Exception as llm_err:
                logger.error(f"[私聊][{self.private_name}]生成主动聊天内容失败: {str(llm_err)}")
                logger.error(traceback.format_exc())
                return

            # 清理结果
            content = content.strip()
            content = content.strip("\"'")

            if not content:
                logger.error(f"[私聊][{self.private_name}]生成的主动聊天内容为空")
                return

            # 获取聊天流
            chat_stream = await self._get_chat_stream()
            if not chat_stream:
                logger.error(f"[私聊][{self.private_name}]无法获取有效的聊天流，取消发送主动消息")
                return

            # 发送消息
            try:
                segments = Seg(type="seglist", data=[Seg(type="text", data=content)])
                logger.debug(f"[私聊][{self.private_name}]准备发送主动聊天消息: {content}")
                await self.message_sender.send_message(
                    chat_stream=chat_stream, segments=segments, reply_to_message=None, content=content
                )
                logger.info(f"[私聊][{self.private_name}]成功主动发起聊天: {content}")
            except Exception as e:
                logger.error(f"[私聊][{self.private_name}]发送主动聊天消息失败: {str(e)}")
                logger.error(traceback.format_exc())

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]主动发起聊天过程中发生未预期的错误: {str(e)}")
            logger.error(traceback.format_exc())
