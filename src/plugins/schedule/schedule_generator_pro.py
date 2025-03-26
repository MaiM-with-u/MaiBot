import datetime
import json
import re
import os
import sys
from typing import Dict, Union
# 添加项目根目录到 Python 路径
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.append(root_path)

from src.common.database import db # noqa: E402
from src.common.logger import get_module_logger # noqa: E402
from src.plugins.schedule.offline_llm import LLMModel # noqa: E402

logger = get_module_logger("scheduler")


class ScheduleGenerator:
    enable_output: bool = True

    def __init__(self, name: str = "bot_name", personality: str = "你是一个爱国爱党的新时代青年", behavior: str = "你非常外向，喜欢尝试新事物和人交流"):
        # 使用离线LLM模型
        self.llm_scheduler = LLMModel(model_name="Pro/deepseek-ai/DeepSeek-V3", temperature=0.9)
        
        self.today_schedule_text = ""
        self.today_done_list = []

        self.yesterday_schedule_text = ""
        self.yesterday_done_list = []

        self.name = name
        self.personality = personality
        self.behavior = behavior

        self.start_time = datetime.datetime.now()

    async def mai_schedule_start(self):
        """启动日程系统，每5分钟执行一次move_doing，并在日期变化时重新检查日程"""
        try:
            logger.info(f"日程系统启动/刷新时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            # 初始化日程
            await self.check_and_create_today_schedule()
            self.print_schedule()
            
            while True:
                current_time = datetime.datetime.now()
                
                # 检查是否需要重新生成日程（日期变化）
                if current_time.date() != self.start_time.date():
                    logger.info("检测到日期变化，重新生成日程")
                    self.start_time = current_time
                    await self.check_and_create_today_schedule()
                    self.print_schedule()
                
                # 执行当前活动
                current_activity = await self.move_doing()
                logger.info(f"当前活动: {current_activity}")
                
                # 等待5分钟
                await asyncio.sleep(300)  # 300秒 = 5分钟
                
        except Exception as e:
            logger.error(f"日程系统运行时出错: {str(e)}")
            logger.exception("详细错误信息：")

    async def check_and_create_today_schedule(self):
        """检查昨天的日程，并确保今天有日程安排
        
        Returns:
            tuple: (today_schedule_text, today_schedule) 今天的日程文本和解析后的日程字典
        """
        today = datetime.datetime.now()
        yesterday = today - datetime.timedelta(days=1)
        
        # 先检查昨天的日程
        self.yesterday_schedule_text, self.yesterday_done_list = self.load_schedule_from_db(yesterday)
        if self.yesterday_schedule_text:
            logger.debug(f"已加载{yesterday.strftime('%Y-%m-%d')}的日程")
        
        # 检查今天的日程
        self.today_schedule_text, self.today_done_list = self.load_schedule_from_db(today)
        if not self.today_schedule_text:
            logger.info(f"{today.strftime('%Y-%m-%d')}的日程不存在，准备生成新的日程")
            self.today_schedule_text = await self.generate_daily_schedule(target_date=today)

        self.save_today_schedule_to_db()
    
    def construct_daytime_prompt(self, target_date: datetime.datetime):
        date_str = target_date.strftime("%Y-%m-%d")
        weekday = target_date.strftime("%A")

        prompt =  f"我是{self.name}，{self.personality}，{self.behavior}"
        prompt += f"我昨天的日程是：{self.yesterday_schedule_text}\n"
        prompt += f"请为我生成{date_str}（{weekday}）的日程安排，结合我的个人特点和行为习惯\n"
        prompt += "推测我的日程安排，包括我一天都在做什么，有什么发现和思考，具体一些，详细一些，记得写明时间\n"
        prompt += "直接返回我的日程，不要输出其他内容："
        return prompt
    
    def construct_doing_prompt(self,time: datetime.datetime):
        now_time = time.strftime("%H:%M")
        previous_doing = self.today_done_list[-20:] if len(self.today_done_list) > 20 else self.today_done_list
        prompt = f"我是{self.name}，{self.personality}，{self.behavior}"
        prompt += f"我今天的日程是：{self.today_schedule_text}\n"
        prompt += f"我之前做了的事情是：{previous_doing}\n"
        prompt += f"现在是{now_time}，结合我的个人特点和行为习惯,"
        prompt += "推测我现在做什么，具体一些，详细一些\n"
        prompt += "直接返回我在做的事情，不要输出其他内容："
        return prompt
    
    async def generate_daily_schedule(
        self, target_date: datetime.datetime = None,) -> Dict[str, str]:
        daytime_prompt = self.construct_daytime_prompt(target_date)
        daytime_response, _ = await self.llm_scheduler.generate_response(daytime_prompt)
        return daytime_response

    def _time_diff(self, time1: str, time2: str) -> int:
        """计算两个时间字符串之间的分钟差"""
        if time1 == "24:00":
            time1 = "23:59"
        if time2 == "24:00":
            time2 = "23:59"
        t1 = datetime.datetime.strptime(time1, "%H:%M")
        t2 = datetime.datetime.strptime(time2, "%H:%M")
        diff = int((t2 - t1).total_seconds() / 60)
        # 考虑时间的循环性
        if diff < -720:
            diff += 1440  # 加一天的分钟
        elif diff > 720:
            diff -= 1440  # 减一天的分钟
        # print(f"时间1[{time1}]: 时间2[{time2}]，差值[{diff}]分钟")
        return diff

    def print_schedule(self):
        """打印完整的日程安排"""
        if not self.today_schedule_text:
            logger.warning("今日日程有误，将在下次运行时重新生成")
            db.schedule.delete_one({"date": datetime.datetime.now().strftime("%Y-%m-%d")})
        else:
            logger.info("=== 今日日程安排 ===")
            logger.info(self.today_schedule_text)
            logger.info("==================")
            self.enable_output = False

    async def update_today_done_list(self):
        # 更新数据库中的 today_done_list
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        existing_schedule = db.schedule.find_one({"date": today_str})
        
        if existing_schedule:
            # 更新数据库中的 today_done_list
            db.schedule.update_one(
                {"date": today_str},
                {"$set": {"today_done_list": self.today_done_list}}
            )
            logger.debug(f"已更新{today_str}的已完成活动列表")
        else:
            logger.warning(f"未找到{today_str}的日程记录")

    async def move_doing(self):
        current_time = datetime.datetime.now()
        time_str = current_time.strftime("%H:%M")
        doing_prompt = self.construct_doing_prompt(current_time)
        doing_response, _ = await self.llm_scheduler.generate_response(doing_prompt)
        self.today_done_list.append(current_time,time_str + "在" + doing_response)

        await self.update_today_done_list()
        
        return doing_response




    def save_today_schedule_to_db(self):
        """保存日程到数据库，同时初始化 today_done_list"""
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        schedule_data = {
            "date": date_str,
            "schedule": self.today_schedule_text,
            "today_done_list": self.today_done_list if hasattr(self, 'today_done_list') else []
        }
        # 使用 upsert 操作，如果存在则更新，不存在则插入
        db.schedule.update_one(
            {"date": date_str},
            {"$set": schedule_data},
            upsert=True
        )
        logger.debug(f"已保存{date_str}的日程到数据库")

    def load_schedule_from_db(self, date: datetime.datetime):
        """从数据库加载日程，同时加载 today_done_list"""
        date_str = date.strftime("%Y-%m-%d")
        existing_schedule = db.schedule.find_one({"date": date_str})
        
        if existing_schedule:
            schedule_text = existing_schedule["schedule"]
            return schedule_text, existing_schedule.get("today_done_list", [])
        else:
            logger.debug(f"{date_str}的日程不存在")
            return None, None

async def main():
    # 使用示例
    scheduler = ScheduleGenerator(name="麦麦", personality="你叫麦麦，你19岁，是一个大二的女大学生，你有一头黑色短发，你会刷贴吧，你现在在学习心理学", behavior="你比较内向")
    await scheduler.check_and_create_today_schedule()
    scheduler.print_schedule()
    print("\n当前任务：")
    print(await scheduler.get_current_task())

    print("昨天日程：")
    print(scheduler.yesterday_schedule)
    print("今天日程：")
    print(scheduler.today_schedule)
    print("明天日程：")
    print(scheduler.tomorrow_schedule)

# 当作为组件导入时使用的实例
bot_schedule = ScheduleGenerator()

if __name__ == "__main__":
    import asyncio
    # 当直接运行此文件时执行
    asyncio.run(main())
