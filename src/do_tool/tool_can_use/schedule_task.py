from src.do_tool.tool_can_use.base_tool import BaseTool
from src.common.logger import get_module_logger
from typing import Dict, Any, Callable, Optional, List, Tuple, Literal
import asyncio
import dateparser
from datetime import datetime, timezone, timedelta
import traceback
import random
import inspect 
import re

logger = get_module_logger("schedule_task_tool")

# --- 定义 UTC+8 时区 --- #
# 使用 datetime.timezone 创建一个固定的 UTC+8 时区对象
UTC_PLUS_8 = timezone(timedelta(hours=8), name='UTC+8')

# --- 中文数字映射 ---
# 用于将中文数字时间描述转换为阿拉伯数字
CHINESE_NUMERALS = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15, '十六': 16,
    '十七': 17, '十八': 18, '十九': 19, '二十': 20, '二十一': 21, '二十二': 22, '二十三': 23,
    '二十四': 24, '零': 0,
}

# --- 任务添加结果类型 ---
# 定义 add_task 方法可能返回的状态字面量
AddTaskStatus = Literal["ADDED", "OVERWRITTEN", "FAILED"]

# --- 自定义时间解析器类型定义 ---
# 定义自定义解析器的结构：(关键词列表, 解析函数)
# 解析函数接收 (时间描述字符串, UTC+8 时区对象) -> 可选的 UTC datetime 对象
# CustomTimeParser = Tuple[List[str], Callable[[str, timezone], Optional[datetime]]] # 旧定义，不再使用

# --- 全局变量存储回调函数 ---
# 用于存储任务到期时执行的异步回调函数
_task_callback: Optional[Callable[[str, Dict[str, Any]], Any]] = None

# --- 辅助函数：检查未来指示词 ---
def _contains_future_keyword(text: str) -> bool:
    """检查文本是否包含明确的未来日期/时间指示词。
    用于常理判断，避免将过去的时间点误解为明天。
    """
    # 可以根据需要扩展这个列表
    future_keywords = [
        "明天", "后天", "大后天",
        "下周", "下月", "下年",
        "星期", "周", # 如果后面跟数字，通常指未来
        "礼拜",
        "next", "tomorrow", "future",
        "明晚", "明早", "后晚", "后早",
        r"\d+\s*天后", r"\d+\s*小时后", r"\d+\s*分钟后", # 匹配 "数字+单位+后"
        r"\d{4}[-/年]", # 匹配明确的年份
        r"\d{1,2}[-/月]", # 匹配明确的月份
    ]
    text_lower = text.lower()
    for keyword in future_keywords:
        # 对正则表达式模式进行搜索，对普通字符串进行包含检查
        try:
            if re.search(keyword, text_lower):
                return True
        except re.error: # 如果 keyword 不是有效的正则表达式，按普通字符串处理
             if keyword in text_lower:
                 return True
    # 特殊处理：如果包含 "今天" 但也包含 "早上/上午/中午/下午/晚上" 且那个时间段未过，也算
    # Note: 这个检查在常理判断中更精细地处理，这里可以简化或移除
    # if "今天" in text_lower or "今日" in text_lower or "today" in text_lower:
    #     return True # 明确指定了今天，我们相信用户

    return False

# --- 默认任务处理函数 ---
async def _default_task_handler(task_description: str, callback_info: Dict[str, Any]):
    """
    默认的任务处理器，在没有通过 set_task_callback 设置外部回调时使用。
    仅记录日志和打印信息，表明任务已到期。
    """
    logger.warning("正在使用默认任务处理器执行计划任务（未设置特定回调）。")
    logger.info(f"[默认处理器] 任务描述: '{task_description}'")
    logger.info(f"[默认处理器] 任务上下文: {callback_info}")
    print("-" * 30)
    print(f"[计划任务执行 - 默认处理器]")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"任务: {task_description}")
    print(f"信息: {callback_info}")
    print("-" * 30)

# --- 设置回调函数 (允许覆盖默认) ---
def set_task_callback(callback_func: Callable[[str, Dict[str, Any]], Any]):
    """
    设置当任务到期时要执行的回调函数。
    此函数必须是异步函数 (async def)。
    允许覆盖默认的 _default_task_handler。
    """
    global _task_callback
    if not inspect.iscoroutinefunction(callback_func):
        raise TypeError("提供的回调函数必须是 'async def' 定义的协程函数。")
    _task_callback = callback_func
    logger.info(f"任务执行回调函数已设置为: {getattr(callback_func, '__name__', repr(callback_func))}")

# --- 自定义时间处理函数 --- #
# 这些函数优先于 dateparser 被调用，用于处理特定或模糊的时间描述。

def parse_a_moment(time_description: str, local_tz: timezone) -> Optional[datetime]:
    """解析 "一会" 等描述为 3-5 分钟后的随机时间"""
    delay_seconds = random.uniform(180, 300)# 个人理解，并且后续麦麦说过疑问的话，可能会自己进行二次决策，比如某次测试麦麦一会理解为半小时。。。
    now_local = datetime.now(local_tz) # 使用传入的 UTC+8 时区获取当前时间
    target_dt_local = now_local + timedelta(seconds=delay_seconds)
    target_dt_utc = target_dt_local.astimezone(timezone.utc)
    logger.info(f"自定义解析器 'parse_a_moment' 处理 '{time_description}'，解析为随机 {delay_seconds:.2f} 秒后: {target_dt_utc.isoformat()}")
    return target_dt_utc

def parse_day_after_tomorrow(time_description: str, local_tz: timezone) -> Optional[datetime]:
    """解析 "大后天" 等描述为三天后的特定时间（带时间段推断）"""
    now_local = datetime.now(local_tz) # 使用传入的 UTC+8 时区获取当前时间
    target_date_local = (now_local + timedelta(days=3)).date()
    hour, minute = 9, 0 # 默认上午9点
    time_description_lower = time_description.lower()
    # 根据时间段词语调整小时
    if "早上" in time_description_lower or "上午" in time_description_lower or "morning" in time_description_lower: hour = 9
    elif "中午" in time_description_lower or "noon" in time_description_lower: hour = 12
    elif "下午" in time_description_lower or "afternoon" in time_description_lower: hour = 14
    elif "晚上" in time_description_lower or "evening" in time_description_lower or "night" in time_description_lower: hour = 20
    # 组合日期和时间时，附加传入的 UTC+8 时区
    target_dt_local = datetime.combine(target_date_local, datetime.min.time().replace(hour=hour, minute=minute), tzinfo=local_tz)
    if target_dt_local <= now_local: logger.warning(f"计算出的目标时间 {target_dt_local.isoformat()} 早于或等于当前时间 {now_local.isoformat()}。")
    target_dt_utc = target_dt_local.astimezone(timezone.utc)
    try: # 尝试用本地 (UTC+8) 时区格式化日志
        log_time_str = target_dt_local.strftime('%Y-%m-%d %H:%M:%S %z')
    except Exception: log_time_str = target_dt_local.isoformat() # 回退到本地 ISO 格式
    logger.debug(f"自定义解析器 'parse_day_after_tomorrow' 处理 '{time_description}', 解析为: {log_time_str}")
    return target_dt_utc

def parse_day_before_yesterday(time_description: str, local_tz: timezone) -> Optional[datetime]:
    """解析 "大前天" 等描述为三天前的特定时间（带时间段推断）"""
    now_local = datetime.now(local_tz) # 使用传入的 UTC+8 时区获取当前时间
    target_date_local = (now_local - timedelta(days=3)).date()
    hour, minute = 9, 0 # 默认上午9点
    time_description_lower = time_description.lower()
    # 根据时间段词语调整小时
    if "早上" in time_description_lower or "上午" in time_description_lower or "morning" in time_description_lower: hour = 9
    elif "中午" in time_description_lower or "noon" in time_description_lower: hour = 12
    elif "下午" in time_description_lower or "afternoon" in time_description_lower: hour = 14
    elif "晚上" in time_description_lower or "evening" in time_description_lower or "night" in time_description_lower: hour = 20
    # 组合日期和时间时，附加传入的 UTC+8 时区
    target_dt_local = datetime.combine(target_date_local, datetime.min.time().replace(hour=hour, minute=minute), tzinfo=local_tz)
    # 大前天必然早于当前，仅记录调试日志
    if target_dt_local <= now_local: logger.debug(f"计算出的目标时间 {target_dt_local.isoformat()} (大前天) 早于当前。")
    target_dt_utc = target_dt_local.astimezone(timezone.utc)
    try: # 尝试用本地 (UTC+8) 时区格式化日志
        log_time_str = target_dt_local.strftime('%Y-%m-%d %H:%M:%S %z')
    except Exception: log_time_str = target_dt_local.isoformat() # 回退到本地 ISO 格式
    logger.debug(f"自定义解析器 'parse_day_before_yesterday' 处理 '{time_description}', 解析为: {log_time_str}")
    return target_dt_utc # 注意：返回的是过去的时间

def parse_specific_oclock(time_description: str, local_tz: timezone) -> Optional[datetime]:
    """解析 "X点" 或 "[时段]X点" 描述为下一个未来的整点时间 (支持中文和阿拉伯数字)。包含常理判断。"""
    now_local = datetime.now(local_tz) # 使用传入的 UTC+8 时区获取当前时间
    target_hour: Optional[int] = None
    hour_part = None # 用于记录提取出的小时部分 (如 "八", "8")

    # 使用正则表达式匹配，允许可选的前缀
    pattern = r"^(?:(早上|上午|中午|下午|晚上|morning|afternoon|evening|night))?([一二三四五六七八九十零]|[0-9]{1,2})[点時]$"
    match = re.match(pattern, time_description.lower().strip())

    if not match:
        logger.debug(f"'parse_specific_oclock' 未匹配到模式: '{time_description}'")
        return None
    
    # period_part = match.group(1) # 获取时间段部分 (仍然可以获取，但不再用于 PM 推断逻辑)
    hour_part = match.group(2) # 获取小时部分

    if hour_part in CHINESE_NUMERALS:
        num = CHINESE_NUMERALS[hour_part]
        # 支持 0 点到 24 点 (24点视为次日0点)
        if 0 <= num <= 24: target_hour = 0 if num == 24 else num
    else:
        try:
            num = int(hour_part)
            # 支持 0 点到 24 点 (24点视为次日0点)
            if 0 <= num <= 24: target_hour = 0 if num == 24 else num
        except ValueError: pass # 非数字，忽略

    # 如果无法解析出小时，返回 None
    if target_hour is None:
        logger.warning(f"无法从 '{time_description}' 的小时部分 '{hour_part}' 解析出有效小时 (0-24)。")
        return None

    # --- 根据上下文推断 PM --- #
    original_target_hour = target_hour # 保存原始解析的小时
    
    if now_local.hour >= 12 and 1 <= target_hour <= 6:
        # 如果当前是中午或之后，且小时是 1-6 点，则假定用户指的是 PM
        target_hour += 12
        logger.debug(f"当前>=12点，将小时 {original_target_hour} 推断为 PM ({target_hour}) 处理")
    # --- 推断结束 --- #

    # --- 常理判断 --- #
    # 使用（可能调整后的）target_hour 计算今天的时间点
    potential_time_today = now_local.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    is_today_past = potential_time_today <= now_local
    # 检查时间今天是否已过，且原始描述中没有明确的未来指示词
    if is_today_past and not _contains_future_keyword(time_description): # 检查原始描述
        logger.warning(f"时间 '{time_description}' (推断/解析为 {potential_time_today.strftime('%H:%M')}) 今天已过去，且未明确指定未来日期，根据常理判断拒绝解析。")
        return None
    # --- 常理判断结束 --- #

    # 处理 24点/二十四点 作为次日 0点
    # 注意：这里的 target_hour 可能是调整过的 (e.g., 13)，但原始 hour_part 仍然是 24 或 二十四
    if original_target_hour == 0 and hour_part and ('24' in hour_part or '二十四' in hour_part):
         potential_time_tomorrow = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
         target_dt_local = potential_time_tomorrow
    else:
        # 如果今天的时间点没过，就用今天；否则用明天的时间点
        potential_time_tomorrow = (now_local + timedelta(days=1)).replace(hour=target_hour, minute=0, second=0, microsecond=0)
        target_dt_local = potential_time_today if not is_today_past else potential_time_tomorrow

    if target_dt_local is None: logger.error(f"计算下一个 '{time_description}' 时间点失败 (逻辑错误)。"); return None

    target_dt_utc = target_dt_local.astimezone(timezone.utc)
    try: # 尝试用本地 (UTC+8) 时区格式化日志
        log_time_str = target_dt_local.strftime('%Y-%m-%d %H:%M:%S %z')
    except Exception: log_time_str = target_dt_local.isoformat() # 回退到本地 ISO 格式
    logger.info(f"自定义解析器 'parse_specific_oclock' 处理 '{time_description}', 解析为: {log_time_str}")
    return target_dt_utc

def parse_specific_oclock_half(time_description: str, local_tz: timezone) -> Optional[datetime]:
    """解析 "X点半" 或 "[时段]X点半" 描述为下一个未来的半点时间 (支持中文和阿拉伯数字)。包含常理判断。"""
    now_local = datetime.now(local_tz) # 使用传入的 UTC+8 时区获取当前时间
    target_hour: Optional[int] = None
    hour_part = None # 用于记录提取出的小时部分 (如 "八", "8")
    # 使用正则表达式匹配，允许可选的前缀
    match = re.match(r"^(?:早上|上午|中午|下午|晚上|morning|afternoon|evening|night)?([一二三四五六七八九十零]|[0-9]{1,2})[点時]半$", time_description.lower().strip())

    if not match:
        logger.debug(f"'parse_specific_oclock_half' 未匹配到模式: '{time_description}'")
        return None

    hour_part = match.group(1) # The part representing the hour (e.g., "八", "8")

    # Convert hour part to integer (0-23 for half-hour)
    if hour_part in CHINESE_NUMERALS:
        num = CHINESE_NUMERALS[hour_part]
        if 0 <= num <= 23:
             target_hour = num
    else:
        try:
            num = int(hour_part)
            if 0 <= num <= 23:
                target_hour = num
        except ValueError:
            pass
    if target_hour is None:
        logger.warning(f"无法从 '{time_description}' 的小时部分 '{hour_part}' 解析出有效小时 (0-23)。")
        return None
    # --- 常理判断 --- #
    potential_time_today = now_local.replace(hour=target_hour, minute=30, second=0, microsecond=0)
    is_today_past = potential_time_today <= now_local
    if is_today_past and not _contains_future_keyword(time_description): # 检查原始描述
        logger.warning(f"时间 '{time_description}' ({potential_time_today.strftime('%H:%M')}) 今天已过去，且未明确指定未来日期，根据常理判断拒绝解析。")
        return None
    # --- 常理判断结束 --- #

    potential_time_tomorrow = (now_local + timedelta(days=1)).replace(hour=target_hour, minute=30, second=0, microsecond=0)
    target_dt_local = potential_time_today if not is_today_past else potential_time_tomorrow

    target_dt_utc = target_dt_local.astimezone(timezone.utc)
    try: # 尝试用本地 (UTC+8) 时区格式化日志
        log_time_str = target_dt_local.strftime('%Y-%m-%d %H:%M:%S %z')
    except Exception:
        log_time_str = target_dt_local.isoformat() # Fallback to local ISO

    logger.info(f"自定义解析器 'parse_specific_oclock_half' 处理 '{time_description}', 解析为: {log_time_str}")
    return target_dt_utc

def parse_specific_hour_minute(time_description: str, local_tz: timezone) -> Optional[datetime]:
    """解析 "X点Y分" 或 "X点Y十" 等描述为下一个未来的具体时间。包含常理判断。"""
    now_local = datetime.now(local_tz)
    target_hour: Optional[int] = None
    target_minute: Optional[int] = None

    # 正则表达式尝试匹配多种格式: X点Y十(Z), X点零Y, X点Y (默认 Y < 10)
    # 分组: (1: 时间段)? (2: 小时) (3: 分钟-几十)? (4: 分钟-个位/零几)?
    pattern = r"^(?:(早上|上午|中午|下午|晚上|morning|afternoon|evening|night))?([一二三四五六七八九十零]|[0-9]{1,2})[点時]"
    pattern += r"(?:([一二三四五六七八九])十([一二三四五六七八九分])?|零([一二三四五六七八九])分?|([一二三四五六七八九十]|[0-5]?[0-9])分?)$"
    match = re.match(pattern, time_description.lower().strip())

    if not match:
        logger.debug(f"'parse_specific_hour_minute' 未匹配到模式: '{time_description}'")
        return None

    _period_part = match.group(1)
    hour_part = match.group(2)
    minute_tens_part = match.group(3) # e.g., '四' in 四十
    minute_tens_unit_part = match.group(4) # e.g., '五' in 四十五 or '分' in 四十
    minute_zero_part = match.group(5) # e.g., '五' in 零五
    minute_single_part = match.group(6) # e.g., '五', '15', '50'

    # --- 解析小时 --- #
    if hour_part in CHINESE_NUMERALS:
        num = CHINESE_NUMERALS[hour_part]
        if 0 <= num <= 23: target_hour = num
    else:
        try:
            num = int(hour_part)
            if 0 <= num <= 23: target_hour = num
        except ValueError: pass
    if target_hour is None: logger.warning(f"无法解析小时: {hour_part}"); return None
    original_target_hour = target_hour # 保存原始小时用于 PM 推断

    # --- 解析分钟 --- #
    if minute_tens_part: # 匹配 X十Y 或 X十
        tens_val = CHINESE_NUMERALS.get(minute_tens_part, 0)
        if tens_val > 0:
            target_minute = tens_val * 10
            if minute_tens_unit_part and minute_tens_unit_part != '分': # X十Y (Y不是'分')
                unit_val = CHINESE_NUMERALS.get(minute_tens_unit_part, 0)
                if unit_val > 0: target_minute += unit_val
            # 如果是 X十 或 X十分，target_minute 已经是 tens_val * 10
        else: logger.warning(f"无法解析分钟十位: {minute_tens_part}"); return None
    elif minute_zero_part: # 匹配 零Y
        zero_val = CHINESE_NUMERALS.get(minute_zero_part, 0)
        if 0 < zero_val < 10: target_minute = zero_val
        else: logger.warning(f"无法解析零几分钟: {minute_zero_part}"); return None
    elif minute_single_part: # 匹配 Y (个位数) 或 数字分钟
        if minute_single_part in CHINESE_NUMERALS and minute_single_part != '十': # 中文个位数
             num = CHINESE_NUMERALS[minute_single_part]
             if 0 <= num < 10: target_minute = num
        elif minute_single_part == '十': # 特殊处理 X点十(分)
             target_minute = 10
        else: # 阿拉伯数字分钟
            try:
                num = int(minute_single_part)
                if 0 <= num <= 59: target_minute = num
            except ValueError: pass
    # 如果无法解析出分钟，则默认为 0
    if target_minute is None: target_minute = 0

    if not (0 <= target_minute <= 59):
        logger.warning(f"解析出的分钟数无效 ({target_minute}) from '{time_description}'")
        return None

    # --- 推断 PM (同 parse_specific_oclock) --- #
    if now_local.hour >= 12 and 1 <= original_target_hour <= 6:
        target_hour += 12
        logger.debug(f"当前>=12点，将小时 {original_target_hour} 推断为 PM ({target_hour}) 处理")

    # --- 常理判断 --- #
    potential_time_today = now_local.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    is_today_past = potential_time_today <= now_local
    if is_today_past and not _contains_future_keyword(time_description):
        logger.warning(f"时间 '{time_description}' (解析为 {potential_time_today.strftime('%H:%M')}) 今天已过去，且未明确指定未来日期，根据常理判断拒绝解析。")
        return None

    # --- 计算最终时间 --- #
    potential_time_tomorrow = (now_local + timedelta(days=1)).replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    target_dt_local = potential_time_today if not is_today_past else potential_time_tomorrow

    target_dt_utc = target_dt_local.astimezone(timezone.utc)
    try: log_time_str = target_dt_local.strftime('%Y-%m-%d %H:%M:%S %z')
    except Exception: log_time_str = target_dt_local.isoformat()
    logger.info(f"自定义解析器 'parse_specific_hour_minute' 处理 '{time_description}', 解析为: {log_time_str}")
    return target_dt_utc

# --- 自定义时间解析器配置 (新) --- #
# 使用正则表达式来匹配，并关联到处理函数
CUSTOM_TIME_PARSER_CONFIG = {
    "a_moment": {
        # 精确匹配模糊时间词语
        "regex": r"^(一会|一会儿|一会会|a moment|in a moment|in a bit)$",
        "func": parse_a_moment
    },
    "day_after_tomorrow": {
        # 匹配 "大后天" 开头，允许后面跟其他词语 (如时间段)
        "regex": r"^(大后天|the day after tomorrow).*",
        "func": parse_day_after_tomorrow
    },
    "day_before_yesterday": {
        # 匹配 "大前天" 开头
        "regex": r"^(大前天|the day before yesterday).*",
        "func": parse_day_before_yesterday
    },
    "specific_oclock": {
        # 匹配可选时段 + 小时(中/阿) + 点/時
        "regex": r"^(?:早上|上午|中午|下午|晚上|morning|afternoon|evening|night)?([一二三四五六七八九十零]|[0-9]{1,2})[点時]$",
        "func": parse_specific_oclock
    },
    "specific_oclock_half": {
        # 匹配可选时段 + 小时(中/阿) + 点半/時半
        "regex": r"^(?:早上|上午|中午|下午|晚上|morning|afternoon|evening|night)?([一二三四五六七八九十零]|[0-9]{1,2})[点時]半$",
        "func": parse_specific_oclock_half
    },
    # --- 新增解析器 --- #
    "specific_hour_minute": {
        # 匹配 X点Y分, X点Y十(Z), X点零Y
        "regex": r"^(?:(早上|上午|中午|下午|晚上|morning|afternoon|evening|night))?([一二三四五六七八九十零]|[0-9]{1,2})[点時](?:([一二三四五六七八九])十([一二三四五六七八九分])?|零([一二三四五六七八九])分?|([一二三四五六七八九十]|[0-5]?[0-9])分?)$",
        "func": parse_specific_hour_minute
    }
    # 注意: 解析顺序很重要，更具体的模式应放在前面 (如果存在重叠)
}

# --- TaskScheduler 类 --- #
# 核心任务调度器类，采用单例模式
class TaskScheduler:
    _instance = None
    # 存储待执行的任务列表，格式为 (utc_timestamp, task_description, callback_info)
    scheduled_tasks: List[Tuple[float, str, Dict[str, Any]]] = []
    _scheduler_task: Optional[asyncio.Task] = None # 指向后台运行的调度循环任务

    @classmethod
    def get_instance(cls):
        """获取 TaskScheduler 的单例实例。首次调用时会自动初始化并启动调度循环。"""
        if cls._instance is None:
            logger.info("TaskScheduler 单例首次创建，执行自动设置...")
            cls._instance = cls()
            global _task_callback
            # 如果外部没有设置回调，则自动注册默认回调
            if _task_callback is None:
                logger.info("未检测到外部任务回调，自动注册默认日志记录回调。")
                _task_callback = _default_task_handler
                logger.info(f"默认回调 '{_default_task_handler.__name__}' 已注册。")
            else:
                logger.info("检测到已设置外部任务回调。")
            # 启动后台调度循环
            cls._instance.start()
        return cls._instance

    def has_pending_tasks(self) -> bool:
        """检查当前是否有待处理的任务。"""
        return bool(self.scheduled_tasks)

    def start(self):
        """启动后台任务调度器循环 (如果尚未运行)。"""
        # 检查调度任务是否未创建或已结束
        if not self._scheduler_task or self._scheduler_task.done():
             logger.info("后台任务调度器循环未运行或已结束，正在启动...")
             # 创建并启动 run_scheduler 协程作为后台任务
             self._scheduler_task = asyncio.create_task(self.run_scheduler())
        else:
            logger.debug("后台任务调度器循环已在运行中。")

    async def add_task(self, time_description: str, task_description: str, callback_info: Dict[str, Any], allow_overwrite: bool = False) -> Tuple[bool, AddTaskStatus, Optional[str]]:
        """
        尝试解析时间描述并添加新任务到调度队列。

        Args:
            time_description: 用户提供的时间描述字符串。
            task_description: 任务的描述，用于区分和取消任务。
            callback_info: 传递给任务回调函数的上下文信息。
            allow_overwrite: 如果设置为 true，并且找到了具有相同 task_description 的现有任务，则允许用新时间和上下文覆盖它。默认为 false，此时会拒绝添加重复描述的任务。

        Returns:
            一个元组: (是否成功, 添加状态["ADDED", "OVERWRITTEN", "FAILED"], 目标时间的 ISO 格式字符串 或 None)。
        """
        logger.debug(f"尝试添加任务: 时间='{time_description}', 任务='{task_description}', allow_overwrite={allow_overwrite}")
        was_overwritten = False
        tasks_to_keep = []
        found_duplicate = False

        # --- 0. 检查同名任务 --- #
        current_tasks_snapshot = self.scheduled_tasks[:]
        for timestamp, desc, info in current_tasks_snapshot:
            if desc == task_description:
                found_duplicate = True
                if allow_overwrite:
                    # 显示旧任务的原定 UTC+8 时间
                    try:
                        old_local_time_str = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(UTC_PLUS_8).strftime('%Y-%m-%d %H:%M:%S %z')
                    except Exception:
                        old_local_time_str = f"timestamp {timestamp}"
                    logger.info(f"发现同名任务 '{desc}' 且允许覆盖，将移除旧任务 (原定: {old_local_time_str})")
                else:
                    # 如果不允许覆盖，则保留所有任务，并直接返回失败
                    logger.warning(f"发现同名任务 '{desc}' 但不允许覆盖 (allow_overwrite=False)。添加任务失败。")
                    return (False, "FAILED", None) # 返回失败，不修改任务列表
            else:
                # 保留描述不同的任务
                tasks_to_keep.append((timestamp, desc, info))
        
        # 如果发现重复且允许覆盖，则实际执行移除旧任务的操作
        if found_duplicate and allow_overwrite:
            self.scheduled_tasks = tasks_to_keep # 原子性地更新列表
            was_overwritten = True
            logger.info(f"已移除描述为 '{task_description}' 的旧任务，准备添加新版本。")
        
        try:
            target_dt: Optional[datetime] = None 
            time_description_lower = time_description.lower().strip()

            # ---- 1. 尝试自定义解析器 (使用新配置和正则) ---- #
            for parser_name, config in CUSTOM_TIME_PARSER_CONFIG.items():
                pattern = config["regex"]
                parser_func = config["func"]
                # 使用 re.fullmatch 确保整个字符串匹配模式
                match = re.fullmatch(pattern, time_description_lower)
                if match:
                    logger.debug(f"时间描述 '{time_description_lower}' 匹配到自定义解析器 '{parser_name}' 的模式 '{pattern}'。")
                    try:
                        # 调用解析函数，传入原始描述和本地时区
                        target_dt = parser_func(time_description, UTC_PLUS_8) # 传入原始大小写描述
                        if target_dt:
                            logger.info(f"使用自定义解析器 '{parser_name}' 成功解析。")
                            break # 找到第一个成功的解析器就停止
                        else:
                            # 解析器匹配了模式但返回 None (例如，常理判断失败)
                            logger.warning(f"自定义解析器 '{parser_name}' 匹配模式但返回 None (可能因常理判断等)。")
                            # 这里我们应该停止尝试其他解析器吗？如果一个特定模式匹配但解析失败（如时间已过），
                            # 通常不应再尝试通用解析器。我们将在此处直接返回失败。
                            return (False, "FAILED", None)
                    except Exception as custom_parse_err:
                        logger.error(f"自定义解析器 '{parser_name}' 执行时出错: {custom_parse_err}", exc_info=True)
                        # 如果自定义解析器内部出错，也视为解析失败
                        return (False, "FAILED", None)
                # else: # Debugging - 查看哪个模式不匹配
                #    logger.debug(f"模式 '{pattern}' 未完全匹配 '{time_description_lower}'")

            else: # 如果 for 循环正常结束 (没有 break，即所有自定义解析器都未成功解析)
                 logger.debug("所有自定义解析器均未成功解析。")
                 # 注意: target_dt 在这里仍然是 None

            # ---- 2. 尝试 dateparser (仅当自定义解析器未成功时) ---- #
            if target_dt is None:
                logger.debug(f"尝试使用 dateparser 解析 '{time_description}'")
                # 配置 dateparser: 偏好未来日期，使用 UTC+8 时区 (通过 IANA 名称 'Asia/Shanghai')，返回时区感知对象
                settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'Asia/Shanghai', 'RETURN_AS_TIMEZONE_AWARE': True}
                try:
                    target_dt = dateparser.parse(time_description, settings=settings)
                    if target_dt:
                        logger.debug(f"Dateparser 成功解析。结果时区: {target_dt.tzinfo}")
                        if target_dt.tzinfo is None:
                             logger.warning("Dateparser 返回了无时区的 datetime，这不符合预期 (RETURN_AS_TIMEZONE_AWARE=True)。")
                             try:
                                 target_dt = target_dt.replace(tzinfo=UTC_PLUS_8).astimezone(timezone.utc)
                             except Exception as tz_err_dp:
                                 logger.error(f"附加 UTC+8 时区到 dateparser 结果时出错: {tz_err_dp}. 放弃解析。", exc_info=True)
                                 target_dt = None 
                    else:
                        logger.debug(f"Dateparser 未能解析 '{time_description}'。")
                except Exception as dp_error: 
                    logger.warning(f"调用 dateparser 时出错: {dp_error}")
                    target_dt = None

            # ---- 3. 尝试手动解析相对时间 ---- #
            if target_dt is None:
                logger.debug(f"尝试手动解析相对时间 '{time_description}'")
                try:
                    delta = None
                    match = re.search(r"(\d+(?:\.\d+)?|[一二三四五六七八九十]+)\s*(秒|分钟|小時|小时|天)(?:后|後)", time_description_lower)
                    if match:
                        value_str = match.group(1)
                        unit = match.group(2)
                        value: Optional[float] = None
                        try:
                            value = float(value_str)
                        except ValueError:
                            if value_str in CHINESE_NUMERALS:
                                value = float(CHINESE_NUMERALS[value_str])
                            else:
                                logger.warning(f"无法将手动解析提取的数字 '{value_str}' 转换为有效数值。")
                        if value is not None:
                            if unit == "秒": delta = timedelta(seconds=value)
                            elif unit == "分钟": delta = timedelta(minutes=value)
                            elif unit in ["小时", "小時"]: delta = timedelta(hours=value)
                            elif unit == "天": delta = timedelta(days=value)
                    else:
                        logger.debug(f"手动解析未匹配到 '数字+单位+后' 模式。")
                    if delta: 
                        target_dt = datetime.now(timezone.utc) + delta
                        logger.debug(f"手动解析相对时间成功: {delta}")
                except (ValueError, TypeError) as parse_error: 
                    logger.warning(f"手动解析相对时间时出错: {parse_error}")

            # ---- 4. 检查最终解析结果 ---- #
            if target_dt is None: logger.warning(f"无法将 '{time_description}' 解析为有效时间。" ); return (False, "FAILED", None)

            # ---- 5. 确保目标时间是 UTC 时区 ---- #
            if target_dt.tzinfo is None or target_dt.tzinfo.utcoffset(target_dt) is None:
                logger.warning(f"解析结果 '{target_dt}' 缺少时区信息，将假定为 UTC+8 并转换为 UTC。")
                try:
                    localized_dt = target_dt.replace(tzinfo=UTC_PLUS_8)
                    target_dt = localized_dt.astimezone(timezone.utc) 
                    logger.debug(f"时区转换成功 (naive -> UTC+8 -> UTC): {target_dt.isoformat()}")
                except Exception as tz_err: logger.error(f"本地化到 UTC+8 并转换为 UTC 时出错: {tz_err}. 无法安排任务。", exc_info=True); return (False, "FAILED", None)
            elif target_dt.tzinfo != timezone.utc:
                original_tz = target_dt.tzinfo
                logger.debug(f"解析结果时区为 {original_tz}，将转换为 UTC。")
                target_dt = target_dt.astimezone(timezone.utc)
                logger.debug(f"时区转换成功 ({original_tz} -> UTC): {target_dt.isoformat()}")
            else:
                logger.debug(f"解析结果已是 UTC 时间: {target_dt.isoformat()}")

            # ---- 6. 检查时间是否已过去并安排任务 ---- #
            target_timestamp = target_dt.timestamp()
            current_timestamp = datetime.now(timezone.utc).timestamp()
            if target_timestamp < current_timestamp - 1:
                logger.warning(f"目标时间 {target_dt.isoformat()} (UTC) 已过去，无法安排任务。")
                return (False, "FAILED", None)
            target_timestamp = max(target_timestamp, current_timestamp + 0.01)
            target_dt = datetime.fromtimestamp(target_timestamp, timezone.utc)
            target_dt_iso_str = target_dt.isoformat() 

            task_entry = (target_timestamp, task_description, callback_info.copy())
            task_entry[2]['original_time_description'] = time_description
            task_entry[2]['target_datetime_utc_iso'] = target_dt_iso_str

            self.scheduled_tasks.append(task_entry)
            self.scheduled_tasks.sort() 
            
            status = "OVERWRITTEN" if was_overwritten else "ADDED"
            # 显示目标的 UTC+8 时间
            try:
                target_local_time_str = target_dt.astimezone(UTC_PLUS_8).strftime('%Y-%m-%d %H:%M:%S %z')
            except Exception:
                target_local_time_str = target_dt_iso_str # Fallback to UTC ISO
            # logger.info(f"任务 '{task_description}' 已成功安排在 {target_dt_iso_str} (UTC) (状态: {status})")
            logger.info(f"任务 '{task_description}' 已成功安排在 {target_local_time_str} (状态: {status})")
            return (True, status, target_dt_iso_str)
        
        except Exception as e:
            logger.error(f"添加任务 '{task_description}' 时发生未预期错误: {e}", exc_info=True)
            return (False, "FAILED", None)

    async def cancel_task_by_description(self, description_to_cancel: str) -> int:
        """
        根据任务描述取消一个或多个待处理的任务。
        
        Args:
            description_to_cancel: 要取消的任务的描述字符串。
        
        Returns:
            成功取消的任务数量。
        """
        cancelled_count = 0
        tasks_to_keep = []
        current_tasks_snapshot = self.scheduled_tasks[:]
        for timestamp, desc, info in current_tasks_snapshot:
            if desc != description_to_cancel:
                tasks_to_keep.append((timestamp, desc, info))
            else:
                # 显示原定 UTC+8 时间
                try:
                    local_time_str = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(UTC_PLUS_8).strftime('%Y-%m-%d %H:%M:%S %z')
                except Exception:
                    local_time_str = f"timestamp {timestamp}"
                # logger.info(f"准备取消任务: '{desc}', 原定时间: {datetime.fromtimestamp(timestamp, timezone.utc).isoformat()}")
                logger.info(f"准备取消任务: '{desc}', 原定时间: {local_time_str}")
        
        if len(tasks_to_keep) < len(current_tasks_snapshot):
            cancelled_count = len(current_tasks_snapshot) - len(tasks_to_keep)
            self.scheduled_tasks = tasks_to_keep
            logger.info(f"成功移除 {cancelled_count} 个描述为 '{description_to_cancel}' 的任务。")
        else:
            logger.info(f"未找到描述为 '{description_to_cancel}' 的待执行任务。")
        return cancelled_count

    def get_scheduled_tasks_summary(self) -> List[Dict[str, Any]]:
        """
        获取当前所有待处理任务的摘要列表。

        Returns:
            List[Dict[str, Any]]: 每个字典包含 'description' 和 'scheduled_time_utc8' (格式化的 UTC+8 时间字符串)。
        """
        summary_list = []
        # 使用快照以避免迭代时修改
        current_tasks_snapshot = self.scheduled_tasks[:]
        for timestamp, desc, info in current_tasks_snapshot:
            try:
                # 将 UTC 时间戳转换为 UTC+8 datetime 对象
                scheduled_dt_utc8 = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(UTC_PLUS_8)
                # 格式化为易读字符串
                time_str = scheduled_dt_utc8.strftime('%Y-%m-%d %H:%M:%S %Z')
            except Exception:
                # 如果格式化失败，使用 UTC 时间戳作为回退
                time_str = f"UTC Timestamp {timestamp}"

            summary_list.append({
                "description": desc,
                "scheduled_time_utc8": time_str
            })
        return summary_list

    async def run_scheduler(self):
        """后台调度循环，持续检查并执行到期的任务。"""
        logger.info("任务调度器后台循环启动...")
        while True:
            try:
                # 如果没有待处理任务，休眠一段时间后继续检查
                if not self.scheduled_tasks:
                    await asyncio.sleep(5); continue # 列表为空，等待5秒

                # 获取下一个任务的时间戳 (列表已排序，第一个即为最早的任务)
                next_task_timestamp = self.scheduled_tasks[0][0]
                now_timestamp = datetime.now(timezone.utc).timestamp()

                # 如果下一个任务的时间还没到
                if next_task_timestamp > now_timestamp:
                    # 计算需要休眠的时间
                    sleep_duration = max(0.1, next_task_timestamp - now_timestamp)
                    # 休眠直到下一个任务即将到期，但最长不超过60秒 (避免长时间阻塞)
                    await asyncio.sleep(min(sleep_duration, 60)); continue
                
                # --- 执行到期任务 --- #
                # 从列表中移除并获取第一个任务 (已到期)
                timestamp, task_description, callback_info = self.scheduled_tasks.pop(0)
                
                global _task_callback
                # 检查回调函数是否存在且是异步函数
                if _task_callback and inspect.iscoroutinefunction(_task_callback):
                    handler_name = getattr(_task_callback, '__name__', repr(_task_callback))
                    # 显示原定 UTC+8 时间
                    try:
                        local_time_str = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(UTC_PLUS_8).strftime('%Y-%m-%d %H:%M:%S %z')
                    except Exception:
                        local_time_str = f"timestamp {timestamp}"
                    # logger.info(f"执行任务: '{task_description}' (原定于 {datetime.fromtimestamp(timestamp, timezone.utc).isoformat()}) 使用处理器: {handler_name}")
                    logger.info(f"执行任务: '{task_description}' (原定于 {local_time_str}) 使用处理器: {handler_name}")
                    try:
                        # 使用 asyncio.create_task 异步执行回调，避免阻塞调度循环
                        asyncio.create_task(_task_callback(task_description, callback_info))
                    except Exception as e:
                        # 捕获并记录回调执行中的错误
                        logger.error(f"执行任务回调 '{task_description}' ({handler_name}) 时出错: {e}", exc_info=True)
                else:
                    # 如果没有有效回调，记录严重错误
                    logger.error(f"CRITICAL: 任务 '{task_description}' 到期，但没有有效的异步回调函数设置! _task_callback is {_task_callback}")
                    print(f"--- CRITICAL ERROR: NO TASK CALLBACK SET for task '{task_description}' ---")
                
                await asyncio.sleep(0.01) # 短暂休眠，避免 CPU 占用过高
            except asyncio.CancelledError:
                # 捕获取消信号，优雅退出循环
                logger.info("任务调度器后台循环被取消。"); break
            except Exception as e:
                # 捕获调度循环中的其他严重错误，记录并休眠后重试
                logger.error(f"任务调度器后台循环发生严重错误: {e}", exc_info=True); await asyncio.sleep(15)

# --- ScheduleTaskTool 类 (自动判断模式) --- #
class ScheduleTaskTool(BaseTool):
    name = "schedule_task"
    description = ("当你需要处理未来的提醒或计划时，无论是响应别人的请求（比如'五分钟后提醒我'），还是你自己需要记住完成当前事情后做某事（比如估算完成时间后提醒别人），你必须使用这个功能。\n"
                   "如果想更新一个已存在的任务，需要告诉我你想更新的是哪件事和新的执行时间（默认会覆盖旧安排）。\n"
                   "如果想取消一个任务，只需要告诉我你想取消的是哪件事就行了。\n"#目前为准确描述，效果不佳，需要改进
                   "它能听懂很多时间说法，像是'五分钟后'、'明天早上'、'下周一'等等。") # 微调描述，暗示可基于自身状态使用
    parameters = {
        "type": "object",
        "properties": {
            "time_description": { # 来自图片
                "type": "string",
                "description": "描述任务应该何时执行的时间。应尽可能使用自然语言，例如 '五分钟后', '明天早上8点', '下周一上午10点'。",
            },
            "task_description": { # 来自图片
                "type": "string",
                "description": "对到时间时需要执行的任务或提醒内容的简短、唯一的描述。这个描述也用于后续可能需要取消任务时的标识符。",
            },
            "context": { # 修改描述
                "type": "string",
                "description": "为任务执行提供附加上下文信息。例如，提醒用户的具体内容，或继续某个流程所需的状态信息。",
            },
            "update_existing": { # 修改描述并移除 default
                "type": "boolean",
                "description": "是否允许新任务覆盖具有完全相同描述的旧任务。默认为 `true` (允许覆盖)。如果设为 `false`，则添加操作会失败。",
            }
        },
        "required": ["task_description"],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """根据提供的参数自动判断并执行安排、更新或取消计划任务的动作。"""
        try:
            task_desc = function_args.get("task_description")
            time_desc = function_args.get("time_description") # 获取可选的时间描述
            context_from_llm = function_args.get("context") # 获取可选的上下文
            update_existing = function_args.get("update_existing", True) # 获取 update_existing 参数，现在默认行为是 True (允许覆盖)
            scheduler = TaskScheduler.get_instance()
            
            # --- 日志：记录收到的参数 --- #
            logger.info(f"[{self.name}] 接收到参数: task_description='{task_desc}', time_description='{time_desc}', context='{context_from_llm}', update_existing={update_existing}")

            if not task_desc:
                logger.warning(f"[{self.name}] 调用缺少必需的 task_description。")
                return {"name": self.name, "content": "操作失败：必须提供任务描述 (task_description)。"}

            # --- 判断操作模式 ---            
            if time_desc is None:
                # --- 取消任务流程 --- #
                logger.info(f"[{self.name}] 判断意图为：[取消任务]")
                logger.debug(f"[{self.name}] 计划调用 cancel_task_by_description，使用精确描述: '{task_desc}'")
                # --- Placeholder: 这里理想情况下应该由调用者先完成LLM识别，获取精确 task_desc --- #
                logger.debug(f"[{self.name}] (注意：当前直接使用提供的描述进行精确匹配取消)")
                
                cancelled_count = await scheduler.cancel_task_by_description(task_desc)
                
                if cancelled_count > 0:
                    content = f"好的，已成功取消 {cancelled_count} 个描述为 '{task_desc}' 的待执行任务。"
                else:
                    content = f"抱歉，未能找到描述为 '{task_desc}' 的待执行任务，无法取消。"
                return {"name": self.name, "content": content}

            else:
                # --- 添加或更新任务流程 --- #
                intent = "更新任务" if update_existing else "添加任务 (若同名则失败)"
                logger.info(f"[{self.name}] 判断意图为：[{intent}] (基于 time_description 存在)")
                # --- Placeholder: 对于 '更新任务'，这里理想情况下应该由调用者先完成LLM识别，获取精确 task_desc --- #
                if update_existing:
                     logger.debug(f"[{self.name}] (注意：当前直接使用提供的描述进行精确匹配更新)")
                
                logger.debug(f"[{self.name}] 计划调用 add_task，参数: time='{time_desc}', desc='{task_desc}', allow_overwrite={update_existing}")
                
                callback_info = {
                        "context": context_from_llm if context_from_llm is not None else "", 
                        "original_message_triggering_tool": message_txt,
                        "tool_args": function_args.copy()
                }

                success, add_status, target_dt_iso = await scheduler.add_task(
                    time_desc, task_desc, callback_info, allow_overwrite=update_existing
                )

                if success:
                    time_str_for_reply = "稍后" # 默认回退值
                    if target_dt_iso:
                        try:
                            target_dt = datetime.fromisoformat(target_dt_iso.replace('Z', '+00:00'))
                            display_tz = UTC_PLUS_8
                            target_dt_display = target_dt.astimezone(display_tz)
                            now_local = datetime.now(display_tz)
                            delta = target_dt_display - now_local
                            # 尝试提供更友好的时间格式
                            if timedelta(minutes=0) < delta <= timedelta(hours=2): # 2小时内
                                time_str_for_reply = f"{target_dt_display.strftime('%H:%M:%S %Z')} (大约 {int(delta.total_seconds() // 60)} 分钟后)"
                            elif timedelta(hours=2) < delta <= timedelta(days=1) and target_dt_display.date() == now_local.date(): # 今天晚些时候
                                 time_str_for_reply = f"今天 {target_dt_display.strftime('%H:%M:%S %Z')}"
                            else: # 其他情况（例如明天或更远）使用标准格式
                                time_str_for_reply = target_dt_display.strftime("%Y-%m-%d %H:%M:%S %Z") # 使用 %Z 显示时区名称
                        except Exception as fmt_err:
                            logger.warning(f"格式化回复时间 (UTC+8) 时出错: {fmt_err}. 回退到 UTC ISO。")
                            time_str_for_reply = target_dt_iso # 回退到 UTC ISO 字符串
                    else:
                         # 理论上，如果 success 为 True，target_dt_iso 不应为 None
                         logger.error(f"任务安排成功，但 target_dt_iso 丢失！任务描述: {task_desc}")
                         time_str_for_reply = f"在 '{time_desc}' 解析后的某个时间"
                        
                    if add_status == "OVERWRITTEN":
                        content = f"好的，已将现有任务 '{task_desc}' 的执行时间更新为 {time_str_for_reply}。" 
                    else: 
                        content = f"好的，已安排新任务 '{task_desc}' 在 {time_str_for_reply} 执行。" 
                else:
                    content = f"抱歉，无法安排或更新任务 '{task_desc}'。可能原因：时间描述 ('{time_desc}') 无法被理解、格式错误、时间已过去，或内部解析/添加错误。"

                return {"name": self.name, "content": content}
        
        except Exception as e:
            error_details = traceback.format_exc()
            logger.error(f"执行 {self.name} 时发生未预期错误: {str(e)}\n{error_details}")
            return {"name": self.name, "content": f"处理计划任务时遇到内部错误: {str(e)}"}

# --- 文件末尾说明 --- #
# 重要提示：
# 1. TaskScheduler 使用单例模式。首次调用 TaskScheduler.get_instance() 会自动初始化并启动后台调度循环。
# 2. 默认情况下，任务到期时仅会记录日志。要执行实际操作（如发送消息），必须在应用程序启动时调用 set_task_callback(your_async_handler) 来设置一个自定义的异步回调函数。
# 3. 此模块依赖第三方库 dateparser
# 4. 此模块内部时间处理已硬编码为 UTC+8 作为本地时区，问就是未知原因使得输出总是默认的UTC时区，配置里的调用过不知道为什么没用。。。逃.jpg


#现在这个工具只能实现短期任务安排，长期任务计划安排需要使用其他工具
#这里指的是单此启动的短期任务，所以。。。

#目前逻辑是在开启这个工具时候，判断三种，设置任务，更新任务，取消任务，通过llm判断是否更新或者取消(不稳定)，然后执行后续操作
#过于依赖dateparser，如果dateparser无法解析，添加了一堆自定义时间来识图解析，为什么不能直接返回一个时间QAQ
#很大一部分代码是自定义解决dateparser无法解析问题的，，，，




#graph TD
#    A[用户/对话触发意图] --> B(LLM 思考);
#    B --> C{LLM 产生初步意图<br>(添加/更新/取消)<br>(更新/取消描述可能模糊)};
#    C --> E[调用者 (如 SubMind) 处理意图];

#    subgraph "识别精确描述 (调用者逻辑)"
#        E -- 意图是更新或取消 --> F{获取当前任务列表<br>(TaskScheduler.get_scheduled_tasks_summary())};
#        F --> G[构建二次 Prompt<br>(上下文+模糊描述+任务列表)];
#        G --> H(调用 LLM 进行识别);
#        H --> I{LLM 返回精确 task_description 或 '未找到'};
#    end

#    I -- 精确描述 --> J[准备工具调用参数<br>(使用精确 task_description)];
#    I -- 未找到 --> K[处理错误/告知用户];
#    E -- 意图是添加 --> J[准备工具调用参数<br>(使用原始 task_description)];

#    J --> L[执行 schedule_task 工具调用];
#    L --> M(ScheduleTaskTool.execute);
#    M --> N{检查 time_description 是否存在?};
#    N -- 是 --> O[判断为 添加/更新];
#    N -- 否 --> P[判断为 取消];

#    O --> Q{TaskScheduler.add_task<br>(使用精确 task_description 查找旧任务)};
#    P --> R{TaskScheduler.cancel_task_by_description<br>(使用精确 task_description 查找任务)};

#    Q --> S{执行成功/失败<br>(精确匹配成功率高)};
#    R --> T{执行成功/失败<br>(精确匹配成功率高)};

#    S --> U(返回结果给 LLM);
#    T --> U;
#    K --> U;
