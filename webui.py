import warnings
import gradio as gr
import os
import toml
import signal
import sys
import requests
import socket
try:
    from src.common.logger import get_module_logger

    logger = get_module_logger("webui")
except ImportError:
    from loguru import logger

    # 检查并创建日志目录
    log_dir = "logs/webui"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    # 配置控制台输出格式
    logger.remove()  # 移除默认的处理器
    logger.add(sys.stderr, format="{time:MM-DD HH:mm} | webui | {message}")  # 添加控制台输出
    logger.add("logs/webui/{time:YYYY-MM-DD}.log", rotation="00:00", format="{time:MM-DD HH:mm} | webui | {message}")
    logger.warning("检测到src.common.logger并未导入，将使用默认loguru作为日志记录器")
    logger.warning("如果你是用的是低版本(0.5.13)麦麦，请忽略此警告")
import shutil
import ast
from packaging import version
from decimal import Decimal
# 忽略 gradio 版本警告
warnings.filterwarnings("ignore", message="IMPORTANT: You are using gradio version.*")

def signal_handler(signum, frame):
    """处理 Ctrl+C 信号"""
    logger.info("收到终止信号，正在关闭 Gradio 服务器...")
    sys.exit(0)


# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)

is_share = False
debug = False

def init_model_pricing():
    """初始化模型价格配置"""
    model_list = [
        "llm_reasoning",
        "llm_reasoning_minor",
        "llm_normal",
        "llm_topic_judge",
        "llm_summary_by_topic",
        "llm_emotion_judge",
        "vlm",
        "embedding",
        "moderation"
    ]
    
    for model in model_list:
        if model in config_data["model"]:
            # 检查是否已有pri_in和pri_out配置
            has_pri_in = "pri_in" in config_data["model"][model]
            has_pri_out = "pri_out" in config_data["model"][model]
            
            # 只在缺少配置时添加默认值
            if not has_pri_in:
                config_data["model"][model]["pri_in"] = 0
                logger.info(f"为模型 {model} 添加默认输入价格配置")
            if not has_pri_out:
                config_data["model"][model]["pri_out"] = 0
                logger.info(f"为模型 {model} 添加默认输出价格配置")

# ==============================================
# env环境配置文件读取部分
def parse_env_config(config_file):
    """
    解析配置文件并将配置项存储到相应的变量中（变量名以env_为前缀）。
    """
    env_variables = {}

    # 读取配置文件
    with open(config_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 逐行处理配置
    for line in lines:
        line = line.strip()
        # 忽略空行和注释行
        if not line or line.startswith("#"):
            continue

        # 处理行尾注释
        if "#" in line:
            line = line.split("#")[0].strip()

        # 拆分键值对
        key, value = line.split("=", 1)

        # 去掉空格并去除两端引号（如果有的话）
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # 将配置项存入以env_为前缀的变量
        env_variable = f"env_{key}"
        env_variables[env_variable] = value

        # 动态创建环境变量
        os.environ[env_variable] = value

    return env_variables


# 检查配置文件是否存在
if not os.path.exists("config/bot_config.toml"):
    logger.error("配置文件 bot_config.toml 不存在，请检查配置文件路径")
    raise FileNotFoundError("配置文件 bot_config.toml 不存在，请检查配置文件路径")
else:
    config_data = toml.load("config/bot_config.toml")
    init_model_pricing()

if not os.path.exists(".env.prod"):
    logger.error("环境配置文件 .env.prod 不存在，请检查配置文件路径")
    raise FileNotFoundError("环境配置文件 .env.prod 不存在，请检查配置文件路径")
else:
    # 载入env文件并解析
    env_config_file = ".env.prod"  # 配置文件路径
    env_config_data = parse_env_config(env_config_file)

# 增加最低支持版本
MIN_SUPPORT_VERSION = version.parse("0.0.8")
MIN_SUPPORT_MAIMAI_VERSION = version.parse("0.5.13")

if "inner" in config_data:
    CONFIG_VERSION = config_data["inner"]["version"]
    PARSED_CONFIG_VERSION = version.parse(CONFIG_VERSION)
    if PARSED_CONFIG_VERSION < MIN_SUPPORT_VERSION:
        logger.error("您的麦麦版本过低！！已经不再支持，请更新到最新版本！！")
        logger.error("最低支持的麦麦版本：" + str(MIN_SUPPORT_MAIMAI_VERSION))
        raise Exception("您的麦麦版本过低！！已经不再支持，请更新到最新版本！！")
else:
    logger.error("您的麦麦版本过低！！已经不再支持，请更新到最新版本！！")
    logger.error("最低支持的麦麦版本：" + str(MIN_SUPPORT_MAIMAI_VERSION))
    raise Exception("您的麦麦版本过低！！已经不再支持，请更新到最新版本！！")

# 添加麦麦版本

if "mai_version" in config_data:
    MAI_VERSION = version.parse(str(config_data["mai_version"]["version"]))
    logger.info("您的麦麦版本为：" + str(MAI_VERSION))
else:
    logger.info("检测到配置文件中并没有定义麦麦版本，将使用默认版本")
    MAI_VERSION = version.parse("0.5.15")
    logger.info("您的麦麦版本为：" + str(MAI_VERSION))

# 增加在线状态更新版本
HAVE_ONLINE_STATUS_VERSION = version.parse("0.0.9")
# 增加日程设置重构版本
SCHEDULE_CHANGED_VERSION = version.parse("0.0.11")

# 定义意愿模式可选项
WILLING_MODE_CHOICES = [
    "classical",
    "dynamic",
    "custom",
]


# 添加WebUI配置文件版本
WEBUI_VERSION = version.parse("0.0.11")





# env环境配置文件保存函数
def save_to_env_file(env_variables, filename=".env.prod"):
    """
    将修改后的变量保存到指定的.env文件中，并在第一次保存前备份文件（如果备份文件不存在）。
    """
    backup_filename = f"{filename}.bak"

    # 如果备份文件不存在，则备份原文件
    if not os.path.exists(backup_filename):
        if os.path.exists(filename):
            logger.info(f"{filename} 已存在，正在备份到 {backup_filename}...")
            shutil.copy(filename, backup_filename)  # 备份文件
            logger.success(f"文件已备份到 {backup_filename}")
        else:
            logger.warning(f"{filename} 不存在，无法进行备份。")

    # 保存新配置
    with open(filename, "w", encoding="utf-8") as f:
        for var, value in env_variables.items():
            f.write(f"{var[4:]}={value}\n")  # 移除env_前缀
    logger.info(f"配置已保存到 {filename}")


# 载入env文件并解析
env_config_file = ".env.prod"  # 配置文件路径
env_config_data = parse_env_config(env_config_file)
if "env_VOLCENGINE_BASE_URL" in env_config_data:
    logger.info("VOLCENGINE_BASE_URL 已存在，使用默认值")
    env_config_data["env_VOLCENGINE_BASE_URL"] = "https://ark.cn-beijing.volces.com/api/v3"
else:
    logger.info("VOLCENGINE_BASE_URL 不存在，已创建并使用默认值")
    env_config_data["env_VOLCENGINE_BASE_URL"] = "https://ark.cn-beijing.volces.com/api/v3"

if "env_VOLCENGINE_KEY" in env_config_data:
    logger.info("VOLCENGINE_KEY 已存在，保持不变")
else:
    logger.info("VOLCENGINE_KEY 不存在，已创建并使用默认值")
    env_config_data["env_VOLCENGINE_KEY"] = "volc_key"
save_to_env_file(env_config_data, env_config_file)


def parse_model_providers(env_vars):
    """
    从环境变量中解析模型提供商列表
    参数:
        env_vars: 包含环境变量的字典
    返回:
        list: 模型提供商列表
    """
    providers = []
    for key in env_vars.keys():
        if key.startswith("env_") and key.endswith("_BASE_URL"):
            # 提取中间部分作为提供商名称
            provider = key[4:-9]  # 移除"env_"前缀和"_BASE_URL"后缀
            providers.append(provider)
    return providers


def add_new_provider(provider_name, current_providers):
    """
    添加新的提供商到列表中
    参数:
        provider_name: 新的提供商名称
        current_providers: 当前的提供商列表
    返回:
        tuple: (更新后的提供商列表, 更新后的下拉列表选项)
    """
    if not provider_name or provider_name in current_providers:
        return current_providers, gr.update(choices=current_providers)

    # 添加新的提供商到环境变量中
    env_config_data[f"env_{provider_name}_BASE_URL"] = ""
    env_config_data[f"env_{provider_name}_KEY"] = ""

    # 更新提供商列表
    updated_providers = current_providers + [provider_name]

    # 保存到环境文件
    save_to_env_file(env_config_data)

    return updated_providers, gr.update(choices=updated_providers)


# 从环境变量中解析并更新提供商列表
MODEL_PROVIDER_LIST = parse_model_providers(env_config_data)

# env读取保存结束
# ==============================================

# 获取在线麦麦数量


def get_online_maimbot(url="http://hyybuth.xyz:10058/api/clients/details", timeout=10):
    """
    获取在线客户端详细信息。

    参数:
        url (str): API 请求地址，默认值为 "http://hyybuth.xyz:10058/api/clients/details"。
        timeout (int): 请求超时时间，默认值为 10 秒。

    返回:
        dict: 解析后的 JSON 数据。

    异常:
        如果请求失败或数据格式不正确，将返回 None 并记录错误信息。
    """
    try:
        response = requests.get(url, timeout=timeout)
        # 检查 HTTP 响应状态码是否为 200
        if response.status_code == 200:
            # 尝试解析 JSON 数据
            return response.json()
        else:
            logger.error(f"请求失败，状态码: {response.status_code}")
            return None
    except requests.exceptions.Timeout:
        logger.error("请求超时，请检查网络连接或增加超时时间。")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("连接错误，请检查网络或API地址是否正确。")
        return None
    except ValueError:  # 包括 json.JSONDecodeError
        logger.error("无法解析返回的JSON数据，请检查API返回内容。")
        return None


online_maimbot_data = get_online_maimbot()


# ==============================================
# env环境文件中插件修改更新函数
def add_item(new_item, current_list):
    updated_list = current_list.copy()
    if new_item.strip():
        updated_list.append(new_item.strip())
    return [
        updated_list,  # 更新State
        "\n".join(updated_list),  # 更新TextArea
        gr.update(choices=updated_list),  # 更新Dropdown
        ", ".join(updated_list),  # 更新最终结果
    ]


def delete_item(selected_item, current_list):
    updated_list = current_list.copy()
    if selected_item in updated_list:
        updated_list.remove(selected_item)
    return [updated_list, "\n".join(updated_list), gr.update(choices=updated_list), ", ".join(updated_list)]


def add_int_item(new_item, current_list):
    updated_list = current_list.copy()
    stripped_item = new_item.strip()
    if stripped_item:
        try:
            item = int(stripped_item)
            updated_list.append(item)
        except ValueError:
            pass
    return [
        updated_list,  # 更新State
        "\n".join(map(str, updated_list)),  # 更新TextArea
        gr.update(choices=updated_list),  # 更新Dropdown
        ", ".join(map(str, updated_list)),  # 更新最终结果
    ]


def delete_int_item(selected_item, current_list):
    updated_list = current_list.copy()
    if selected_item in updated_list:
        updated_list.remove(selected_item)
    return [
        updated_list,
        "\n".join(map(str, updated_list)),
        gr.update(choices=updated_list),
        ", ".join(map(str, updated_list)),
    ]


# env文件中插件值处理函数
def parse_list_str(input_str):
    """
    将形如["src2.plugins.chat"]的字符串解析为Python列表
    parse_list_str('["src2.plugins.chat"]')
    ['src2.plugins.chat']
    parse_list_str("['plugin1', 'plugin2']")
    ['plugin1', 'plugin2']
    """
    try:
        return ast.literal_eval(input_str.strip())
    except (ValueError, SyntaxError):
        # 处理不符合Python列表格式的字符串
        cleaned = input_str.strip(" []")  # 去除方括号
        return [item.strip(" '\"") for item in cleaned.split(",") if item.strip()]


def format_list_to_str(lst):
    """
    将Python列表转换为形如["src2.plugins.chat"]的字符串格式
    format_list_to_str(['src2.plugins.chat'])
    '["src2.plugins.chat"]'
    format_list_to_str([1, "two", 3.0])
    '[1, "two", 3.0]'
    """
    resarr = lst.split(", ")
    res = ""
    for items in resarr:
        temp = '"' + str(items) + '"'
        res += temp + ","

    res = res[:-1]
    return "[" + res + "]"


# env保存函数
def save_trigger(
    server_address,
    server_port,
    final_result_list,
    t_mongodb_host,
    t_mongodb_port,
    t_mongodb_database_name,
    t_console_log_level,
    t_file_log_level,
    t_default_console_log_level,
    t_default_file_log_level,
    t_api_provider,
    t_api_base_url,
    t_api_key,
):
    final_result_lists = format_list_to_str(final_result_list)
    env_config_data["env_HOST"] = server_address
    env_config_data["env_PORT"] = server_port
    env_config_data["env_PLUGINS"] = final_result_lists
    env_config_data["env_MONGODB_HOST"] = t_mongodb_host
    env_config_data["env_MONGODB_PORT"] = t_mongodb_port
    env_config_data["env_DATABASE_NAME"] = t_mongodb_database_name

    # 保存日志配置
    env_config_data["env_CONSOLE_LOG_LEVEL"] = t_console_log_level
    env_config_data["env_FILE_LOG_LEVEL"] = t_file_log_level
    env_config_data["env_DEFAULT_CONSOLE_LOG_LEVEL"] = t_default_console_log_level
    env_config_data["env_DEFAULT_FILE_LOG_LEVEL"] = t_default_file_log_level

    # 保存选中的API提供商的配置
    env_config_data[f"env_{t_api_provider}_BASE_URL"] = t_api_base_url
    env_config_data[f"env_{t_api_provider}_KEY"] = t_api_key

    save_to_env_file(env_config_data)
    logger.success("配置已保存到 .env.prod 文件中")
    return "配置已保存"


def update_api_inputs(provider):
    """
    根据选择的提供商更新Base URL和API Key输入框的值
    """
    base_url = env_config_data.get(f"env_{provider}_BASE_URL", "")
    api_key = env_config_data.get(f"env_{provider}_KEY", "")
    return base_url, api_key


# 绑定下拉列表的change事件


# ==============================================


# ==============================================
# 主要配置文件保存函数
def save_config_to_file(t_config_data):
    filename = "config/bot_config.toml"
    backup_filename = f"{filename}.bak"
    if not os.path.exists(backup_filename):
        if os.path.exists(filename):
            logger.info(f"{filename} 已存在，正在备份到 {backup_filename}...")
            shutil.copy(filename, backup_filename)  # 备份文件
            logger.success(f"文件已备份到 {backup_filename}")
        else:
            logger.warning(f"{filename} 不存在，无法进行备份。")

    with open(filename, "w", encoding="utf-8") as f:
        toml.dump(t_config_data, f)
    logger.success("配置已保存到 bot_config.toml 文件中")


def save_bot_config(t_qqbot_qq, t_nickname, t_nickname_final_result):
    config_data["bot"]["qq"] = int(t_qqbot_qq)
    config_data["bot"]["nickname"] = t_nickname
    config_data["bot"]["alias_names"] = t_nickname_final_result
    save_config_to_file(config_data)
    logger.info("Bot配置已保存")
    return "Bot配置已保存"


# 监听滑块的值变化，确保总和不超过 1，并显示警告
def adjust_personality_greater_probabilities(
    t_personality_1_probability, t_personality_2_probability, t_personality_3_probability
):
    total = (
        Decimal(str(t_personality_1_probability))
        + Decimal(str(t_personality_2_probability))
        + Decimal(str(t_personality_3_probability))
    )
    if total > Decimal("1.0"):
        warning_message = (
            f"警告: 人格1、人格2和人格3的概率总和为 {float(total):.2f}，超过了 1.0！请调整滑块使总和等于 1.0。"
        )
        return warning_message
    return ""  # 没有警告时返回空字符串


def adjust_personality_less_probabilities(
    t_personality_1_probability, t_personality_2_probability, t_personality_3_probability
):
    total = (
        Decimal(str(t_personality_1_probability))
        + Decimal(str(t_personality_2_probability))
        + Decimal(str(t_personality_3_probability))
    )
    if total < Decimal("1.0"):
        warning_message = (
            f"警告: 人格1、人格2和人格3的概率总和为 {float(total):.2f}，小于 1.0！请调整滑块使总和等于 1.0。"
        )
        return warning_message
    return ""  # 没有警告时返回空字符串


def adjust_model_greater_probabilities(t_model_1_probability, t_model_2_probability, t_model_3_probability):
    total = (
        Decimal(str(t_model_1_probability)) + Decimal(str(t_model_2_probability)) + Decimal(str(t_model_3_probability))
    )
    if total > Decimal("1.0"):
        warning_message = (
            f"警告: 选择模型1、模型2和模型3的概率总和为 {float(total):.2f}，超过了 1.0！请调整滑块使总和等于 1.0。"
        )
        return warning_message
    return ""  # 没有警告时返回空字符串


def adjust_model_less_probabilities(t_model_1_probability, t_model_2_probability, t_model_3_probability):
    total = (
        Decimal(str(t_model_1_probability)) + Decimal(str(t_model_2_probability)) + Decimal(str(t_model_3_probability))
    )
    if total < Decimal("1.0"):
        warning_message = (
            f"警告: 选择模型1、模型2和模型3的概率总和为 {float(total):.2f}，小于了 1.0！请调整滑块使总和等于 1.0。"
        )
        return warning_message
    return ""  # 没有警告时返回空字符串


# ==============================================
# 人格保存函数
def save_personality_config(
    t_prompt_personality_1,
    t_prompt_personality_2,
    t_prompt_personality_3,
    t_enable_schedule_gen,
    t_prompt_schedule_gen,
    t_schedule_doing_update_interval,
    t_personality_1_probability,
    t_personality_2_probability,
    t_personality_3_probability,
):
    # 保存人格提示词
    config_data["personality"]["prompt_personality"][0] = t_prompt_personality_1
    config_data["personality"]["prompt_personality"][1] = t_prompt_personality_2
    config_data["personality"]["prompt_personality"][2] = t_prompt_personality_3

    # 保存日程生成部分
    if PARSED_CONFIG_VERSION >= SCHEDULE_CHANGED_VERSION:
        config_data["schedule"]["enable_schedule_gen"] = t_enable_schedule_gen
        config_data["schedule"]["prompt_schedule_gen"] = t_prompt_schedule_gen
        config_data["schedule"]["schedule_doing_update_interval"] = t_schedule_doing_update_interval
    else:
        config_data["personality"]["prompt_schedule"] = t_prompt_schedule_gen

    # 保存三个人格的概率
    config_data["personality"]["personality_1_probability"] = t_personality_1_probability
    config_data["personality"]["personality_2_probability"] = t_personality_2_probability
    config_data["personality"]["personality_3_probability"] = t_personality_3_probability

    save_config_to_file(config_data)
    logger.info("人格配置已保存到 bot_config.toml 文件中")
    return "人格配置已保存"


def save_message_and_emoji_config(
    t_min_text_length,
    t_max_context_size,
    t_emoji_chance,
    t_thinking_timeout,
    t_response_willing_amplifier,
    t_response_interested_rate_amplifier,
    t_down_frequency_rate,
    t_ban_words_final_result,
    t_ban_msgs_regex_final_result,
    t_check_interval,
    t_register_interval,
    t_auto_save,
    t_enable_check,
    t_check_prompt,
):
    if PARSED_CONFIG_VERSION < version.parse("0.0.11"):
        config_data["message"]["min_text_length"] = t_min_text_length
    config_data["message"]["max_context_size"] = t_max_context_size
    config_data["message"]["emoji_chance"] = t_emoji_chance
    config_data["message"]["thinking_timeout"] = t_thinking_timeout
    if PARSED_CONFIG_VERSION < version.parse("0.0.11"):
        config_data["message"]["response_willing_amplifier"] = t_response_willing_amplifier
        config_data["message"]["response_interested_rate_amplifier"] = t_response_interested_rate_amplifier
        config_data["message"]["down_frequency_rate"] = t_down_frequency_rate
    config_data["message"]["ban_words"] = t_ban_words_final_result
    config_data["message"]["ban_msgs_regex"] = t_ban_msgs_regex_final_result
    config_data["emoji"]["check_interval"] = t_check_interval
    config_data["emoji"]["register_interval"] = t_register_interval
    config_data["emoji"]["auto_save"] = t_auto_save
    config_data["emoji"]["enable_check"] = t_enable_check
    config_data["emoji"]["check_prompt"] = t_check_prompt
    save_config_to_file(config_data)
    logger.info("消息和表情配置已保存到 bot_config.toml 文件中")
    return "消息和表情配置已保存"

def save_willing_config(
        t_willing_mode,
        t_response_willing_amplifier,
        t_response_interested_rate_amplifier,
        t_down_frequency_rate,
        t_emoji_response_penalty,
):
    config_data["willing"]["willing_mode"] = t_willing_mode
    config_data["willing"]["response_willing_amplifier"] = t_response_willing_amplifier
    config_data["willing"]["response_interested_rate_amplifier"] = t_response_interested_rate_amplifier
    config_data["willing"]["down_frequency_rate"] = t_down_frequency_rate
    config_data["willing"]["emoji_response_penalty"] = t_emoji_response_penalty
    save_config_to_file(config_data)
    logger.info("willinng配置已保存到 bot_config.toml 文件中")
    return "willinng配置已保存"

def save_response_model_config(
    t_willing_mode,
    t_model_r1_probability,
    t_model_r2_probability,
    t_model_r3_probability,
    t_max_response_length,
    t_model1_name,
    t_model1_provider,
    t_model1_pri_in,
    t_model1_pri_out,
    t_model2_name,
    t_model2_provider,
    t_model2_pri_in,
    t_model2_pri_out,
    t_model3_name,
    t_model3_provider,
    t_model3_pri_in,
    t_model3_pri_out,
    t_emotion_model_name,
    t_emotion_model_provider,
    t_emotion_model_pri_in,
    t_emotion_model_pri_out,
    t_topic_judge_model_name,
    t_topic_judge_model_provider,
    t_topic_judge_model_pri_in,
    t_topic_judge_model_pri_out,
    t_summary_by_topic_model_name,
    t_summary_by_topic_model_provider,
    t_summary_by_topic_model_pri_in,
    t_summary_by_topic_model_pri_out,
    t_vlm_model_name,
    t_vlm_model_provider,
    t_vlm_model_pri_in,
    t_vlm_model_pri_out,
):
    if PARSED_CONFIG_VERSION >= version.parse("0.0.10"):
        config_data["willing"]["willing_mode"] = t_willing_mode
    config_data["response"]["model_r1_probability"] = t_model_r1_probability
    config_data["response"]["model_v3_probability"] = t_model_r2_probability
    config_data["response"]["model_r1_distill_probability"] = t_model_r3_probability
    if PARSED_CONFIG_VERSION <= version.parse("0.0.10"):
        config_data["response"]["max_response_length"] = t_max_response_length
    
    # 保存模型1配置
    config_data["model"]["llm_reasoning"]["name"] = t_model1_name
    config_data["model"]["llm_reasoning"]["provider"] = t_model1_provider
    config_data["model"]["llm_reasoning"]["pri_in"] = t_model1_pri_in
    config_data["model"]["llm_reasoning"]["pri_out"] = t_model1_pri_out
    
    # 保存模型2配置
    config_data["model"]["llm_normal"]["name"] = t_model2_name
    config_data["model"]["llm_normal"]["provider"] = t_model2_provider
    config_data["model"]["llm_normal"]["pri_in"] = t_model2_pri_in
    config_data["model"]["llm_normal"]["pri_out"] = t_model2_pri_out
    
    # 保存模型3配置
    config_data["model"]["llm_reasoning_minor"]["name"] = t_model3_name
    config_data["model"]["llm_reasoning_minor"]["provider"] = t_model3_provider
    config_data["model"]["llm_reasoning_minor"]["pri_in"] = t_model3_pri_in
    config_data["model"]["llm_reasoning_minor"]["pri_out"] = t_model3_pri_out
    
    # 保存情感模型配置
    config_data["model"]["llm_emotion_judge"]["name"] = t_emotion_model_name
    config_data["model"]["llm_emotion_judge"]["provider"] = t_emotion_model_provider
    config_data["model"]["llm_emotion_judge"]["pri_in"] = t_emotion_model_pri_in
    config_data["model"]["llm_emotion_judge"]["pri_out"] = t_emotion_model_pri_out
    
    # 保存主题判断模型配置
    config_data["model"]["llm_topic_judge"]["name"] = t_topic_judge_model_name
    config_data["model"]["llm_topic_judge"]["provider"] = t_topic_judge_model_provider
    config_data["model"]["llm_topic_judge"]["pri_in"] = t_topic_judge_model_pri_in
    config_data["model"]["llm_topic_judge"]["pri_out"] = t_topic_judge_model_pri_out
    
    # 保存主题总结模型配置
    config_data["model"]["llm_summary_by_topic"]["name"] = t_summary_by_topic_model_name
    config_data["model"]["llm_summary_by_topic"]["provider"] = t_summary_by_topic_model_provider
    config_data["model"]["llm_summary_by_topic"]["pri_in"] = t_summary_by_topic_model_pri_in
    config_data["model"]["llm_summary_by_topic"]["pri_out"] = t_summary_by_topic_model_pri_out
    
    # 保存识图模型配置
    config_data["model"]["vlm"]["name"] = t_vlm_model_name
    config_data["model"]["vlm"]["provider"] = t_vlm_model_provider
    config_data["model"]["vlm"]["pri_in"] = t_vlm_model_pri_in
    config_data["model"]["vlm"]["pri_out"] = t_vlm_model_pri_out
    
    save_config_to_file(config_data)
    logger.info("回复&模型设置已保存到 bot_config.toml 文件中")
    return "回复&模型设置已保存"


def save_memory_mood_config(
    t_build_memory_interval,
    t_memory_compress_rate,
    t_forget_memory_interval,
    t_memory_forget_time,
    t_memory_forget_percentage,
    t_memory_ban_words_final_result,
    t_mood_update_interval,
    t_mood_decay_rate,
    t_mood_intensity_factor,
    t_build_memory_dist1_mean,
    t_build_memory_dist1_std,
    t_build_memory_dist1_weight,
    t_build_memory_dist2_mean,
    t_build_memory_dist2_std,
    t_build_memory_dist2_weight,
):
    config_data["memory"]["build_memory_interval"] = t_build_memory_interval
    config_data["memory"]["memory_compress_rate"] = t_memory_compress_rate
    config_data["memory"]["forget_memory_interval"] = t_forget_memory_interval
    config_data["memory"]["memory_forget_time"] = t_memory_forget_time
    config_data["memory"]["memory_forget_percentage"] = t_memory_forget_percentage
    config_data["memory"]["memory_ban_words"] = t_memory_ban_words_final_result
    if PARSED_CONFIG_VERSION >= version.parse("0.0.11"):
        config_data["memory"]["build_memory_distribution"] = [
            t_build_memory_dist1_mean,
            t_build_memory_dist1_std,
            t_build_memory_dist1_weight,
            t_build_memory_dist2_mean,
            t_build_memory_dist2_std,
            t_build_memory_dist2_weight,
        ]
    config_data["mood"]["update_interval"] = t_mood_update_interval
    config_data["mood"]["decay_rate"] = t_mood_decay_rate
    config_data["mood"]["intensity_factor"] = t_mood_intensity_factor
    save_config_to_file(config_data)
    logger.info("记忆和心情设置已保存到 bot_config.toml 文件中")
    return "记忆和心情设置已保存"


def save_other_config(
    t_keywords_reaction_enabled,
    t_enable_advance_output,
    t_enable_kuuki_read,
    t_enable_debug_output,
    t_enable_friend_chat,
    t_chinese_typo_enabled,
    t_error_rate,
    t_min_freq,
    t_tone_error_rate,
    t_word_replace_rate,
    t_remote_status,
    t_enable_response_spliter,
    t_max_response_length,
    t_max_sentence_num,
):
    config_data["keywords_reaction"]["enable"] = t_keywords_reaction_enabled
    config_data["others"]["enable_advance_output"] = t_enable_advance_output
    config_data["others"]["enable_kuuki_read"] = t_enable_kuuki_read
    config_data["others"]["enable_debug_output"] = t_enable_debug_output
    config_data["others"]["enable_friend_chat"] = t_enable_friend_chat
    config_data["chinese_typo"]["enable"] = t_chinese_typo_enabled
    config_data["chinese_typo"]["error_rate"] = t_error_rate
    config_data["chinese_typo"]["min_freq"] = t_min_freq
    config_data["chinese_typo"]["tone_error_rate"] = t_tone_error_rate
    config_data["chinese_typo"]["word_replace_rate"] = t_word_replace_rate
    if PARSED_CONFIG_VERSION > HAVE_ONLINE_STATUS_VERSION:
        config_data["remote"]["enable"] = t_remote_status
    if PARSED_CONFIG_VERSION >= version.parse("0.0.11"):
        config_data["response_spliter"]["enable_response_spliter"] = t_enable_response_spliter
        config_data["response_spliter"]["response_max_length"] = t_max_response_length
        config_data["response_spliter"]["response_max_sentence_num"] = t_max_sentence_num
    save_config_to_file(config_data)
    logger.info("其他设置已保存到 bot_config.toml 文件中")
    return "其他设置已保存"


def save_group_config(
    t_talk_allowed_final_result,
    t_talk_frequency_down_final_result,
    t_ban_user_id_final_result,
):
    config_data["groups"]["talk_allowed"] = t_talk_allowed_final_result
    config_data["groups"]["talk_frequency_down"] = t_talk_frequency_down_final_result
    config_data["groups"]["ban_user_id"] = t_ban_user_id_final_result
    save_config_to_file(config_data)
    logger.info("群聊设置已保存到 bot_config.toml 文件中")
    return "群聊设置已保存"

with gr.Blocks(title="MaimBot配置文件编辑") as app:
    gr.Markdown(
        value="""
        # 欢迎使用由墨梓柒MotricSeven编写的MaimBot配置文件编辑器\n
        感谢ZureTz大佬提供的人格保存部分修复！
        """
    )
    gr.Markdown(value="---")  # 添加分割线
    gr.Markdown(value="""
        ## 注意！！！\n
        由于Gradio的限制，在保存配置文件时，请不要刷新浏览器窗口！！\n
        您的配置文件在点击保存按钮的时候就已经成功保存！！
        """)
    gr.Markdown(value="---")  # 添加分割线
    gr.Markdown(value="## 全球在线MaiMBot数量: " + str((online_maimbot_data or {}).get("online_clients", 0)))
    gr.Markdown(value="## 当前WebUI版本: " + str(WEBUI_VERSION))
    gr.Markdown(value="## 配置文件版本：" + config_data["inner"]["version"])
    gr.Markdown(value="---")  # 添加分割线
    with gr.Tabs():
        with gr.TabItem("0-环境设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        gr.Markdown(
                            value="""
                            MaimBot服务器地址，默认127.0.0.1\n
                            不熟悉配置的不要轻易改动此项！！\n
                            """
                        )
                    with gr.Row():
                        server_address = gr.Textbox(
                            label="服务器地址", value=env_config_data["env_HOST"], interactive=True
                        )
                    with gr.Row():
                        server_port = gr.Textbox(
                            label="服务器端口", value=env_config_data["env_PORT"], interactive=True
                        )
                    with gr.Row():
                        plugin_list = parse_list_str(env_config_data["env_PLUGINS"])
                        with gr.Blocks():
                            list_state = gr.State(value=plugin_list.copy())

                        with gr.Row():
                            list_display = gr.TextArea(
                                value="\n".join(plugin_list), label="插件列表", interactive=False, lines=5
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                new_item_input = gr.Textbox(label="添加新插件")
                                add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                item_to_delete = gr.Dropdown(choices=plugin_list, label="选择要删除的插件")
                            delete_btn = gr.Button("删除", scale=1)

                        final_result = gr.Text(label="修改后的列表")
                        add_btn.click(
                            add_item,
                            inputs=[new_item_input, list_state],
                            outputs=[list_state, list_display, item_to_delete, final_result],
                        )

                        delete_btn.click(
                            delete_item,
                            inputs=[item_to_delete, list_state],
                            outputs=[list_state, list_display, item_to_delete, final_result],
                        )
                    with gr.Row():
                        gr.Markdown(
                            """MongoDB设置项\n
                            保持默认即可，如果你有能力承担修改过后的后果（简称能改回来（笑））\n
                            可以对以下配置项进行修改\n
                            """
                        )
                    with gr.Row():
                        mongodb_host = gr.Textbox(
                            label="MongoDB服务器地址", value=env_config_data["env_MONGODB_HOST"], interactive=True
                        )
                    with gr.Row():
                        mongodb_port = gr.Textbox(
                            label="MongoDB服务器端口", value=env_config_data["env_MONGODB_PORT"], interactive=True
                        )
                    with gr.Row():
                        mongodb_database_name = gr.Textbox(
                            label="MongoDB数据库名称", value=env_config_data["env_DATABASE_NAME"], interactive=True
                        )
                    with gr.Row():
                        gr.Markdown(
                            """日志设置\n
                            配置日志输出级别\n
                            改完了记得保存！！！
                            """
                        )
                    with gr.Row():
                        console_log_level = gr.Dropdown(
                            choices=["INFO", "DEBUG", "WARNING", "ERROR", "SUCCESS"],
                            label="控制台日志级别",
                            value=env_config_data.get("env_CONSOLE_LOG_LEVEL", "INFO"),
                            interactive=True,
                        )
                    with gr.Row():
                        file_log_level = gr.Dropdown(
                            choices=["INFO", "DEBUG", "WARNING", "ERROR", "SUCCESS"],
                            label="文件日志级别",
                            value=env_config_data.get("env_FILE_LOG_LEVEL", "DEBUG"),
                            interactive=True,
                        )
                    with gr.Row():
                        default_console_log_level = gr.Dropdown(
                            choices=["INFO", "DEBUG", "WARNING", "ERROR", "SUCCESS", "NONE"],
                            label="默认控制台日志级别",
                            value=env_config_data.get("env_DEFAULT_CONSOLE_LOG_LEVEL", "SUCCESS"),
                            interactive=True,
                        )
                    with gr.Row():
                        default_file_log_level = gr.Dropdown(
                            choices=["INFO", "DEBUG", "WARNING", "ERROR", "SUCCESS", "NONE"],
                            label="默认文件日志级别",
                            value=env_config_data.get("env_DEFAULT_FILE_LOG_LEVEL", "DEBUG"),
                            interactive=True,
                        )
                    with gr.Row():
                        gr.Markdown(
                            """API设置\n
                            选择API提供商并配置相应的BaseURL和Key\n
                            改完了记得保存！！！
                            """
                        )
                    with gr.Row():
                        with gr.Column(scale=3):
                            new_provider_input = gr.Textbox(label="添加新提供商", placeholder="输入新提供商名称")
                        add_provider_btn = gr.Button("添加提供商", scale=1)
                    with gr.Row():
                        api_provider = gr.Dropdown(
                            choices=MODEL_PROVIDER_LIST,
                            label="选择API提供商",
                            value=MODEL_PROVIDER_LIST[0] if MODEL_PROVIDER_LIST else None,
                        )

                    with gr.Row():
                        api_base_url = gr.Textbox(
                            label="Base URL",
                            value=env_config_data.get(f"env_{MODEL_PROVIDER_LIST[0]}_BASE_URL", "")
                            if MODEL_PROVIDER_LIST
                            else "",
                            interactive=True,
                        )
                    with gr.Row():
                        api_key = gr.Textbox(
                            label="API Key",
                            value=env_config_data.get(f"env_{MODEL_PROVIDER_LIST[0]}_KEY", "")
                            if MODEL_PROVIDER_LIST
                            else "",
                            interactive=True,
                        )
                        api_provider.change(update_api_inputs, inputs=[api_provider], outputs=[api_base_url, api_key])
                    with gr.Row():
                        save_env_btn = gr.Button("保存环境配置", variant="primary")
                    with gr.Row():
                        save_env_btn.click(
                            save_trigger,
                            inputs=[
                                server_address,
                                server_port,
                                final_result,
                                mongodb_host,
                                mongodb_port,
                                mongodb_database_name,
                                console_log_level,
                                file_log_level,
                                default_console_log_level,
                                default_file_log_level,
                                api_provider,
                                api_base_url,
                                api_key,
                            ],
                            outputs=[gr.Textbox(label="保存结果", interactive=False)],
                        )

                    # 绑定添加提供商按钮的点击事件
                    add_provider_btn.click(
                        add_new_provider,
                        inputs=[new_provider_input, gr.State(value=MODEL_PROVIDER_LIST)],
                        outputs=[gr.State(value=MODEL_PROVIDER_LIST), api_provider],
                    ).then(
                        lambda x: (
                            env_config_data.get(f"env_{x}_BASE_URL", ""),
                            env_config_data.get(f"env_{x}_KEY", ""),
                        ),
                        inputs=[api_provider],
                        outputs=[api_base_url, api_key],
                    )
        with gr.TabItem("1-Bot基础设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        qqbot_qq = gr.Textbox(label="QQ机器人QQ号", value=config_data["bot"]["qq"], interactive=True)
                    with gr.Row():
                        nickname = gr.Textbox(label="昵称", value=config_data["bot"]["nickname"], interactive=True)
                    with gr.Row():
                        nickname_list = config_data["bot"]["alias_names"]
                        with gr.Blocks():
                            nickname_list_state = gr.State(value=nickname_list.copy())

                        with gr.Row():
                            nickname_list_display = gr.TextArea(
                                value="\n".join(nickname_list), label="别名列表", interactive=False, lines=5
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                nickname_new_item_input = gr.Textbox(label="添加新别名")
                                nickname_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                nickname_item_to_delete = gr.Dropdown(choices=nickname_list, label="选择要删除的别名")
                            nickname_delete_btn = gr.Button("删除", scale=1)

                        nickname_final_result = gr.Text(label="修改后的列表")
                        nickname_add_btn.click(
                            add_item,
                            inputs=[nickname_new_item_input, nickname_list_state],
                            outputs=[
                                nickname_list_state,
                                nickname_list_display,
                                nickname_item_to_delete,
                                nickname_final_result,
                            ],
                        )

                        nickname_delete_btn.click(
                            delete_item,
                            inputs=[nickname_item_to_delete, nickname_list_state],
                            outputs=[
                                nickname_list_state,
                                nickname_list_display,
                                nickname_item_to_delete,
                                nickname_final_result,
                            ],
                        )
                    gr.Button(
                        "保存Bot配置", variant="primary", elem_id="save_bot_btn", elem_classes="save_bot_btn"
                    ).click(
                        save_bot_config,
                        inputs=[qqbot_qq, nickname, nickname_list_state],
                        outputs=[gr.Textbox(label="保存Bot结果")],
                    )
        with gr.TabItem("2-人格设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        prompt_personality_1 = gr.Textbox(
                            label="人格1提示词",
                            value=config_data["personality"]["prompt_personality"][0],
                            interactive=True,
                        )
                    with gr.Row():
                        prompt_personality_2 = gr.Textbox(
                            label="人格2提示词",
                            value=config_data["personality"]["prompt_personality"][1],
                            interactive=True,
                        )
                    with gr.Row():
                        prompt_personality_3 = gr.Textbox(
                            label="人格3提示词",
                            value=config_data["personality"]["prompt_personality"][2],
                            interactive=True,
                        )
                with gr.Column(scale=3):
                    # 创建三个滑块, 代表三个人格的概率
                    personality_1_probability = gr.Slider(
                        minimum=0,
                        maximum=1,
                        step=0.01,
                        value=config_data["personality"]["personality_1_probability"],
                        label="人格1概率",
                    )
                    personality_2_probability = gr.Slider(
                        minimum=0,
                        maximum=1,
                        step=0.01,
                        value=config_data["personality"]["personality_2_probability"],
                        label="人格2概率",
                    )
                    personality_3_probability = gr.Slider(
                        minimum=0,
                        maximum=1,
                        step=0.01,
                        value=config_data["personality"]["personality_3_probability"],
                        label="人格3概率",
                    )

                    # 用于显示警告消息
                    warning_greater_text = gr.Markdown()
                    warning_less_text = gr.Markdown()

                    # 绑定滑块的值变化事件，确保总和必须等于 1.0

                    # 输入的 3 个概率
                    personality_probability_change_inputs = [
                        personality_1_probability,
                        personality_2_probability,
                        personality_3_probability,
                    ]

                    # 绑定滑块的值变化事件，确保总和不大于 1.0
                    personality_1_probability.change(
                        adjust_personality_greater_probabilities,
                        inputs=personality_probability_change_inputs,
                        outputs=[warning_greater_text],
                    )
                    personality_2_probability.change(
                        adjust_personality_greater_probabilities,
                        inputs=personality_probability_change_inputs,
                        outputs=[warning_greater_text],
                    )
                    personality_3_probability.change(
                        adjust_personality_greater_probabilities,
                        inputs=personality_probability_change_inputs,
                        outputs=[warning_greater_text],
                    )

                    # 绑定滑块的值变化事件，确保总和不小于 1.0
                    personality_1_probability.change(
                        adjust_personality_less_probabilities,
                        inputs=personality_probability_change_inputs,
                        outputs=[warning_less_text],
                    )
                    personality_2_probability.change(
                        adjust_personality_less_probabilities,
                        inputs=personality_probability_change_inputs,
                        outputs=[warning_less_text],
                    )
                    personality_3_probability.change(
                        adjust_personality_less_probabilities,
                        inputs=personality_probability_change_inputs,
                        outputs=[warning_less_text],
                    )
            with gr.Row():
                gr.Markdown("---")
            with gr.Row():
                gr.Markdown("麦麦提示词设置")
            if PARSED_CONFIG_VERSION >= SCHEDULE_CHANGED_VERSION:
                with gr.Row():
                    enable_schedule_gen = gr.Checkbox(value=config_data["schedule"]["enable_schedule_gen"],
                                                    label="是否开启麦麦日程生成(尚未完成)",
                                                    interactive=True
                                                    )
                with gr.Row():
                    prompt_schedule_gen = gr.Textbox(
                        label="日程生成提示词", value=config_data["schedule"]["prompt_schedule_gen"], interactive=True
                    )
                with gr.Row():
                    schedule_doing_update_interval = gr.Number(
                        value=config_data["schedule"]["schedule_doing_update_interval"],
                        label="日程表更新间隔 单位秒",
                        interactive=True
                        )
            else:
                with gr.Row():
                    prompt_schedule_gen = gr.Textbox(
                        label="日程生成提示词", value=config_data["personality"]["prompt_schedule"], interactive=True
                    )
                    enable_schedule_gen = gr.Checkbox(value=False,visible=False,interactive=False)
                    schedule_doing_update_interval = gr.Number(value=0,visible=False,interactive=False)
            with gr.Row():
                personal_save_btn = gr.Button(
                    "保存人格配置",
                    variant="primary",
                    elem_id="save_personality_btn",
                    elem_classes="save_personality_btn",
                )
            with gr.Row():
                personal_save_message = gr.Textbox(label="保存人格结果")
            personal_save_btn.click(
                save_personality_config,
                inputs=[
                    prompt_personality_1,
                    prompt_personality_2,
                    prompt_personality_3,
                    enable_schedule_gen,
                    prompt_schedule_gen,
                    schedule_doing_update_interval,
                    personality_1_probability,
                    personality_2_probability,
                    personality_3_probability,
                ],
                outputs=[personal_save_message],
            )
        with gr.TabItem("3-消息&表情包设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    if PARSED_CONFIG_VERSION < version.parse("0.0.11"):
                        with gr.Row():
                            min_text_length = gr.Number(
                                value=config_data["message"]["min_text_length"],
                                label="与麦麦聊天时麦麦只会回答文本大于等于此数的消息",
                            )
                    else:
                        min_text_length = gr.Number(visible=False,value=0,interactive=False)
                    with gr.Row():
                        max_context_size = gr.Number(
                            value=config_data["message"]["max_context_size"], label="麦麦获得的上文数量"
                        )
                    with gr.Row():
                        emoji_chance = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["message"]["emoji_chance"],
                            label="麦麦使用表情包的概率",
                        )
                    with gr.Row():
                        thinking_timeout = gr.Number(
                            value=config_data["message"]["thinking_timeout"],
                            label="麦麦正在思考时，如果超过此秒数，则停止思考",
                        )
                    if PARSED_CONFIG_VERSION < version.parse("0.0.11"):
                        with gr.Row():
                            response_willing_amplifier = gr.Number(
                                value=config_data["message"]["response_willing_amplifier"],
                                label="麦麦回复意愿放大系数，一般为1",
                            )
                        with gr.Row():
                            response_interested_rate_amplifier = gr.Number(
                                value=config_data["message"]["response_interested_rate_amplifier"],
                                label="麦麦回复兴趣度放大系数,听到记忆里的内容时放大系数",
                            )
                        with gr.Row():
                            down_frequency_rate = gr.Number(
                                value=config_data["message"]["down_frequency_rate"],
                                label="降低回复频率的群组回复意愿降低系数",
                            )
                    else:
                        response_willing_amplifier = gr.Number(visible=False,value=0,interactive=False)
                        response_interested_rate_amplifier = gr.Number(visible=False,value=0,interactive=False)
                        down_frequency_rate = gr.Number(visible=False,value=0,interactive=False)

                    with gr.Row():
                        gr.Markdown("### 违禁词列表")
                    with gr.Row():
                        ban_words_list = config_data["message"]["ban_words"]
                        with gr.Blocks():
                            ban_words_list_state = gr.State(value=ban_words_list.copy())
                        with gr.Row():
                            ban_words_list_display = gr.TextArea(
                                value="\n".join(ban_words_list), label="违禁词列表", interactive=False, lines=5
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                ban_words_new_item_input = gr.Textbox(label="添加新违禁词")
                                ban_words_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                ban_words_item_to_delete = gr.Dropdown(
                                    choices=ban_words_list, label="选择要删除的违禁词"
                                )
                                ban_words_delete_btn = gr.Button("删除", scale=1)

                        ban_words_final_result = gr.Text(label="修改后的违禁词")
                        ban_words_add_btn.click(
                            add_item,
                            inputs=[ban_words_new_item_input, ban_words_list_state],
                            outputs=[
                                ban_words_list_state,
                                ban_words_list_display,
                                ban_words_item_to_delete,
                                ban_words_final_result,
                            ],
                        )

                        ban_words_delete_btn.click(
                            delete_item,
                            inputs=[ban_words_item_to_delete, ban_words_list_state],
                            outputs=[
                                ban_words_list_state,
                                ban_words_list_display,
                                ban_words_item_to_delete,
                                ban_words_final_result,
                            ],
                        )
                    with gr.Row():
                        gr.Markdown("### 检测违禁消息正则表达式列表")
                    with gr.Row():
                        gr.Markdown(
                            """
                                 需要过滤的消息（原始消息）匹配的正则表达式，匹配到的消息将被过滤（支持CQ码），若不了解正则表达式请勿修改\n
                                "https?://[^\\s]+", # 匹配https链接\n
                                "\\d{4}-\\d{2}-\\d{2}", # 匹配日期\n
                                 "\\[CQ:at,qq=\\d+\\]" # 匹配@\n
                            """
                        )
                    with gr.Row():
                        ban_msgs_regex_list = config_data["message"]["ban_msgs_regex"]
                        with gr.Blocks():
                            ban_msgs_regex_list_state = gr.State(value=ban_msgs_regex_list.copy())
                        with gr.Row():
                            ban_msgs_regex_list_display = gr.TextArea(
                                value="\n".join(ban_msgs_regex_list),
                                label="违禁消息正则列表",
                                interactive=False,
                                lines=5,
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                ban_msgs_regex_new_item_input = gr.Textbox(label="添加新违禁消息正则")
                                ban_msgs_regex_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                ban_msgs_regex_item_to_delete = gr.Dropdown(
                                    choices=ban_msgs_regex_list, label="选择要删除的违禁消息正则"
                                )
                            ban_msgs_regex_delete_btn = gr.Button("删除", scale=1)

                        ban_msgs_regex_final_result = gr.Text(label="修改后的违禁消息正则")
                        ban_msgs_regex_add_btn.click(
                            add_item,
                            inputs=[ban_msgs_regex_new_item_input, ban_msgs_regex_list_state],
                            outputs=[
                                ban_msgs_regex_list_state,
                                ban_msgs_regex_list_display,
                                ban_msgs_regex_item_to_delete,
                                ban_msgs_regex_final_result,
                            ],
                        )

                        ban_msgs_regex_delete_btn.click(
                            delete_item,
                            inputs=[ban_msgs_regex_item_to_delete, ban_msgs_regex_list_state],
                            outputs=[
                                ban_msgs_regex_list_state,
                                ban_msgs_regex_list_display,
                                ban_msgs_regex_item_to_delete,
                                ban_msgs_regex_final_result,
                            ],
                        )
                    with gr.Row():
                        check_interval = gr.Number(
                            value=config_data["emoji"]["check_interval"], label="检查表情包的时间间隔"
                        )
                    with gr.Row():
                        register_interval = gr.Number(
                            value=config_data["emoji"]["register_interval"], label="注册表情包的时间间隔"
                        )
                    with gr.Row():
                        auto_save = gr.Checkbox(value=config_data["emoji"]["auto_save"], label="自动保存表情包")
                    with gr.Row():
                        enable_check = gr.Checkbox(value=config_data["emoji"]["enable_check"], label="启用表情包检查")
                    with gr.Row():
                        check_prompt = gr.Textbox(value=config_data["emoji"]["check_prompt"], label="表情包过滤要求")
                    with gr.Row():
                        emoji_save_btn = gr.Button(
                            "保存消息&表情包设置",
                            variant="primary",
                            elem_id="save_personality_btn",
                            elem_classes="save_personality_btn",
                        )
                    with gr.Row():
                        emoji_save_message = gr.Textbox(label="消息&表情包设置保存结果")
                    emoji_save_btn.click(
                        save_message_and_emoji_config,
                        inputs=[
                            min_text_length,
                            max_context_size,
                            emoji_chance,
                            thinking_timeout,
                            response_willing_amplifier,
                            response_interested_rate_amplifier,
                            down_frequency_rate,
                            ban_words_list_state,
                            ban_msgs_regex_list_state,
                            check_interval,
                            register_interval,
                            auto_save,
                            enable_check,
                            check_prompt,
                        ],
                        outputs=[emoji_save_message],
                    )
        with gr.TabItem("4-意愿设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        gr.Markdown("""### 回复设置""")
                    if PARSED_CONFIG_VERSION >= version.parse("0.0.10"):
                        with gr.Row():
                            gr.Markdown("""#### 回复意愿模式""")
                        with gr.Row():
                            gr.Markdown("""回复意愿模式说明：\n
                                        classical为经典回复意愿管理器\n
                                        dynamic为动态意愿管理器\n
                                        custom为自定义意愿管理器
                                        """)
                        with gr.Row():
                            willing_mode = gr.Dropdown(
                                choices=WILLING_MODE_CHOICES,
                                value=config_data["willing"]["willing_mode"],
                                label="回复意愿模式",
                            )
                    else:
                        willing_mode = gr.Textbox(visible=False, value="disabled")
                    if PARSED_CONFIG_VERSION >= version.parse("0.0.11"):
                        with gr.Row():
                                response_willing_amplifier = gr.Number(
                                    value=config_data["willing"]["response_willing_amplifier"],
                                    label="麦麦回复意愿放大系数，一般为1",
                                )
                        with gr.Row():
                            response_interested_rate_amplifier = gr.Number(
                                value=config_data["willing"]["response_interested_rate_amplifier"],
                                label="麦麦回复兴趣度放大系数,听到记忆里的内容时放大系数",
                            )
                        with gr.Row():
                            down_frequency_rate = gr.Number(
                                value=config_data["willing"]["down_frequency_rate"],
                                label="降低回复频率的群组回复意愿降低系数",
                            )
                        with gr.Row():
                            emoji_response_penalty = gr.Number(
                                value=config_data["willing"]["emoji_response_penalty"],
                                label="表情包回复惩罚系数，设为0为不回复单个表情包，减少单独回复表情包的概率",
                            )
                    else:
                        response_willing_amplifier = gr.Number(visible=False, value=1.0)
                        response_interested_rate_amplifier = gr.Number(visible=False, value=1.0)
                        down_frequency_rate = gr.Number(visible=False, value=1.0)
                        emoji_response_penalty = gr.Number(visible=False, value=1.0)
                    with gr.Row():
                        willing_save_btn = gr.Button(
                            "保存意愿设置设置",
                            variant="primary",
                            elem_id="save_personality_btn",
                            elem_classes="save_personality_btn",
                        )
                    with gr.Row():
                        willing_save_message = gr.Textbox(label="意愿设置保存结果")
                    willing_save_btn.click(
                        save_willing_config,
                        inputs=[
                            willing_mode,
                            response_willing_amplifier,
                            response_interested_rate_amplifier,
                            down_frequency_rate,
                            emoji_response_penalty,
                        ],
                        outputs=[emoji_save_message],
                    )
        with gr.TabItem("4-回复&模型设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        model_r1_probability = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["response"]["model_r1_probability"],
                            label="麦麦回答时选择主要回复模型1 模型的概率",
                        )
                    with gr.Row():
                        model_r2_probability = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["response"]["model_v3_probability"],
                            label="麦麦回答时选择主要回复模型2 模型的概率",
                        )
                    with gr.Row():
                        model_r3_probability = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["response"]["model_r1_distill_probability"],
                            label="麦麦回答时选择主要回复模型3 模型的概率",
                        )
                        # 用于显示警告消息
                    with gr.Row():
                        model_warning_greater_text = gr.Markdown()
                        model_warning_less_text = gr.Markdown()

                        # 绑定滑块的值变化事件，确保总和必须等于 1.0
                        model_r1_probability.change(
                            adjust_model_greater_probabilities,
                            inputs=[model_r1_probability, model_r2_probability, model_r3_probability],
                            outputs=[model_warning_greater_text],
                        )
                        model_r2_probability.change(
                            adjust_model_greater_probabilities,
                            inputs=[model_r1_probability, model_r2_probability, model_r3_probability],
                            outputs=[model_warning_greater_text],
                        )
                        model_r3_probability.change(
                            adjust_model_greater_probabilities,
                            inputs=[model_r1_probability, model_r2_probability, model_r3_probability],
                            outputs=[model_warning_greater_text],
                        )
                        model_r1_probability.change(
                            adjust_model_less_probabilities,
                            inputs=[model_r1_probability, model_r2_probability, model_r3_probability],
                            outputs=[model_warning_less_text],
                        )
                        model_r2_probability.change(
                            adjust_model_less_probabilities,
                            inputs=[model_r1_probability, model_r2_probability, model_r3_probability],
                            outputs=[model_warning_less_text],
                        )
                        model_r3_probability.change(
                            adjust_model_less_probabilities,
                            inputs=[model_r1_probability, model_r2_probability, model_r3_probability],
                            outputs=[model_warning_less_text],
                        )
                    if PARSED_CONFIG_VERSION <= version.parse("0.0.10"):
                        with gr.Row():
                            max_response_length = gr.Number(
                                value=config_data["response"]["max_response_length"], label="麦麦回答的最大token数"
                            )
                    else:
                        max_response_length = gr.Number(visible=False,value=0)
                    with gr.Row():
                        gr.Markdown("""### 模型设置""")
                    with gr.Row():
                        gr.Markdown(
                            """### 注意\n
                            如果你是用的是火山引擎的API，建议查看[这篇文档](https://zxmucttizt8.feishu.cn/wiki/MQj7wp6dki6X8rkplApc2v6Enkd)中的修改火山API部分\n
                            因为修改至火山API涉及到修改源码部分，由于自己修改源码造成的问题MaiMBot官方并不因此负责！\n
                            感谢理解，感谢你使用MaiMBot
                            """
                        )
                    with gr.Tabs():
                        with gr.TabItem("1-主要回复模型"):
                            with gr.Row():
                                model1_name = gr.Textbox(
                                    value=config_data["model"]["llm_reasoning"]["name"], label="模型1的名称"
                                )
                            with gr.Row():
                                model1_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["llm_reasoning"]["provider"],
                                    label="模型1（主要回复模型）提供商",
                                )
                            with gr.Row():
                                model1_pri_in = gr.Number(
                                    value=config_data["model"]["llm_reasoning"]["pri_in"],
                                    label="模型1（主要回复模型）的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                model1_pri_out = gr.Number(
                                    value=config_data["model"]["llm_reasoning"]["pri_out"],
                                    label="模型1（主要回复模型）的输出价格（非必填，可以记录消耗）",
                                )
                        with gr.TabItem("2-次要回复模型"):
                            with gr.Row():
                                model2_name = gr.Textbox(
                                    value=config_data["model"]["llm_normal"]["name"], label="模型2的名称"
                                )
                            with gr.Row():
                                model2_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["llm_normal"]["provider"],
                                    label="模型2提供商",
                                )
                            with gr.Row():
                                model2_pri_in = gr.Number(
                                    value=config_data["model"]["llm_normal"]["pri_in"],
                                    label="模型2（次要回复模型）的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                model2_pri_out = gr.Number(
                                    value=config_data["model"]["llm_normal"]["pri_out"],
                                    label="模型2（次要回复模型）的输出价格（非必填，可以记录消耗）",
                                )
                        with gr.TabItem("3-次要模型"):
                            with gr.Row():
                                model3_name = gr.Textbox(
                                    value=config_data["model"]["llm_reasoning_minor"]["name"], label="模型3的名称"
                                )
                            with gr.Row():
                                model3_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["llm_reasoning_minor"]["provider"],
                                    label="模型3提供商",
                                )
                            with gr.Row():
                                model3_pri_in = gr.Number(
                                    value=config_data["model"]["llm_reasoning_minor"]["pri_in"],
                                    label="模型3（次要回复模型）的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                model3_pri_out = gr.Number(
                                    value=config_data["model"]["llm_reasoning_minor"]["pri_out"],
                                    label="模型3（次要回复模型）的输出价格（非必填，可以记录消耗）",
                                )
                        with gr.TabItem("4-情感&主题模型"):
                            with gr.Row():
                                gr.Markdown("""### 情感模型设置""")
                            with gr.Row():
                                emotion_model_name = gr.Textbox(
                                    value=config_data["model"]["llm_emotion_judge"]["name"], label="情感模型名称"
                                )
                            with gr.Row():
                                emotion_model_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["llm_emotion_judge"]["provider"],
                                    label="情感模型提供商",
                                )
                            with gr.Row():
                                emotion_model_pri_in = gr.Number(
                                    value=config_data["model"]["llm_emotion_judge"]["pri_in"],
                                    label="情感模型的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                emotion_model_pri_out = gr.Number(
                                    value=config_data["model"]["llm_emotion_judge"]["pri_out"],
                                    label="情感模型的输出价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                gr.Markdown("""### 主题模型设置""")
                            with gr.Row():
                                topic_judge_model_name = gr.Textbox(
                                    value=config_data["model"]["llm_topic_judge"]["name"], label="主题判断模型名称"
                                )
                            with gr.Row():
                                topic_judge_model_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["llm_topic_judge"]["provider"],
                                    label="主题判断模型提供商",
                                )
                            with gr.Row():
                                topic_judge_model_pri_in = gr.Number(
                                    value=config_data["model"]["llm_topic_judge"]["pri_in"],
                                    label="主题判断模型的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                topic_judge_model_pri_out = gr.Number(
                                    value=config_data["model"]["llm_topic_judge"]["pri_out"],
                                    label="主题判断模型的输出价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                gr.Markdown("""### 主题总结模型设置""")
                            with gr.Row():
                                summary_by_topic_model_name = gr.Textbox(
                                    value=config_data["model"]["llm_summary_by_topic"]["name"], label="主题总结模型名称"
                                )
                            with gr.Row():
                                summary_by_topic_model_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["llm_summary_by_topic"]["provider"],
                                    label="主题总结模型提供商",
                                )
                            with gr.Row():
                                summary_by_topic_model_pri_in = gr.Number(
                                    value=config_data["model"]["llm_summary_by_topic"]["pri_in"],
                                    label="主题总结模型的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                summary_by_topic_model_pri_out = gr.Number(
                                    value=config_data["model"]["llm_summary_by_topic"]["pri_out"],
                                    label="主题总结模型的输出价格（非必填，可以记录消耗）",
                                )
                        with gr.TabItem("5-识图模型"):
                            with gr.Row():
                                gr.Markdown("""### 识图模型设置""")
                            with gr.Row():
                                vlm_model_name = gr.Textbox(
                                    value=config_data["model"]["vlm"]["name"], label="识图模型名称"
                                )
                            with gr.Row():
                                vlm_model_provider = gr.Dropdown(
                                    choices=MODEL_PROVIDER_LIST,
                                    value=config_data["model"]["vlm"]["provider"],
                                    label="识图模型提供商",
                                )
                            with gr.Row():
                                vlm_model_pri_in = gr.Number(
                                    value=config_data["model"]["vlm"]["pri_in"],
                                    label="识图模型的输入价格（非必填，可以记录消耗）",
                                )
                            with gr.Row():
                                vlm_model_pri_out = gr.Number(
                                    value=config_data["model"]["vlm"]["pri_out"],
                                    label="识图模型的输出价格（非必填，可以记录消耗）",
                                )
                    with gr.Row():
                        save_model_btn = gr.Button("保存回复&模型设置", variant="primary", elem_id="save_model_btn")
                    with gr.Row():
                        save_btn_message = gr.Textbox()
                        save_model_btn.click(
                            save_response_model_config,
                            inputs=[
                                willing_mode,
                                model_r1_probability,
                                model_r2_probability,
                                model_r3_probability,
                                max_response_length,
                                model1_name,
                                model1_provider,
                                model1_pri_in,
                                model1_pri_out,
                                model2_name,
                                model2_provider,
                                model2_pri_in,
                                model2_pri_out,
                                model3_name,
                                model3_provider,
                                model3_pri_in,
                                model3_pri_out,
                                emotion_model_name,
                                emotion_model_provider,
                                emotion_model_pri_in,
                                emotion_model_pri_out,
                                topic_judge_model_name,
                                topic_judge_model_provider,
                                topic_judge_model_pri_in,
                                topic_judge_model_pri_out,
                                summary_by_topic_model_name,
                                summary_by_topic_model_provider,
                                summary_by_topic_model_pri_in,
                                summary_by_topic_model_pri_out,
                                vlm_model_name,
                                vlm_model_provider,
                                vlm_model_pri_in,
                                vlm_model_pri_out,
                            ],
                            outputs=[save_btn_message],
                        )
        with gr.TabItem("5-记忆&心情设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        gr.Markdown("""### 记忆设置""")
                    with gr.Row():
                        build_memory_interval = gr.Number(
                            value=config_data["memory"]["build_memory_interval"],
                            label="记忆构建间隔 单位秒,间隔越低，麦麦学习越多，但是冗余信息也会增多",
                        )
                    if PARSED_CONFIG_VERSION >= version.parse("0.0.11"):
                        with gr.Row():
                            gr.Markdown("---")
                        with gr.Row():
                            gr.Markdown("""### 记忆构建分布设置""")
                        with gr.Row():
                            gr.Markdown("""记忆构建分布参数说明：\n
                                        分布1均值：第一个正态分布的均值\n
                                        分布1标准差：第一个正态分布的标准差\n
                                        分布1权重：第一个正态分布的权重\n
                                        分布2均值：第二个正态分布的均值\n
                                        分布2标准差：第二个正态分布的标准差\n
                                        分布2权重：第二个正态分布的权重
                                        """)
                        with gr.Row():
                            with gr.Column(scale=1):
                                build_memory_dist1_mean = gr.Number(
                                    value=config_data["memory"].get(
                                        "build_memory_distribution", 
                                        [4.0,2.0,0.6,24.0,8.0,0.4]
                                    )[0],
                                    label="分布1均值",
                                )
                            with gr.Column(scale=1):
                                build_memory_dist1_std = gr.Number(
                                    value=config_data["memory"].get(
                                        "build_memory_distribution", 
                                        [4.0,2.0,0.6,24.0,8.0,0.4]
                                    )[1],
                                    label="分布1标准差",
                                )
                            with gr.Column(scale=1):
                                build_memory_dist1_weight = gr.Number(
                                    value=config_data["memory"].get(
                                        "build_memory_distribution", 
                                        [4.0,2.0,0.6,24.0,8.0,0.4]
                                    )[2],
                                    label="分布1权重",
                                )
                        with gr.Row():
                            with gr.Column(scale=1):
                                build_memory_dist2_mean = gr.Number(
                                    value=config_data["memory"].get(
                                        "build_memory_distribution", 
                                        [4.0,2.0,0.6,24.0,8.0,0.4]
                                    )[3],
                                    label="分布2均值",
                                )
                            with gr.Column(scale=1):
                                build_memory_dist2_std = gr.Number(
                                    value=config_data["memory"].get(
                                        "build_memory_distribution", 
                                        [4.0,2.0,0.6,24.0,8.0,0.4]
                                    )[4],
                                    label="分布2标准差",
                                )
                            with gr.Column(scale=1):
                                build_memory_dist2_weight = gr.Number(
                                    value=config_data["memory"].get(
                                        "build_memory_distribution", 
                                        [4.0,2.0,0.6,24.0,8.0,0.4]
                                    )[5],
                                    label="分布2权重",
                                )
                        with gr.Row():
                            gr.Markdown("---")
                    else:
                        build_memory_dist1_mean = gr.Number(value=0.0,visible=False,interactive=False)
                        build_memory_dist1_std = gr.Number(value=0.0,visible=False,interactive=False)
                        build_memory_dist1_weight = gr.Number(value=0.0,visible=False,interactive=False)
                        build_memory_dist2_mean = gr.Number(value=0.0,visible=False,interactive=False)
                        build_memory_dist2_std = gr.Number(value=0.0,visible=False,interactive=False)
                        build_memory_dist2_weight = gr.Number(value=0.0,visible=False,interactive=False)
                    with gr.Row():
                        memory_compress_rate = gr.Number(
                            value=config_data["memory"]["memory_compress_rate"],
                            label="记忆压缩率 控制记忆精简程度 建议保持默认,调高可以获得更多信息，但是冗余信息也会增多",
                        )
                    with gr.Row():
                        forget_memory_interval = gr.Number(
                            value=config_data["memory"]["forget_memory_interval"],
                            label="记忆遗忘间隔 单位秒   间隔越低，麦麦遗忘越频繁，记忆更精简，但更难学习",
                        )
                    with gr.Row():
                        memory_forget_time = gr.Number(
                            value=config_data["memory"]["memory_forget_time"],
                            label="多长时间后的记忆会被遗忘 单位小时 ",
                        )
                    with gr.Row():
                        memory_forget_percentage = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["memory"]["memory_forget_percentage"],
                            label="记忆遗忘比例 控制记忆遗忘程度 越大遗忘越多 建议保持默认",
                        )
                    with gr.Row():
                        memory_ban_words_list = config_data["memory"]["memory_ban_words"]
                        with gr.Blocks():
                            memory_ban_words_list_state = gr.State(value=memory_ban_words_list.copy())

                        with gr.Row():
                            memory_ban_words_list_display = gr.TextArea(
                                value="\n".join(memory_ban_words_list),
                                label="不希望记忆词列表",
                                interactive=False,
                                lines=5,
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                memory_ban_words_new_item_input = gr.Textbox(label="添加不希望记忆词")
                                memory_ban_words_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                memory_ban_words_item_to_delete = gr.Dropdown(
                                    choices=memory_ban_words_list, label="选择要删除的不希望记忆词"
                                )
                            memory_ban_words_delete_btn = gr.Button("删除", scale=1)

                        memory_ban_words_final_result = gr.Text(label="修改后的不希望记忆词列表")
                        memory_ban_words_add_btn.click(
                            add_item,
                            inputs=[memory_ban_words_new_item_input, memory_ban_words_list_state],
                            outputs=[
                                memory_ban_words_list_state,
                                memory_ban_words_list_display,
                                memory_ban_words_item_to_delete,
                                memory_ban_words_final_result,
                            ],
                        )

                        memory_ban_words_delete_btn.click(
                            delete_item,
                            inputs=[memory_ban_words_item_to_delete, memory_ban_words_list_state],
                            outputs=[
                                memory_ban_words_list_state,
                                memory_ban_words_list_display,
                                memory_ban_words_item_to_delete,
                                memory_ban_words_final_result,
                            ],
                        )
                    with gr.Row():
                        mood_update_interval = gr.Number(
                            value=config_data["mood"]["mood_update_interval"], label="心情更新间隔 单位秒"
                        )
                    with gr.Row():
                        mood_decay_rate = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["mood"]["mood_decay_rate"],
                            label="心情衰减率",
                        )
                    with gr.Row():
                        mood_intensity_factor = gr.Number(
                            value=config_data["mood"]["mood_intensity_factor"], label="心情强度因子"
                        )
                    with gr.Row():
                        save_memory_mood_btn = gr.Button("保存记忆&心情设置", variant="primary")
                    with gr.Row():
                        save_memory_mood_message = gr.Textbox()
                    with gr.Row():
                        save_memory_mood_btn.click(
                            save_memory_mood_config,
                            inputs=[
                                build_memory_interval,
                                memory_compress_rate,
                                forget_memory_interval,
                                memory_forget_time,
                                memory_forget_percentage,
                                memory_ban_words_list_state,
                                mood_update_interval,
                                mood_decay_rate,
                                mood_intensity_factor,
                                build_memory_dist1_mean,
                                build_memory_dist1_std,
                                build_memory_dist1_weight,
                                build_memory_dist2_mean,
                                build_memory_dist2_std,
                                build_memory_dist2_weight,
                            ],
                            outputs=[save_memory_mood_message],
                        )
        with gr.TabItem("6-群组设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        gr.Markdown("""## 群组设置""")
                    with gr.Row():
                        gr.Markdown("""### 可以回复消息的群""")
                    with gr.Row():
                        talk_allowed_list = config_data["groups"]["talk_allowed"]
                        with gr.Blocks():
                            talk_allowed_list_state = gr.State(value=talk_allowed_list.copy())

                        with gr.Row():
                            talk_allowed_list_display = gr.TextArea(
                                value="\n".join(map(str, talk_allowed_list)),
                                label="可以回复消息的群列表",
                                interactive=False,
                                lines=5,
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                talk_allowed_new_item_input = gr.Textbox(label="添加新群")
                                talk_allowed_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                talk_allowed_item_to_delete = gr.Dropdown(
                                    choices=talk_allowed_list, label="选择要删除的群"
                                )
                            talk_allowed_delete_btn = gr.Button("删除", scale=1)

                        talk_allowed_final_result = gr.Text(label="修改后的可以回复消息的群列表")
                        talk_allowed_add_btn.click(
                            add_int_item,
                            inputs=[talk_allowed_new_item_input, talk_allowed_list_state],
                            outputs=[
                                talk_allowed_list_state,
                                talk_allowed_list_display,
                                talk_allowed_item_to_delete,
                                talk_allowed_final_result,
                            ],
                        )

                        talk_allowed_delete_btn.click(
                            delete_int_item,
                            inputs=[talk_allowed_item_to_delete, talk_allowed_list_state],
                            outputs=[
                                talk_allowed_list_state,
                                talk_allowed_list_display,
                                talk_allowed_item_to_delete,
                                talk_allowed_final_result,
                            ],
                        )
                    with gr.Row():
                        talk_frequency_down_list = config_data["groups"]["talk_frequency_down"]
                        with gr.Blocks():
                            talk_frequency_down_list_state = gr.State(value=talk_frequency_down_list.copy())

                        with gr.Row():
                            talk_frequency_down_list_display = gr.TextArea(
                                value="\n".join(map(str, talk_frequency_down_list)),
                                label="降低回复频率的群列表",
                                interactive=False,
                                lines=5,
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                talk_frequency_down_new_item_input = gr.Textbox(label="添加新群")
                                talk_frequency_down_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                talk_frequency_down_item_to_delete = gr.Dropdown(
                                    choices=talk_frequency_down_list, label="选择要删除的群"
                                )
                            talk_frequency_down_delete_btn = gr.Button("删除", scale=1)

                        talk_frequency_down_final_result = gr.Text(label="修改后的降低回复频率的群列表")
                        talk_frequency_down_add_btn.click(
                            add_int_item,
                            inputs=[talk_frequency_down_new_item_input, talk_frequency_down_list_state],
                            outputs=[
                                talk_frequency_down_list_state,
                                talk_frequency_down_list_display,
                                talk_frequency_down_item_to_delete,
                                talk_frequency_down_final_result,
                            ],
                        )

                        talk_frequency_down_delete_btn.click(
                            delete_int_item,
                            inputs=[talk_frequency_down_item_to_delete, talk_frequency_down_list_state],
                            outputs=[
                                talk_frequency_down_list_state,
                                talk_frequency_down_list_display,
                                talk_frequency_down_item_to_delete,
                                talk_frequency_down_final_result,
                            ],
                        )
                    with gr.Row():
                        ban_user_id_list = config_data["groups"]["ban_user_id"]
                        with gr.Blocks():
                            ban_user_id_list_state = gr.State(value=ban_user_id_list.copy())

                        with gr.Row():
                            ban_user_id_list_display = gr.TextArea(
                                value="\n".join(map(str, ban_user_id_list)),
                                label="禁止回复消息的QQ号列表",
                                interactive=False,
                                lines=5,
                            )
                        with gr.Row():
                            with gr.Column(scale=3):
                                ban_user_id_new_item_input = gr.Textbox(label="添加新QQ号")
                                ban_user_id_add_btn = gr.Button("添加", scale=1)

                        with gr.Row():
                            with gr.Column(scale=3):
                                ban_user_id_item_to_delete = gr.Dropdown(
                                    choices=ban_user_id_list, label="选择要删除的QQ号"
                                )
                            ban_user_id_delete_btn = gr.Button("删除", scale=1)

                        ban_user_id_final_result = gr.Text(label="修改后的禁止回复消息的QQ号列表")
                        ban_user_id_add_btn.click(
                            add_int_item,
                            inputs=[ban_user_id_new_item_input, ban_user_id_list_state],
                            outputs=[
                                ban_user_id_list_state,
                                ban_user_id_list_display,
                                ban_user_id_item_to_delete,
                                ban_user_id_final_result,
                            ],
                        )

                        ban_user_id_delete_btn.click(
                            delete_int_item,
                            inputs=[ban_user_id_item_to_delete, ban_user_id_list_state],
                            outputs=[
                                ban_user_id_list_state,
                                ban_user_id_list_display,
                                ban_user_id_item_to_delete,
                                ban_user_id_final_result,
                            ],
                        )
                    with gr.Row():
                        save_group_btn = gr.Button("保存群组设置", variant="primary")
                    with gr.Row():
                        save_group_btn_message = gr.Textbox()
                    with gr.Row():
                        save_group_btn.click(
                            save_group_config,
                            inputs=[
                                talk_allowed_list_state,
                                talk_frequency_down_list_state,
                                ban_user_id_list_state,
                            ],
                            outputs=[save_group_btn_message],
                        )
        with gr.TabItem("7-其他设置"):
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        gr.Markdown("""### 其他设置""")
                    with gr.Row():
                        keywords_reaction_enabled = gr.Checkbox(
                            value=config_data["keywords_reaction"]["enable"], label="是否针对某个关键词作出反应"
                        )
                    if PARSED_CONFIG_VERSION <= version.parse("0.0.10"):
                        with gr.Row():
                            enable_advance_output = gr.Checkbox(
                                value=config_data["others"]["enable_advance_output"], label="是否开启高级输出"
                            )
                        with gr.Row():
                            enable_kuuki_read = gr.Checkbox(
                                value=config_data["others"]["enable_kuuki_read"], label="是否启用读空气功能"
                            )
                        with gr.Row():
                            enable_debug_output = gr.Checkbox(
                                value=config_data["others"]["enable_debug_output"], label="是否开启调试输出"
                            )
                        with gr.Row():
                            enable_friend_chat = gr.Checkbox(
                                value=config_data["others"]["enable_friend_chat"], label="是否开启好友聊天"
                            )
                    elif PARSED_CONFIG_VERSION >= version.parse("0.0.11"):
                        with gr.Row():
                            enable_friend_chat = gr.Checkbox(
                                value=config_data["experimental"]["enable_friend_chat"], label="是否开启好友聊天"
                            )
                        enable_advance_output = gr.Checkbox(value=False,visible=False,interactive=False)
                        enable_kuuki_read = gr.Checkbox(value=False,visible=False,interactive=False)
                        enable_debug_output = gr.Checkbox(value=False,visible=False,interactive=False)
                    if PARSED_CONFIG_VERSION > HAVE_ONLINE_STATUS_VERSION:
                        with gr.Row():
                            gr.Markdown(
                                """### 远程统计设置\n
                                测试功能，发送统计信息，主要是看全球有多少只麦麦
                                """
                            )
                        with gr.Row():
                            remote_status = gr.Checkbox(
                                value=config_data["remote"]["enable"], label="是否开启麦麦在线全球统计"
                            )
                    if PARSED_CONFIG_VERSION >= version.parse("0.0.11"):
                        with gr.Row():
                            gr.Markdown("""### 回复分割器设置""")
                        with gr.Row():
                            enable_response_spliter = gr.Checkbox(
                                value=config_data["response_spliter"]["enable_response_spliter"],
                                label="是否启用回复分割器"
                            )
                        with gr.Row():
                            response_max_length = gr.Number(
                                value=config_data["response_spliter"]["response_max_length"],
                                label="回复允许的最大长度"
                                )
                        with gr.Row():
                            response_max_sentence_num = gr.Number(
                                value=config_data["response_spliter"]["response_max_sentence_num"],
                                label="回复允许的最大句子数"
                                )
                    else:
                        enable_response_spliter = gr.Checkbox(value=False,visible=False,interactive=False)
                        response_max_length = gr.Number(value=0,visible=False,interactive=False)
                        response_max_sentence_num = gr.Number(value=0,visible=False,interactive=False)
                    with gr.Row():
                        gr.Markdown("""### 中文错别字设置""")
                    with gr.Row():
                        chinese_typo_enabled = gr.Checkbox(
                            value=config_data["chinese_typo"]["enable"], label="是否开启中文错别字"
                        )
                    with gr.Row():
                        error_rate = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.001,
                            value=config_data["chinese_typo"]["error_rate"],
                            label="单字替换概率",
                        )
                    with gr.Row():
                        min_freq = gr.Number(value=config_data["chinese_typo"]["min_freq"], label="最小字频阈值")
                    with gr.Row():
                        tone_error_rate = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.01,
                            value=config_data["chinese_typo"]["tone_error_rate"],
                            label="声调错误概率",
                        )
                    with gr.Row():
                        word_replace_rate = gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.001,
                            value=config_data["chinese_typo"]["word_replace_rate"],
                            label="整词替换概率",
                        )
                    with gr.Row():
                        save_other_config_btn = gr.Button("保存其他配置", variant="primary")
                    with gr.Row():
                        save_other_config_message = gr.Textbox()
                    with gr.Row():
                        if PARSED_CONFIG_VERSION <= HAVE_ONLINE_STATUS_VERSION:
                            remote_status = gr.Checkbox(value=False, visible=False)
                        save_other_config_btn.click(
                            save_other_config,
                            inputs=[
                                keywords_reaction_enabled,
                                enable_advance_output,
                                enable_kuuki_read,
                                enable_debug_output,
                                enable_friend_chat,
                                chinese_typo_enabled,
                                error_rate,
                                min_freq,
                                tone_error_rate,
                                word_replace_rate,
                                remote_status,
                                enable_response_spliter,
                                response_max_length,
                                response_max_sentence_num
                            ],
                            outputs=[save_other_config_message],
                        )
# 检查端口是否可用
def is_port_available(port, host='0.0.0.0'):
    """检查指定的端口是否可用"""
    try:
        # 创建一个socket对象
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 设置socket重用地址选项
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 尝试绑定端口
        sock.bind((host, port))
        # 如果成功绑定，则关闭socket并返回True
        sock.close()
        return True
    except socket.error:
        # 如果绑定失败，说明端口已被占用
        return False


    # 寻找可用端口
def find_available_port(start_port=7000, max_port=8000):
    """
    从start_port开始，寻找可用的端口
    如果端口被占用，尝试下一个端口，直到找到可用端口或达到max_port
    """
    port = start_port
    while port <= max_port:
        if is_port_available(port):
            logger.info(f"找到可用端口: {port}")
            return port
        logger.warning(f"端口 {port} 已被占用，尝试下一个端口")
        port += 1
    # 如果所有端口都被占用，返回None
    logger.error(f"无法找到可用端口 (已尝试 {start_port}-{max_port})")
    return None

# 寻找可用端口
launch_port = find_available_port(7000, 8000) or 7000

app.queue().launch(  # concurrency_count=511, max_size=1022
    server_name="0.0.0.0",
    inbrowser=True,
    share=is_share,
    server_port=launch_port,
    debug=debug,
    quiet=True,
)

