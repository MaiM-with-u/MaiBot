#定义了来自外部世界的信息
import asyncio
from datetime import datetime
from src.plugins.models.utils_model import LLM_request
from src.plugins.config.config import global_config
from src.common.database import db

#存储一段聊天的大致内容
class Talking_info:
    def __init__(self,chat_id):
        self.chat_id = chat_id
        self.talking_message = []
        self.talking_message_str = ""
        self.talking_summary = ""
        self.last_observe_time = int(datetime.now().timestamp()) #初始化为当前时间
        self.observe_times = 0
        self.activate = 360
        
        self.last_summary_time = int(datetime.now().timestamp())  # 上次更新summary的时间
        self.summary_count = 0  # 30秒内的更新次数
        self.max_update_in_30s = 2
        
        self.oberve_interval = 3
        
        self.llm_summary = LLM_request(
            model=global_config.llm_outer_world, temperature=0.7, max_tokens=300, request_type="outer_world")
    
    async def start_observe(self):
        while True:
            if self.activate <= 0:
                print(f"聊天 {self.chat_id} 活跃度不足，进入休眠状态")
                await self.waiting_for_activate()
                print(f"聊天 {self.chat_id} 被重新激活")
            await self.observe_world()
            await asyncio.sleep(self.oberve_interval)
    
    async def waiting_for_activate(self):
        while True:
            # 检查从上次观察时间之后的新消息数量
            new_messages_count = db.messages.count_documents({
                "chat_id": self.chat_id,
                "time": {"$gt": self.last_observe_time}
            })
            
            if new_messages_count > 15:
                self.activate = 360*(self.observe_times+1)
                return
            
            await asyncio.sleep(8)  # 每10秒检查一次
    
    async def observe_world(self):
        # 查找新消息，限制最多20条
        new_messages = list(db.messages.find({
            "chat_id": self.chat_id,
            "time": {"$gt": self.last_observe_time}
        }).sort("time", 1).limit(20))  # 按时间正序排列，最多20条
        
        if not new_messages:
            self.activate += -1
            return
            
        # 将新消息添加到talking_message，同时保持列表长度不超过20条
        self.talking_message.extend(new_messages)
        if len(self.talking_message) > 20:
            self.talking_message = self.talking_message[-20:]  # 只保留最新的20条
        self.translate_message_list_to_str()
        self.observe_times += 1
        self.last_observe_time = new_messages[-1]["time"]
        
        # 检查是否需要更新summary
        current_time = int(datetime.now().timestamp())
        if current_time - self.last_summary_time >= 30:  # 如果超过30秒，重置计数
            self.summary_count = 0
            self.last_summary_time = current_time
            
        if self.summary_count < self.max_update_in_30s:  # 如果30秒内更新次数小于2次
            await self.update_talking_summary()
            self.summary_count += 1
    
    async def update_talking_summary(self):
        #基于已经有的talking_summary，和新的talking_message，生成一个summary
        # print(f"更新聊天总结：{self.talking_summary}")
        prompt = ""
        prompt = f"你正在参与一个qq群聊的讨论，这个群之前在聊的内容是：{self.talking_summary}\n"
        prompt += f"现在群里的群友们产生了新的讨论，有了新的发言，具体内容如下：{self.talking_message_str}\n"
        prompt += '''以上是群里在进行的聊天，请你对这个聊天内容进行总结，总结内容要包含聊天的大致内容，
        以及聊天中的一些重要信息，记得不要分点，不要太长，精简的概括成一段文本\n'''
        prompt += "总结概括："
        self.talking_summary, reasoning_content = await self.llm_summary.generate_response_async(prompt)
        
    def translate_message_list_to_str(self):
        self.talking_message_str = ""
        for message in self.talking_message:
            self.talking_message_str += message["detailed_plain_text"]
    
class SheduleInfo:
    def __init__(self):
        self.shedule_info = ""

class OuterWorld:
    def __init__(self):
        self.talking_info_list = [] #装的一堆talking_info
        self.shedule_info = "无日程"
        # self.interest_info = "麦麦你好"
        self.outer_world_info = ""
        self.start_time = int(datetime.now().timestamp())
    
        self.llm_summary = LLM_request(
            model=global_config.llm_outer_world, temperature=0.7, max_tokens=600, request_type="outer_world_info")  
    
    async def check_and_add_new_observe(self):
        # 获取所有聊天流
        all_streams = db.chat_streams.find({})
        # 遍历所有聊天流
        for data in all_streams:         
            stream_id = data.get("stream_id")
            # 检查是否已存在该聊天流的观察对象
            existing_info = next((info for info in self.talking_info_list if info.chat_id == stream_id), None)
            
            # 如果不存在，创建新的Talking_info对象并添加到列表中
            if existing_info is None:
                print(f"发现新的聊天流: {stream_id}")
                new_talking_info = Talking_info(stream_id)
                self.talking_info_list.append(new_talking_info)
                # 启动新对象的观察任务
                asyncio.create_task(new_talking_info.start_observe())

    async def open_eyes(self):
        while True:
            print("检查新的聊天流")
            await self.check_and_add_new_observe()
            await asyncio.sleep(60)
    
    def get_world_by_stream_id(self,stream_id):
        for talking_info in self.talking_info_list:
            if talking_info.chat_id == stream_id:
                return talking_info
        return None


outer_world = OuterWorld()

if __name__ == "__main__":
    asyncio.run(outer_world.open_eyes())
