import asyncio
import threading
import random
import time
import json
import re
from typing import Dict, Optional, List, Any
from pymongo.errors import OperationFailure, DuplicateKeyError
from src.common.logger_manager import get_logger
from src.common.database import db
from src.config.config import global_config
from src.chat.models.utils_model import LLMRequest
from .nickname_db import NicknameDB
from .nickname_mapper import _build_mapping_prompt
from .nickname_utils import select_nicknames_for_prompt, format_nickname_prompt_injection
from src.chat.person_info.person_info import person_info_manager
from src.chat.person_info.relationship_manager import relationship_manager
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.message_receive.message import MessageRecv
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat

logger = get_logger("NicknameManager")
logger_helper = get_logger("AsyncLoopHelper")  # 为辅助函数创建单独的 logger


def run_async_loop(loop: asyncio.AbstractEventLoop, coro):
    """
    运行给定的协程直到完成，并确保循环最终关闭。

    Args:
        loop: 要使用的 asyncio 事件循环。
        coro: 要在循环中运行的主协程。
    """
    try:
        logger_helper.debug(f"Running coroutine in loop {id(loop)}...")
        result = loop.run_until_complete(coro)
        logger_helper.debug(f"Coroutine completed in loop {id(loop)}.")
        return result
    except asyncio.CancelledError:
        logger_helper.info(f"Coroutine in loop {id(loop)} was cancelled.")
        # 取消是预期行为，不视为错误
    except Exception as e:
        logger_helper.error(f"Error in async loop {id(loop)}: {e}", exc_info=True)
    finally:
        try:
            # 1. 取消所有剩余任务
            all_tasks = asyncio.all_tasks(loop)
            current_task = asyncio.current_task(loop)
            tasks_to_cancel = [
                task for task in all_tasks if task is not current_task
            ]  # 避免取消 run_until_complete 本身
            if tasks_to_cancel:
                logger_helper.info(f"Cancelling {len(tasks_to_cancel)} outstanding tasks in loop {id(loop)}...")
                for task in tasks_to_cancel:
                    task.cancel()
                # 等待取消完成
                loop.run_until_complete(asyncio.gather(*tasks_to_cancel, return_exceptions=True))
                logger_helper.info(f"Outstanding tasks cancelled in loop {id(loop)}.")

            # 2. 停止循环 (如果仍在运行)
            if loop.is_running():
                loop.stop()
                logger_helper.info(f"Asyncio loop {id(loop)} stopped.")

            # 3. 关闭循环 (如果未关闭)
            if not loop.is_closed():
                # 在关闭前再运行一次以处理挂起的关闭回调
                loop.run_until_complete(loop.shutdown_asyncgens())  # 关闭异步生成器
                loop.close()
                logger_helper.info(f"Asyncio loop {id(loop)} closed.")
        except Exception as close_err:
            logger_helper.error(f"Error during asyncio loop cleanup for loop {id(loop)}: {close_err}", exc_info=True)


class NicknameManager:
    """
    管理群组绰号分析、处理、存储和使用的单例类。
    封装了 LLM 调用、后台处理线程和数据库交互。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    logger.info("正在创建 NicknameManager 单例实例...")
                    cls._instance = super(NicknameManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """
        初始化 NicknameManager。
        使用锁和标志确保实际初始化只执行一次。
        """
        if hasattr(self, "_initialized") and self._initialized:
            return

        with self._lock:
            if hasattr(self, "_initialized") and self._initialized:
                return

            logger.info("正在初始化 NicknameManager 组件...")
            self.config = global_config
            self.is_enabled = self.config.enable_nickname_mapping

            # 数据库处理器
            person_info_collection = getattr(db, "person_info", None)
            self.db_handler = NicknameDB(person_info_collection)
            if not self.db_handler.is_available():
                logger.error("数据库处理器初始化失败，NicknameManager 功能受限。")
                self.is_enabled = False

            # LLM 映射器
            self.llm_mapper: Optional[LLMRequest] = None
            if self.is_enabled:
                try:
                    model_config = self.config.llm_nickname_mapping
                    if model_config and model_config.get("name"):
                        self.llm_mapper = LLMRequest(
                            model=model_config,
                            temperature=model_config.get("temp", 0.5),
                            max_tokens=model_config.get("max_tokens", 256),
                            request_type="nickname_mapping",
                        )
                        logger.info("绰号映射 LLM 映射器初始化成功。")
                    else:
                        logger.warning("绰号映射 LLM 配置无效或缺失 'name'，功能禁用。")
                        self.is_enabled = False
                except KeyError as ke:
                    logger.error(f"初始化绰号映射 LLM 时缺少配置项: {ke}，功能禁用。", exc_info=True)
                    self.llm_mapper = None
                    self.is_enabled = False
                except Exception as e:
                    logger.error(f"初始化绰号映射 LLM 映射器失败: {e}，功能禁用。", exc_info=True)
                    self.llm_mapper = None
                    self.is_enabled = False

            # 队列和线程
            self.queue_max_size = getattr(self.config, "nickname_queue_max_size", 100)
            # 使用 asyncio.Queue
            self.nickname_queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_max_size)
            self._stop_event = threading.Event()  # stop_event 仍然使用 threading.Event，因为它是由另一个线程设置的
            self._nickname_thread: Optional[threading.Thread] = None
            self.sleep_interval = getattr(self.config, "nickname_process_sleep_interval", 5)  # 超时时间

            self._initialized = True
            logger.info("NicknameManager 初始化完成。")

    def start_processor(self):
        """启动后台处理线程（如果已启用且未运行）。"""
        if not self.is_enabled:
            logger.info("绰号处理功能已禁用，处理器未启动。")
            return
        if global_config.max_nicknames_in_prompt == 0:  # 考虑有神秘的用户输入为0的可能性
            logger.error("[错误] 绰号注入数量不合适，绰号处理功能已禁用！")
            return

        if self._nickname_thread is None or not self._nickname_thread.is_alive():
            logger.info("正在启动绰号处理器线程...")
            self._stop_event.clear()
            self._nickname_thread = threading.Thread(
                target=self._run_processor_in_thread,  # 线程目标函数不变
                daemon=True,
            )
            self._nickname_thread.start()
            logger.info(f"绰号处理器线程已启动 (ID: {self._nickname_thread.ident})")
        else:
            logger.warning("绰号处理器线程已在运行中。")

    def stop_processor(self):
        """停止后台处理线程。"""
        if self._nickname_thread and self._nickname_thread.is_alive():
            logger.info("正在停止绰号处理器线程...")
            self._stop_event.set()  # 设置停止事件，_processing_loop 会检测到
            try:
                # 不需要清空 asyncio.Queue，让循环自然结束或被取消
                # self.empty_queue(self.nickname_queue)
                self._nickname_thread.join(timeout=10)  # 等待线程结束
                if self._nickname_thread.is_alive():
                    logger.warning("绰号处理器线程在超时后仍未停止。")
            except Exception as e:
                logger.error(f"停止绰号处理器线程时出错: {e}", exc_info=True)
            finally:
                if self._nickname_thread and not self._nickname_thread.is_alive():
                    logger.info("绰号处理器线程已成功停止。")
                self._nickname_thread = None
        else:
            logger.info("绰号处理器线程未在运行或已被清理。")

    # def empty_queue(self, q: asyncio.Queue):
    #     while not q.empty():
    #         # Depending on your program, you may want to
    #         # catch QueueEmpty
    #         q.get_nowait()
    #         q.task_done()

    async def trigger_nickname_analysis(
        self,
        anchor_message: MessageRecv,
        bot_reply: List[str],
        chat_stream: Optional[ChatStream] = None,
    ):
        """
        准备数据并将其排队等待绰号分析（如果满足条件）。
        (现在调用异步的 _add_to_queue)
        """
        if not self.is_enabled:
            return

        if random.random() < global_config.nickname_analysis_probability:
            logger.debug("跳过绰号分析：随机概率未命中。")
            return

        current_chat_stream = chat_stream or anchor_message.chat_stream
        if not current_chat_stream or not current_chat_stream.group_info:
            logger.debug("跳过绰号分析：非群聊或无效的聊天流。")
            return

        log_prefix = f"[{current_chat_stream.stream_id}]"
        try:
            # 1. 获取历史记录
            history_limit = getattr(self.config, "nickname_analysis_history_limit", 30)
            history_messages = get_raw_msg_before_timestamp_with_chat(
                chat_id=current_chat_stream.stream_id,
                timestamp=time.time(),
                limit=history_limit,
            )
            # 格式化历史记录
            chat_history_str = await build_readable_messages(
                messages=history_messages,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
                truncate=False,
            )
            # 2. 获取 Bot 回复
            bot_reply_str = " ".join(bot_reply) if bot_reply else ""
            # 3. 获取群组和平台信息
            group_id = str(current_chat_stream.group_info.group_id)
            platform = current_chat_stream.platform
            # 4. 构建用户 ID 到名称的映射 (user_name_map)
            user_ids_in_history = {
                str(msg["user_info"]["user_id"]) for msg in history_messages if msg.get("user_info", {}).get("user_id")
            }
            user_name_map = {}
            if user_ids_in_history:
                try:
                    names_data = await relationship_manager.get_person_names_batch(platform, list(user_ids_in_history))
                except Exception as e:
                    logger.error(f"{log_prefix} 批量获取 person_name 时出错: {e}", exc_info=True)
                    names_data = {}
                for user_id in user_ids_in_history:
                    if user_id in names_data:
                        user_name_map[user_id] = names_data[user_id]
                    else:
                        latest_nickname = next(
                            (
                                m["user_info"].get("user_nickname")
                                for m in reversed(history_messages)
                                if str(m["user_info"].get("user_id")) == user_id and m["user_info"].get("user_nickname")
                            ),
                            None,
                        )
                        user_name_map[user_id] = (
                            latest_nickname or f"{global_config.BOT_NICKNAME}"
                            if user_id == global_config.BOT_QQ
                            else "未知"
                        )

            item = (chat_history_str, bot_reply_str, platform, group_id, user_name_map)
            await self._add_to_queue(item, platform, group_id)

        except Exception as e:
            logger.error(f"{log_prefix} 触发绰号分析时出错: {e}", exc_info=True)

    async def get_nickname_prompt_injection(self, chat_stream: ChatStream, message_list_before_now: List[Dict]) -> str:
        """
        获取并格式化用于 Prompt 注入的绰号信息字符串。
        """
        if not self.is_enabled or not chat_stream or not chat_stream.group_info:
            return ""

        log_prefix = f"[{chat_stream.stream_id}]"
        try:
            group_id = str(chat_stream.group_info.group_id)
            platform = chat_stream.platform
            user_ids_in_context = {
                str(msg["user_info"]["user_id"])
                for msg in message_list_before_now
                if msg.get("user_info", {}).get("user_id")
            }

            if not user_ids_in_context:
                recent_speakers = chat_stream.get_recent_speakers(limit=5)
                user_ids_in_context.update(str(speaker["user_id"]) for speaker in recent_speakers)

            if not user_ids_in_context:
                logger.warning(f"{log_prefix} 未找到上下文用户用于绰号注入。")
                return ""

            all_nicknames_data = await relationship_manager.get_users_group_nicknames(
                platform, list(user_ids_in_context), group_id
            )

            if all_nicknames_data:
                selected_nicknames = select_nicknames_for_prompt(all_nicknames_data)
                injection_str = format_nickname_prompt_injection(selected_nicknames)
                if injection_str:
                    logger.debug(f"{log_prefix} 生成的绰号 Prompt 注入:\n{injection_str}")
                return injection_str
            else:
                return ""

        except Exception as e:
            logger.error(f"{log_prefix} 获取绰号注入时出错: {e}", exc_info=True)
            return ""

    # 私有/内部方法

    async def _add_to_queue(self, item: tuple, platform: str, group_id: str):
        """将项目异步添加到内部处理队列 (asyncio.Queue)。"""
        try:
            # 使用 await put()，如果队列满则异步等待
            await self.nickname_queue.put(item)
            logger.debug(
                f"已将项目添加到平台 '{platform}' 群组 '{group_id}' 的绰号队列。当前大小: {self.nickname_queue.qsize()}"
            )
        except asyncio.QueueFull:
            # 理论上 await put() 不会直接抛 QueueFull，除非 maxsize=0
            # 但保留以防万一或未来修改
            logger.warning(
                f"绰号队列已满 (最大={self.queue_max_size})。平台 '{platform}' 群组 '{group_id}' 的项目被丢弃。"
            )
        except Exception as e:
            logger.error(f"将项目添加到绰号队列时出错: {e}", exc_info=True)

    async def _analyze_and_update_nicknames(self, item: tuple):
        """处理单个队列项目：调用 LLM 分析并更新数据库。"""
        if not isinstance(item, tuple) or len(item) != 5:
            logger.warning(f"从队列接收到无效项目: {type(item)}")
            return

        chat_history_str, bot_reply, platform, group_id, user_name_map = item
        # 使用 asyncio.get_running_loop().call_soon(threading.get_ident) 可能不准确，线程ID是同步概念
        # 可以考虑移除线程ID日志或寻找异步安全的获取标识符的方式
        log_prefix = f"[{platform}:{group_id}]"  # 简化日志前缀
        logger.debug(f"{log_prefix} 开始处理绰号分析任务...")

        if not self.llm_mapper:
            logger.error(f"{log_prefix} LLM 映射器不可用，无法执行分析。")
            return
        if not self.db_handler.is_available():
            logger.error(f"{log_prefix} 数据库处理器不可用，无法更新计数。")
            return

        # 1. 调用 LLM 分析 (内部逻辑不变)
        analysis_result = await self._call_llm_for_analysis(chat_history_str, bot_reply, user_name_map)

        # 2. 如果分析成功且找到映射，则更新数据库 (内部逻辑不变)
        if analysis_result.get("is_exist") and analysis_result.get("data"):
            nickname_map_to_update = analysis_result["data"]
            logger.info(f"{log_prefix} LLM 找到绰号映射，准备更新数据库: {nickname_map_to_update}")

            for user_id_str, nickname in nickname_map_to_update.items():
                if not user_id_str or not nickname:
                    logger.warning(f"{log_prefix} 跳过无效条目: user_id='{user_id_str}', nickname='{nickname}'")
                    continue
                if not user_id_str.isdigit():
                    logger.warning(f"{log_prefix} 无效的用户ID格式 (非纯数字): '{user_id_str}'，跳过。")
                    continue
                user_id_int = int(user_id_str)

                try:
                    person_id = person_info_manager.get_person_id(platform, user_id_str)
                    if not person_id:
                        logger.error(
                            f"{log_prefix} 无法为 platform='{platform}', user_id='{user_id_str}' 生成 person_id，跳过此用户。"
                        )
                        continue
                    self.db_handler.upsert_person(person_id, user_id_int, platform)
                    self.db_handler.update_group_nickname_count(person_id, group_id, nickname)
                except (OperationFailure, DuplicateKeyError) as db_err:
                    logger.exception(
                        f"{log_prefix} 数据库操作失败 ({type(db_err).__name__}): 用户 {user_id_str}, 绰号 {nickname}. 错误: {db_err}"
                    )
                except Exception as e:
                    logger.exception(f"{log_prefix} 处理用户 {user_id_str} 的绰号 '{nickname}' 时发生意外错误：{e}")
        else:
            logger.debug(f"{log_prefix} LLM 未找到可靠的绰号映射或分析失败。")

    async def _call_llm_for_analysis(
        self,
        chat_history_str: str,
        bot_reply: str,
        user_name_map: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        内部方法：调用 LLM 分析聊天记录和 Bot 回复，提取可靠的 用户ID-绰号 映射。
        """
        if not self.llm_mapper:
            logger.error("LLM 映射器未初始化，无法执行分析。")
            return {"is_exist": False}

        prompt = _build_mapping_prompt(chat_history_str, bot_reply, user_name_map)
        logger.debug(f"构建的绰号映射 Prompt:\n{prompt}...")

        try:
            response_content, _, _ = await self.llm_mapper.generate_response(prompt)
            logger.debug(f"LLM 原始响应 (绰号映射): {response_content}")

            if not response_content:
                logger.warning("LLM 返回了空的绰号映射内容。")
                return {"is_exist": False}

            response_content = response_content.strip()
            markdown_code_regex = re.compile(r"^```(?:\w+)?\s*\n(.*?)\n\s*```$", re.DOTALL | re.IGNORECASE)
            match = markdown_code_regex.match(response_content)
            if match:
                response_content = match.group(1).strip()
            elif response_content.startswith("{") and response_content.endswith("}"):
                pass  # 可能是纯 JSON
            else:
                json_match = re.search(r"\{.*\}", response_content, re.DOTALL)
                if json_match:
                    response_content = json_match.group(0)
                else:
                    logger.warning(f"LLM 响应似乎不包含有效的 JSON 对象。响应: {response_content}")
                    return {"is_exist": False}

            result = json.loads(response_content)

            if not isinstance(result, dict):
                logger.warning(f"LLM 响应不是一个有效的 JSON 对象 (字典类型)。响应内容: {response_content}")
                return {"is_exist": False}

            is_exist = result.get("is_exist")

            if is_exist is True:
                original_data = result.get("data")
                if isinstance(original_data, dict) and original_data:
                    logger.info(f"LLM 找到的原始绰号映射: {original_data}")
                    filtered_data = self._filter_llm_results(original_data, user_name_map)
                    if not filtered_data:
                        logger.info("所有找到的绰号映射都被过滤掉了。")
                        return {"is_exist": False}
                    else:
                        logger.info(f"过滤后的绰号映射: {filtered_data}")
                        return {"is_exist": True, "data": filtered_data}
                else:
                    logger.warning(f"LLM 响应格式错误: is_exist=True 但 data 无效。原始 data: {original_data}")
                    return {"is_exist": False}
            elif is_exist is False:
                logger.info("LLM 明确指示未找到可靠的绰号映射 (is_exist=False)。")
                return {"is_exist": False}
            else:
                logger.warning(f"LLM 响应格式错误: 'is_exist' 的值 '{is_exist}' 无效。")
                return {"is_exist": False}

        except json.JSONDecodeError as json_err:
            logger.error(f"解析 LLM 响应 JSON 失败: {json_err}\n原始响应: {response_content}")
            return {"is_exist": False}
        except Exception as e:
            logger.error(f"绰号映射 LLM 调用或处理过程中发生意外错误: {e}", exc_info=True)
            return {"is_exist": False}

    def _filter_llm_results(self, original_data: Dict[str, str], user_name_map: Dict[str, str]) -> Dict[str, str]:
        """过滤 LLM 返回的绰号映射结果。"""
        filtered_data = {}
        bot_qq_str = str(self.config.BOT_QQ) if hasattr(self.config, "BOT_QQ") else None

        for user_id, nickname in original_data.items():
            if not isinstance(user_id, str):
                logger.warning(f"过滤掉非字符串 user_id: {user_id}")
                continue
            if bot_qq_str and user_id == bot_qq_str:
                logger.debug(f"过滤掉机器人自身的映射: ID {user_id}")
                continue
            if not nickname or nickname.isspace():
                logger.debug(f"过滤掉用户 {user_id} 的空绰号。")
                continue
            # person_name = user_name_map.get(user_id)
            # if person_name and person_name == nickname:
            #     logger.debug(f"过滤掉用户 {user_id} 的映射: 绰号 '{nickname}' 与其名称 '{person_name}' 相同。")
            #     continue
            filtered_data[user_id] = nickname.strip()

        return filtered_data

    # 线程相关
    # 修改：使用 run_async_loop 辅助函数
    def _run_processor_in_thread(self):
        """后台线程入口函数，使用辅助函数管理 asyncio 事件循环。"""
        thread_id = threading.get_ident()  # 获取线程ID用于日志
        logger.info(f"绰号处理器线程启动 (线程 ID: {thread_id})...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)  # 为当前线程设置事件循环
        logger.info(f"(线程 ID: {thread_id}) Asyncio 事件循环已创建并设置。")

        # 调用辅助函数来运行主处理协程并管理循环生命周期
        run_async_loop(loop, self._processing_loop())

        logger.info(f"绰号处理器线程结束 (线程 ID: {thread_id}).")

    # 结束修改

    # 修改：使用 asyncio.Queue 和 wait_for
    async def _processing_loop(self):
        """后台线程中运行的异步处理循环 (使用 asyncio.Queue)。"""
        # 移除线程ID日志，因为它在异步上下文中不一定准确
        logger.info("绰号异步处理循环已启动。")

        while not self._stop_event.is_set():  # 仍然检查同步的停止事件
            try:
                # 使用 asyncio.wait_for 从异步队列获取项目，并设置超时
                item = await asyncio.wait_for(self.nickname_queue.get(), timeout=self.sleep_interval)

                # 处理获取到的项目 (调用异步方法)
                await self._analyze_and_update_nicknames(item)

                self.nickname_queue.task_done()  # 标记任务完成

            except asyncio.TimeoutError:
                # 等待超时，相当于之前 queue.Empty，继续循环检查停止事件
                continue
            except asyncio.CancelledError:
                # 协程被取消 (通常在 stop_processor 中发生)
                logger.info("绰号处理循环被取消。")
                break  # 退出循环
            except Exception as e:
                # 捕获处理单个项目时可能发生的其他异常
                logger.error(f"绰号处理循环出错: {e}", exc_info=True)
                # 短暂异步休眠避免快速连续失败
                await asyncio.sleep(5)

        logger.info("绰号异步处理循环已结束。")
        # 可以在这里添加清理逻辑，比如确保队列为空或处理剩余项目
        # 例如：await self.nickname_queue.join() # 等待所有任务完成 (如果需要)

    # 结束修改


# 在模块级别创建单例实例
nickname_manager = NicknameManager()
