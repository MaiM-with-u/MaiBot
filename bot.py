import asyncio
import hashlib
import os
import sys

# import shutil
from pathlib import Path
import time
import platform
import traceback
import signal
from dotenv import load_dotenv
from src.common.logger_manager import get_logger

# from src.common.logger import LogConfig, CONFIRM_STYLE_CONFIG
from src.common.crash_logger import install_crash_handler
from src.main import MainSystem
from rich.traceback import install

from asyncio import CancelledError
# from src.manager.async_task_manager import async_task_manager

install(extra_lines=3)

# 设置工作目录为脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
print(f"已设置工作目录为: {script_dir}")


logger = get_logger("main")
confirm_logger = get_logger("confirm")
# 获取没有加载env时的环境变量
env_mask = {key: os.getenv(key) for key in os.environ}

uvicorn_server = None
driver = None
app = None
loop = None

shutdown_requested = False  # 新增全局变量


async def request_shutdown() -> bool:
    """请求关闭程序"""
    global shutdown_requested
    if shutdown_requested:
        return True
    shutdown_requested = True
    try:
        if loop and not loop.is_closed():
            try:
                await graceful_shutdown()
            except Exception as ge:  # 捕捉优雅关闭时可能发生的错误
                logger.error(f"优雅关闭时发生错误: {ge}")
                return False
        return True
    except Exception as e:
        logger.error(f"请求关闭程序时发生错误: {e}")
        return False


def easter_egg():
    # 彩蛋
    from colorama import init, Fore

    init()
    text = "多年以后，面对AI行刑队，张三将会回想起他2023年在会议上讨论人工智能的那个下午"
    rainbow_colors = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]
    rainbow_text = ""
    for i, char in enumerate(text):
        rainbow_text += rainbow_colors[i % len(rainbow_colors)] + char
    print(rainbow_text)


# def init_config():
#     # 初次启动检测
#     if not os.path.exists("config/bot_config.toml"):
#         logger.warning("检测到bot_config.toml不存在，正在从模板复制")

#         # 检查config目录是否存在
#         if not os.path.exists("config"):
#             os.makedirs("config")
#             logger.info("创建config目录")

#         shutil.copy("template/bot_config_template.toml", "config/bot_config.toml")
#         logger.info("复制完成，请修改config/bot_config.toml和.env中的配置后重新启动")
#     if not os.path.exists("config/lpmm_config.toml"):
#         logger.warning("检测到lpmm_config.toml不存在，正在从模板复制")

#         # 检查config目录是否存在
#         if not os.path.exists("config"):
#             os.makedirs("config")
#             logger.info("创建config目录")

#         shutil.copy("template/lpmm_config_template.toml", "config/lpmm_config.toml")
#         logger.info("复制完成，请修改config/lpmm_config.toml和.env中的配置后重新启动")


# def init_env():
#     # 检测.env文件是否存在
#     if not os.path.exists(".env"):
#         logger.error("检测到.env文件不存在")
#         shutil.copy("template/template.env", "./.env")
#         logger.info("已从template/template.env复制创建.env，请修改配置后重新启动")


def load_env():
    # 直接加载生产环境变量配置
    if os.path.exists(".env"):
        load_dotenv(".env", override=True)
        logger.success("成功加载环境变量配置")
    else:
        logger.error("未找到.env文件，请确保文件存在")
        raise FileNotFoundError("未找到.env文件，请确保文件存在")


def scan_provider(env_config: dict):
    provider = {}

    # 利用未初始化 env 时获取的 env_mask 来对新的环境变量集去重
    # 避免 GPG_KEY 这样的变量干扰检查
    env_config = dict(filter(lambda item: item[0] not in env_mask, env_config.items()))

    # 遍历 env_config 的所有键
    for key in env_config:
        # 检查键是否符合 {provider}_BASE_URL 或 {provider}_KEY 的格式
        if key.endswith("_BASE_URL") or key.endswith("_KEY"):
            # 提取 provider 名称
            provider_name = key.split("_", 1)[0]  # 从左分割一次，取第一部分

            # 初始化 provider 的字典（如果尚未初始化）
            if provider_name not in provider:
                provider[provider_name] = {"url": None, "key": None}

            # 根据键的类型填充 url 或 key
            if key.endswith("_BASE_URL"):
                provider[provider_name]["url"] = env_config[key]
            elif key.endswith("_KEY"):
                provider[provider_name]["key"] = env_config[key]

    # 检查每个 provider 是否同时存在 url 和 key
    for provider_name, config in provider.items():
        if config["url"] is None or config["key"] is None:
            logger.error(f"provider 内容：{config}\nenv_config 内容：{env_config}")
            raise ValueError(f"请检查 '{provider_name}' 提供商配置是否丢失 BASE_URL 或 KEY 环境变量")


async def graceful_shutdown():
    try:
        logger.info("正在优雅关闭麦麦...")
        # await async_task_manager.stop_and_wait_all_tasks()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"麦麦关闭失败: {e}")


def check_eula():
    eula_confirm_file = Path("eula.confirmed")
    privacy_confirm_file = Path("privacy.confirmed")
    eula_file = Path("EULA.md")
    privacy_file = Path("PRIVACY.md")

    eula_updated = True
    privacy_updated = True

    eula_confirmed = False
    privacy_confirmed = False

    # 首先计算当前EULA文件的哈希值
    if eula_file.exists():
        with open(eula_file, "r", encoding="utf-8") as f:
            eula_content = f.read()
        eula_new_hash = hashlib.md5(eula_content.encode("utf-8")).hexdigest()
    else:
        logger.error("EULA.md 文件不存在")
        raise FileNotFoundError("EULA.md 文件不存在")

    # 首先计算当前隐私条款文件的哈希值
    if privacy_file.exists():
        with open(privacy_file, "r", encoding="utf-8") as f:
            privacy_content = f.read()
        privacy_new_hash = hashlib.md5(privacy_content.encode("utf-8")).hexdigest()
    else:
        logger.error("PRIVACY.md 文件不存在")
        raise FileNotFoundError("PRIVACY.md 文件不存在")

    # 检查EULA确认文件是否存在
    if eula_confirm_file.exists():
        with open(eula_confirm_file, "r", encoding="utf-8") as f:
            confirmed_content = f.read()
        if eula_new_hash == confirmed_content:
            eula_confirmed = True
            eula_updated = False
    if eula_new_hash == os.getenv("EULA_AGREE"):
        eula_confirmed = True
        eula_updated = False

    # 检查隐私条款确认文件是否存在
    if privacy_confirm_file.exists():
        with open(privacy_confirm_file, "r", encoding="utf-8") as f:
            confirmed_content = f.read()
        if privacy_new_hash == confirmed_content:
            privacy_confirmed = True
            privacy_updated = False
    if privacy_new_hash == os.getenv("PRIVACY_AGREE"):
        privacy_confirmed = True
        privacy_updated = False

    # 如果EULA或隐私条款有更新，提示用户重新确认
    if eula_updated or privacy_updated:
        confirm_logger.critical("EULA或隐私条款内容已更新，请在阅读后重新确认，继续运行视为同意更新后的以上两款协议")
        confirm_logger.critical(
            f'输入"同意"或"confirmed"或设置环境变量"EULA_AGREE={eula_new_hash}"和"PRIVACY_AGREE={privacy_new_hash}"继续运行'
        )
        while True:
            user_input = input().strip().lower()
            if user_input in ["同意", "confirmed"]:
                # print("确认成功，继续运行")
                # print(f"确认成功，继续运行{eula_updated} {privacy_updated}")
                if eula_updated:
                    logger.info(f"更新EULA确认文件{eula_new_hash}")
                    eula_confirm_file.write_text(eula_new_hash, encoding="utf-8")
                if privacy_updated:
                    logger.info(f"更新隐私条款确认文件{privacy_new_hash}")
                    privacy_confirm_file.write_text(privacy_new_hash, encoding="utf-8")
                break
            else:
                confirm_logger.critical('请输入"同意"或"confirmed"以继续运行')
        return
    elif eula_confirmed and privacy_confirmed:
        return


def raw_main():
    # 利用 TZ 环境变量设定程序工作的时区
    if platform.system().lower() != "windows":
        time.tzset()

    # 安装崩溃日志处理器
    install_crash_handler()

    check_eula()
    print("检查EULA和隐私条款完成")
    easter_egg()
    # init_config()
    # init_env()
    load_env()

    env_config = {key: os.getenv(key) for key in os.environ}
    scan_provider(env_config)

    # 返回MainSystem实例
    return MainSystem()


def signal_handler(sig, frame):
    """信号处理函数，捕获SIGINT和SIGTERM信号"""
    loop.create_task(request_shutdown())


if __name__ == "__main__":
    exit_code = 0  # 用于记录程序最终的退出状态
    try:
        # 获取MainSystem实例
        main_system = raw_main()

        # 创建事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 新增：设置信号处理
        if platform.system().lower() != "windows":
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

        try:
            # 执行初始化和任务调度
            loop.run_until_complete(main_system.initialize())
            loop.run_until_complete(main_system.schedule_tasks())
            loop.run_forever()
        except KeyboardInterrupt:
            logger.warning("收到中断信号，正在优雅关闭...")
            try:
                loop.run_until_complete(request_shutdown())
            except CancelledError as e:
                logger.error(f"优雅关闭时发生错误: {e}")
        # 新增：检测外部请求关闭

        # except Exception as e: # 将主异常捕获移到外层 try...except
        #     logger.error(f"事件循环内发生错误: {str(e)} {str(traceback.format_exc())}")
        #     exit_code = 1
        # finally: # finally 块移到最外层，确保 loop 关闭和暂停总是执行
        #     if loop and not loop.is_closed():
        #         loop.close()
        #     # 在这里添加 input() 来暂停
        #     input("按 Enter 键退出...") # <--- 添加这行
        #     sys.exit(exit_code) # <--- 使用记录的退出码

    except Exception as e:
        logger.error(f"主程序发生异常: {str(e)} {str(traceback.format_exc())}")
        exit_code = 1  # 标记发生错误
    finally:
        # 确保 loop 在任何情况下都尝试关闭（如果存在且未关闭）
        if "loop" in locals() and loop and not loop.is_closed():
            loop.close()
            logger.info("事件循环已关闭")
        # 在程序退出前暂停，让你有机会看到输出
        # input("按 Enter 键退出...")  # <--- 添加这行
        sys.exit(exit_code)  # <--- 使用记录的退出码
