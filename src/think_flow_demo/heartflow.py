from .current_mind import SubHeartflow
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLM_request
from src.plugins.chat.config import global_config, BotConfig
from src.plugins.schedule.schedule_generator import bot_schedule
import asyncio
from src.common.logger import get_module_logger, LogConfig, HEARTFLOW_STYLE_CONFIG # noqa: E402

heartflow_config = LogConfig(
    # 使用海马体专用样式
    console_format=HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=HEARTFLOW_STYLE_CONFIG["file_format"],
)   
logger = get_module_logger("heartflow", config=heartflow_config)

class CuttentState:
    def __init__(self):
        self.willing = 0
        self.current_state_info = ""
        
        self.mood_manager = MoodManager()
        self.mood = self.mood_manager.get_prompt()
    
    def update_current_state_info(self):
        self.current_state_info = self.mood_manager.get_current_mood()

class Heartflow:
    def __init__(self):
        self.current_mind = "你什么也没想"
        self.past_mind = []
        self.current_state : CuttentState = CuttentState()
        self.llm_model = LLM_request(
            model=global_config.llm_heartflow, temperature=0.6, max_tokens=1000, request_type="heart_flow")
        
        self._subheartflows = {}
        self.active_subheartflows_nums = 0
        
        

    async def heartflow_start_working(self):
        while True:
            await self.do_a_thinking()
            await asyncio.sleep(600)
    
    async def do_a_thinking(self):
        logger.info("麦麦大脑袋转起来了")
        self.current_state.update_current_state_info()
        
        personality_info = " ".join(BotConfig.PROMPT_PERSONALITY)
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        related_memory_info = 'memory'
        sub_flows_info = await self.get_all_subheartflows_minds()
        
        schedule_info = bot_schedule.get_current_num_task(num = 5,time_info = True)
        
        prompt = ""
        prompt += f"你刚刚在做的事情是：{schedule_info}\n"
        prompt += f"{personality_info}\n"
        prompt += f"你想起来{related_memory_info}。"
        prompt += f"刚刚你的主要想法是{current_thinking_info}。"
        prompt += f"你还有一些小想法，因为你在参加不同的群聊天，是你正在做的事情：{sub_flows_info}\n"
        prompt += f"你现在{mood_info}。"
        prompt += "现在你接下去继续思考，产生新的想法，但是要基于原有的主要想法，不要分点输出，"
        prompt += "输出连贯的内心独白，不要太长，但是记得结合上述的消息，关注新内容:"
        
        reponse, reasoning_content = await self.llm_model.generate_response_async(prompt)
        
        self.update_current_mind(reponse)
        
        self.current_mind = reponse
        logger.info(f"麦麦的总体脑内状态：{self.current_mind}")
        logger.info("麦麦想了想，当前活动:")
        await bot_schedule.move_doing(self.current_mind)
        
        
        for _, subheartflow in self._subheartflows.items():
            subheartflow.main_heartflow_info = reponse

    def update_current_mind(self,reponse):
        self.past_mind.append(self.current_mind)
        self.current_mind = reponse
        
    
    
    async def get_all_subheartflows_minds(self):
        sub_minds = ""
        for _, subheartflow in self._subheartflows.items():
            sub_minds += subheartflow.current_mind
            
        return await self.minds_summary(sub_minds)
    
    async def minds_summary(self,minds_str):
        personality_info = " ".join(BotConfig.PROMPT_PERSONALITY)
        mood_info = self.current_state.mood
        
        prompt = ""
        prompt += f"{personality_info}\n"
        prompt += f"现在{global_config.BOT_NICKNAME}的想法是：{self.current_mind}\n"
        prompt += f"现在{global_config.BOT_NICKNAME}在qq群里进行聊天，聊天的话题如下：{minds_str}\n"
        prompt += f"你现在{mood_info}\n"
        prompt += '''现在请你总结这些聊天内容，注意关注聊天内容对原有的想法的影响，输出连贯的内心独白
        不要太长，但是记得结合上述的消息，要记得你的人设，关注新内容:'''

        reponse, reasoning_content = await self.llm_model.generate_response_async(prompt)

        return reponse
        
    def create_subheartflow(self, observe_chat_id):
        """创建一个新的SubHeartflow实例"""
        if observe_chat_id not in self._subheartflows:
            subheartflow = SubHeartflow()
            subheartflow.assign_observe(observe_chat_id)
            # 创建异步任务
            asyncio.create_task(subheartflow.subheartflow_start_working())
            self._subheartflows[observe_chat_id] = subheartflow
        return self._subheartflows[observe_chat_id]
    
    def get_subheartflow(self, observe_chat_id):
        """获取指定ID的SubHeartflow实例"""
        return self._subheartflows.get(observe_chat_id)


# 创建一个全局的管理器实例
subheartflow_manager = Heartflow() 
