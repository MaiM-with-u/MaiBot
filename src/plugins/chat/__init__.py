import asyncio
import time

from nonebot import get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, NoticeEvent
from nonebot.typing import T_State

from ..moods.moods import MoodManager  # 导入情绪管理器
from ..schedule.schedule_generator import bot_schedule
from ..utils.statistic import LLMStatistics
from .bot import chat_bot
from .config import global_config
from .emoji_manager import emoji_manager
from .relationship_manager import relationship_manager
from ..willing.willing_manager import willing_manager
from .chat_stream import chat_manager
from ..memory_system.memory import hippocampus
from .message_sender import message_manager, message_sender
from .storage import MessageStorage
from src.common.logger import get_module_logger
from src.think_flow_demo.outer_world import outer_world
from src.think_flow_demo.heartflow import subheartflow_manager

logger = get_module_logger("chat_init")

# 创建LLM统计实例
llm_stats = LLMStatistics("llm_statistics.txt")

# 添加标志变量
_message_manager_started = False

# 获取驱动器
driver = get_driver()
config = driver.config

# 初始化表情管理器
emoji_manager.initialize()
logger.success("--------------------------------")
logger.success(f"正在唤醒{global_config.BOT_NICKNAME}......使用版本：{global_config.MAI_VERSION}")
logger.success("--------------------------------")
# 注册消息处理器
msg_in = on_message(priority=5)
# 注册和bot相关的通知处理器
notice_matcher = on_notice(priority=1)
# 创建定时任务
scheduler = require("nonebot_plugin_apscheduler").scheduler


async def start_think_flow():
    """启动外部世界"""
    try:
        outer_world_task = asyncio.create_task(outer_world.open_eyes())
        logger.success("大脑和外部世界启动成功")
        # 启动心流系统
        heartflow_task = asyncio.create_task(subheartflow_manager.heartflow_start_working())
        logger.success("心流系统启动成功")  
        return outer_world_task, heartflow_task
    except Exception as e:
        logger.error(f"启动大脑和外部世界失败: {e}")
        raise


@driver.on_startup
async def start_background_tasks():
    """启动后台任务"""
    # 启动LLM统计
    llm_stats.start()
    logger.success("LLM统计功能启动成功")

    # 初始化并启动情绪管理器
    mood_manager = MoodManager.get_instance()
    mood_manager.start_mood_update(update_interval=global_config.mood_update_interval)
    logger.success("情绪管理器启动成功")

    # 启动大脑和外部世界
    if global_config.enable_think_flow:
        logger.success("启动测试功能：心流系统")
        await start_think_flow()

    # 只启动表情包管理任务
    asyncio.create_task(emoji_manager.start_periodic_check())
    await bot_schedule.initialize()
    bot_schedule.print_schedule()


@driver.on_startup
async def init_relationships():
    """在 NoneBot2 启动时初始化关系管理器"""
    logger.debug("正在加载用户关系数据...")
    await relationship_manager.load_all_relationships()
    asyncio.create_task(relationship_manager._start_relationship_manager())


@driver.on_bot_connect
async def _(bot: Bot):
    """Bot连接成功时的处理"""
    global _message_manager_started
    logger.debug(f"-----------{global_config.BOT_NICKNAME}成功连接！-----------")
    await willing_manager.ensure_started()

    message_sender.set_bot(bot)
    logger.success("-----------消息发送器已启动！-----------")

    if not _message_manager_started:
        asyncio.create_task(message_manager.start_processor())
        _message_manager_started = True
        logger.success("-----------消息处理器已启动！-----------")

    asyncio.create_task(emoji_manager._periodic_scan())
    logger.success("-----------开始偷表情包！-----------")
    asyncio.create_task(chat_manager._initialize())
    asyncio.create_task(chat_manager._auto_save_task())


@msg_in.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    # 处理合并转发消息
    if "forward" in event.message:
        await chat_bot.handle_forward_message(event, bot)
    else:
        await chat_bot.handle_message(event, bot)


@notice_matcher.handle()
async def _(bot: Bot, event: NoticeEvent, state: T_State):
    logger.debug(f"收到通知：{event}")
    await chat_bot.handle_notice(event, bot)


# 添加build_memory定时任务
@scheduler.scheduled_job("interval", seconds=global_config.build_memory_interval, id="build_memory")
async def build_memory_task():
    """每build_memory_interval秒执行一次记忆构建"""
    await hippocampus.operation_build_memory()


@scheduler.scheduled_job("interval", seconds=global_config.forget_memory_interval, id="forget_memory")
async def forget_memory_task():
    """每30秒执行一次记忆构建"""
    print("\033[1;32m[记忆遗忘]\033[0m 开始遗忘记忆...")
    await hippocampus.operation_forget_topic(percentage=global_config.memory_forget_percentage)
    print("\033[1;32m[记忆遗忘]\033[0m 记忆遗忘完成")


@scheduler.scheduled_job("interval", seconds=global_config.build_memory_interval + 10, id="merge_memory")
async def merge_memory_task():
    """每30秒执行一次记忆构建"""
    # print("\033[1;32m[记忆整合]\033[0m 开始整合")
    # await hippocampus.operation_merge_memory(percentage=0.1)
    # print("\033[1;32m[记忆整合]\033[0m 记忆整合完成")


@scheduler.scheduled_job("interval", seconds=15, id="print_mood")
async def print_mood_task():
    """每30秒打印一次情绪状态"""
    mood_manager = MoodManager.get_instance()
    mood_manager.print_mood_status()


@scheduler.scheduled_job("interval", seconds=7200, id="generate_schedule")
async def generate_schedule_task():
    """每2小时尝试生成一次日程"""
    logger.debug("尝试生成日程")
    await bot_schedule.initialize()
    if not bot_schedule.enable_output:
        bot_schedule.print_schedule()


@scheduler.scheduled_job("interval", seconds=3600, id="remove_recalled_message")
async def remove_recalled_message() -> None:
    """删除撤回消息"""
    try:
        storage = MessageStorage()
        await storage.remove_recalled_message(time.time())
    except Exception:
        logger.exception("删除撤回消息失败")
