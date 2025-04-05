import asyncio
import time
from .plugins.utils.statistic import LLMStatistics
from .plugins.moods.moods import MoodManager
from .plugins.schedule.schedule_generator import bot_schedule
from .plugins.chat.emoji_manager import emoji_manager
from .plugins.person_info.person_info import person_info_manager
from .plugins.willing.willing_manager import willing_manager
from .plugins.chat.chat_stream import chat_manager
from .heart_flow.heartflow import heartflow
from .plugins.memory_system.Hippocampus import HippocampusManager
from .plugins.chat.message_sender import message_manager
from .plugins.storage.storage import MessageStorage
from .plugins.config.config import global_config
from .plugins.chat.bot import chat_bot
from .common.logger import get_module_logger
from .plugins.remote import heartbeat_thread  # noqa: F401


logger = get_module_logger("main")


class MainSystem:
    def __init__(self):
        self.llm_stats = LLMStatistics("llm_statistics.txt")
        self.mood_manager = MoodManager.get_instance()
        self.hippocampus_manager = HippocampusManager.get_instance()
        self._message_manager_started = False

        # 使用消息API替代直接的FastAPI实例
        from .plugins.message import global_api

        self.app = global_api

    async def initialize(self):
        """初始化系统组件"""
        logger.debug(f"正在唤醒{global_config.BOT_NICKNAME}......")

        # 其他初始化任务
        await asyncio.gather(self._init_components())

        logger.success("系统初始化完成")

    async def _init_components(self):
        """初始化其他组件"""
        init_start_time = time.time()
        # 启动LLM统计
        self.llm_stats.start()
        logger.success("LLM统计功能启动成功")

        # 初始化表情管理器
        emoji_manager.initialize()
        logger.success("表情包管理器初始化成功")

        # 启动情绪管理器
        self.mood_manager.start_mood_update(update_interval=global_config.mood_update_interval)
        logger.success("情绪管理器启动成功")

        # 检查并清除person_info冗余字段
        await person_info_manager.del_all_undefined_field()

        # 启动愿望管理器
        await willing_manager.ensure_started()

        # 启动消息处理器
        if not self._message_manager_started:
            asyncio.create_task(message_manager.start_processor())
            self._message_manager_started = True

        # 初始化聊天管理器
        await chat_manager._initialize()
        asyncio.create_task(chat_manager._auto_save_task())

        # 使用HippocampusManager初始化海马体
        self.hippocampus_manager.initialize(global_config=global_config)
        # await asyncio.sleep(0.5) #防止logger输出飞了

        # 初始化日程
        bot_schedule.initialize(
            name=global_config.BOT_NICKNAME,
            personality=global_config.PROMPT_PERSONALITY,
            behavior=global_config.PROMPT_SCHEDULE_GEN,
            interval=global_config.SCHEDULE_DOING_UPDATE_INTERVAL,
        )
        asyncio.create_task(bot_schedule.mai_schedule_start())

        # 启动FastAPI服务器
        self.app.register_message_handler(chat_bot.message_process)

        try:
            # 启动心流系统
            asyncio.create_task(heartflow.heartflow_start_working())
            logger.success("心流系统启动成功")

            init_time = int(1000 * (time.time() - init_start_time))
            logger.success(f"初始化完成，神经元放电{init_time}次")
        except Exception as e:
            logger.error(f"启动大脑和外部世界失败: {e}")
            raise

    async def schedule_tasks(self):
        """调度定时任务"""
        while True:
            tasks = [
                self.build_memory_task(),
                self.forget_memory_task(),
                self.print_mood_task(),
                self.remove_recalled_message_task(),
                emoji_manager.start_periodic_check_register(),
                # emoji_manager.start_periodic_register(),
                self.app.run(),
            ]
            await asyncio.gather(*tasks)

    async def build_memory_task(self):
        """记忆构建任务"""
        while True:
            logger.info("正在进行记忆构建")
            await HippocampusManager.get_instance().build_memory()
            await asyncio.sleep(global_config.build_memory_interval)

    async def forget_memory_task(self):
        """记忆遗忘任务"""
        while True:
            print("\033[1;32m[记忆遗忘]\033[0m 开始遗忘记忆...")
            await HippocampusManager.get_instance().forget_memory(percentage=global_config.memory_forget_percentage)
            print("\033[1;32m[记忆遗忘]\033[0m 记忆遗忘完成")
            await asyncio.sleep(global_config.forget_memory_interval)

    async def print_mood_task(self):
        """打印情绪状态"""
        while True:
            self.mood_manager.print_mood_status()
            await asyncio.sleep(30)

    async def remove_recalled_message_task(self):
        """删除撤回消息任务"""
        while True:
            try:
                storage = MessageStorage()
                await storage.remove_recalled_message(time.time())
            except Exception:
                logger.exception("删除撤回消息失败")
            await asyncio.sleep(3600)


async def main():
    """主函数"""
    system = MainSystem()
    await asyncio.gather(
        system.initialize(),
        system.schedule_tasks(),
    )


if __name__ == "__main__":
    asyncio.run(main())
