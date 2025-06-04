from src.manager.async_task_manager import AsyncTask
from src.common.logger_manager import get_logger
from src.person_info.relationship_manager import relationship_manager
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp
from src.config.config import global_config
from src.person_info.person_info import person_info_manager
from src.chat.message_receive.chat_stream import chat_manager
import time
import random
from collections import defaultdict

logger = get_logger("relation")


class ImpressionUpdateTask(AsyncTask):
    def __init__(self):
        super().__init__(
            task_name="impression_update",
            wait_before_start=10,  # 启动后等待10秒
            run_interval=600,  # 每1分钟运行一次
        )

    async def run(self):
        try:
            # 获取最近10分钟的消息
            current_time = int(time.time())
            start_time = current_time - 600  # 10分钟前

            # 获取所有消息
            messages = get_raw_msg_by_timestamp(timestamp_start=start_time, timestamp_end=current_time, limit=150)

            if not messages:
                # logger.info("没有找到需要处理的消息")
                return

            logger.info(f"获取到 {len(messages)} 条消息")

            # 按chat_id分组消息
            chat_messages = defaultdict(list)
            for msg in messages:
                chat_messages[msg["chat_id"]].append(msg)

            logger.info(f"消息按聊天分组: {len(chat_messages)} 个聊天组")

            # 处理每个聊天组
            for chat_id, msgs in chat_messages.items():
                # logger.info(f"处理聊天组 {chat_id}, 消息数: {len(msgs)}")

                # 获取chat_stream
                chat_stream = chat_manager.get_stream(chat_id)
                if not chat_stream:
                    logger.warning(f"未找到聊天组 {chat_id} 的chat_stream，跳过处理")
                    continue

                # 找到bot的消息
                bot_messages = [msg for msg in msgs if msg["user_nickname"] == global_config.bot.nickname]
                logger.debug(f"找到 {len(bot_messages)} 条bot消息")

                # 统计用户发言权重
                user_weights = defaultdict(lambda: {"weight": 0, "messages": [], "middle_time": 0})

                if not bot_messages:
                    # 如果没有bot消息，所有消息权重都为1
                    logger.info("没有找到bot消息，所有消息权重设为1")
                    for msg in msgs:
                        if msg["user_nickname"] == global_config.bot.nickname:
                            continue

                        person_id = person_info_manager.get_person_id(msg["chat_info_platform"], msg["user_id"])
                        if not person_id:
                            logger.warning(f"未找到用户 {msg['user_nickname']} 的person_id")
                            continue

                        user_weights[person_id]["weight"] += 1
                        user_weights[person_id]["messages"].append(msg)
                else:
                    # 有bot消息时的原有逻辑
                    for bot_msg in bot_messages:
                        # 获取bot消息前后的消息
                        bot_time = bot_msg["time"]
                        context_messages = [msg for msg in msgs if abs(msg["time"] - bot_time) <= 600]  # 前后10分钟
                        logger.debug(f"Bot消息 {bot_time} 的上下文消息数: {len(context_messages)}")

                        # 计算权重
                        for msg in context_messages:
                            if msg["user_nickname"] == global_config.bot.nickname:
                                continue

                            person_id = person_info_manager.get_person_id(msg["chat_info_platform"], msg["user_id"])
                            if not person_id:
                                logger.warning(f"未找到用户 {msg['user_nickname']} 的person_id")
                                continue

                            # 在bot消息附近的发言权重加倍
                            if abs(msg["time"] - bot_time) <= 120:  # 前后2分钟
                                user_weights[person_id]["weight"] += 2
                                logger.debug(f"用户 {msg['user_nickname']} 在bot消息附近发言，权重+2")
                            else:
                                user_weights[person_id]["weight"] += 1
                                logger.debug(f"用户 {msg['user_nickname']} 发言，权重+1")

                            user_weights[person_id]["messages"].append(msg)

                # 计算每个用户的中间时间
                for _, data in user_weights.items():
                    if data["messages"]:
                        sorted_messages = sorted(data["messages"], key=lambda x: x["time"])
                        middle_index = len(sorted_messages) // 2
                        data["middle_time"] = sorted_messages[middle_index]["time"]
                        logger.debug(f"用户 {sorted_messages[0]['user_nickname']} 中间时间: {data['middle_time']}")

                # 按权重排序
                sorted_users = sorted(user_weights.items(), key=lambda x: x[1]["weight"], reverse=True)

                logger.debug(
                    f"用户权重排序: {[(msg[1]['messages'][0]['user_nickname'], msg[1]['weight']) for msg in sorted_users]}"
                )

                # 随机选择三个用户
                selected_users = []
                if len(sorted_users) > 3:
                    # 使用权重作为概率进行随机选择
                    weights = [user[1]["weight"] for user in sorted_users]
                    selected_indices = random.choices(range(len(sorted_users)), weights=weights, k=3)
                    selected_users = [sorted_users[i] for i in selected_indices]
                    logger.info(
                        f"开始进一步了解这些用户: {[msg[1]['messages'][0]['user_nickname'] for msg in selected_users]}"
                    )
                else:
                    selected_users = sorted_users
                    logger.info(
                        f"开始进一步了解用户: {[msg[1]['messages'][0]['user_nickname'] for msg in selected_users]}"
                    )

                # 更新选中用户的印象
                for person_id, data in selected_users:
                    user_nickname = data["messages"][0]["user_nickname"]
                    logger.info(f"开始更新用户 {user_nickname} 的印象")
                    await relationship_manager.update_person_impression(
                        person_id=person_id, chat_id=chat_id, reason="", timestamp=data["middle_time"]
                    )

            logger.debug("印象更新任务执行完成")

        except Exception as e:
            logger.exception(f"更新印象任务失败: {str(e)}")


# 创建任务实例
impression_update_task = ImpressionUpdateTask()
