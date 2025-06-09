import time
import random
from typing import List, Dict, Optional, Any, Tuple
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_random, build_anonymous_messages
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
import os
from src.chat.message_receive.chat_stream import chat_manager
import json


MAX_EXPRESSION_COUNT = 300
DECAY_DAYS = 30  # 30天衰减到0.01
DECAY_MIN = 0.01  # 最小衰减值

logger = get_logger("expressor")


def init_prompt() -> None:
    learn_style_prompt = """
{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语言风格
1. 只考虑文字，不要考虑表情包和图片
2. 不要涉及具体的人名，只考虑语言风格
3. 语言风格包含特殊内容和情感
4. 思考有没有特殊的梗，一并总结成语言风格
5. 例子仅供参考，请严格根据群聊内容总结!!!
注意：总结成如下格式的规律，总结的内容要详细，但具有概括性：
当"xxxxxx"时，可以"xxxxxx", xxxxxx不超过20个字

例如：
当"对某件事表示十分惊叹，有些意外"时，使用"我嘞个xxxx"
当"表示讽刺的赞同，不想讲道理"时，使用"对对对"
当"想说明某个具体的事实观点，但懒得明说，或者不便明说，或表达一种默契"，使用"懂的都懂"
当"当涉及游戏相关时，表示意外的夸赞，略带戏谑意味"时，使用"这么强！"

注意不要总结你自己（SELF）的发言
现在请你概括
"""
    Prompt(learn_style_prompt, "learn_style_prompt")

    learn_grammar_prompt = """
{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语法和句法特点，只考虑纯文字，不要考虑表情包和图片
1.不要总结【图片】，【动画表情】，[图片]，[动画表情]，不总结 表情符号 at @ 回复 和[回复]
2.不要涉及具体的人名，只考虑语法和句法特点,
3.语法和句法特点要包括，句子长短（具体字数），有何种语病，如何拆分句子。
4. 例子仅供参考，请严格根据群聊内容总结!!!
总结成如下格式的规律，总结的内容要简洁，不浮夸：
当"xxx"时，可以"xxx"

例如：
当"表达观点较复杂"时，使用"省略主语(3-6个字)"的句法
当"不用详细说明的一般表达"时，使用"非常简洁的句子"的句法
当"需要单纯简单的确认"时，使用"单字或几个字的肯定(1-2个字)"的句法

注意不要总结你自己（SELF）的发言
现在请你概括
"""
    Prompt(learn_grammar_prompt, "learn_grammar_prompt")


class ExpressionLearner:
    def __init__(self) -> None:
        # TODO: API-Adapter修改标记
        self.express_learn_model: LLMRequest = LLMRequest(
            model=global_config.model.replyer_1,
            temperature=0.1,
            request_type="expressor.learner",
        )

    async def get_expression_by_chat_id(self, chat_id: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """
        读取/data/expression/learnt/{chat_id}/expressions.json和/data/expression/personality/expressions.json
        返回(learnt_expressions, personality_expressions)
        """
        learnt_style_file = os.path.join("data", "expression", "learnt_style", str(chat_id), "expressions.json")
        learnt_grammar_file = os.path.join("data", "expression", "learnt_grammar", str(chat_id), "expressions.json")
        personality_file = os.path.join("data", "expression", "personality", "expressions.json")
        learnt_style_expressions = []
        learnt_grammar_expressions = []
        personality_expressions = []
        if os.path.exists(learnt_style_file):
            with open(learnt_style_file, "r", encoding="utf-8") as f:
                learnt_style_expressions = json.load(f)
        if os.path.exists(learnt_grammar_file):
            with open(learnt_grammar_file, "r", encoding="utf-8") as f:
                learnt_grammar_expressions = json.load(f)
        if os.path.exists(personality_file):
            with open(personality_file, "r", encoding="utf-8") as f:
                personality_expressions = json.load(f)
        return learnt_style_expressions, learnt_grammar_expressions, personality_expressions

    def is_similar(self, s1: str, s2: str) -> bool:
        """
        判断两个字符串是否相似（只考虑长度大于5且有80%以上重合，不考虑子串）
        """
        if not s1 or not s2:
            return False
        min_len = min(len(s1), len(s2))
        if min_len < 5:
            return False
        same = sum(1 for a, b in zip(s1, s2) if a == b)
        return same / min_len > 0.8

    async def learn_and_store_expression(self) -> List[Tuple[str, str, str]]:
        """
        学习并存储表达方式，分别学习语言风格和句法特点
        同时对所有已存储的表达方式进行全局衰减
        """
        current_time = time.time()
        
        # 全局衰减所有已存储的表达方式
        for type in ["style", "grammar"]:
            base_dir = os.path.join("data", "expression", f"learnt_{type}")
            if not os.path.exists(base_dir):
                continue
                
            for chat_id in os.listdir(base_dir):
                file_path = os.path.join(base_dir, chat_id, "expressions.json")
                if not os.path.exists(file_path):
                    continue
                    
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        expressions = json.load(f)
                    
                    # 应用全局衰减
                    decayed_expressions = self.apply_decay_to_expressions(expressions, current_time)
                    
                    # 保存衰减后的结果
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(decayed_expressions, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error(f"全局衰减{type}表达方式失败: {e}")
                    continue

        # 学习新的表达方式（这里会进行局部衰减）
        for i in range(3):
            learnt_style: Optional[List[Tuple[str, str, str]]] = await self.learn_and_store(type="style", num=25)
            if not learnt_style:
                return []

        for j in range(1):
            learnt_grammar: Optional[List[Tuple[str, str, str]]] = await self.learn_and_store(type="grammar", num=10)
            if not learnt_grammar:
                return []

        return learnt_style, learnt_grammar

    def calculate_decay_factor(self, time_diff_days: float) -> float:
        """
        计算衰减值
        当时间差为0天时，衰减值为0.001
        当时间差为7天时，衰减值为0
        当时间差为30天时，衰减值为0.001
        使用二次函数进行曲线插值
        """
        if time_diff_days <= 0 or time_diff_days >= DECAY_DAYS:
            return 0.001
            
        # 使用二次函数进行插值
        # 将7天作为顶点，0天和30天作为两个端点
        # 使用顶点式：y = a(x-h)^2 + k，其中(h,k)为顶点
        h = 7.0  # 顶点x坐标
        k = 0.001  # 顶点y坐标
        
        # 计算a值，使得x=0和x=30时y=0.001
        # 0.001 = a(0-7)^2 + 0.001
        # 解得a = 0
        a = 0
        
        # 计算衰减值
        decay = a * (time_diff_days - h) ** 2 + k
        return min(0.001, decay)

    def apply_decay_to_expressions(self, expressions: List[Dict[str, Any]], current_time: float) -> List[Dict[str, Any]]:
        """
        对表达式列表应用衰减
        返回衰减后的表达式列表，移除count小于0的项
        """
        result = []
        for expr in expressions:
            # 确保last_active_time存在，如果不存在则使用current_time
            if "last_active_time" not in expr:
                expr["last_active_time"] = current_time
                
            last_active = expr["last_active_time"]
            time_diff_days = (current_time - last_active) / (24 * 3600)  # 转换为天
            
            decay_value = self.calculate_decay_factor(time_diff_days)
            expr["count"] = max(0.01, expr.get("count", 1) - decay_value)
            
            if expr["count"] > 0:
                result.append(expr)
        
        return result

    async def learn_and_store(self, type: str, num: int = 10) -> List[Tuple[str, str, str]]:
        """
        选择从当前到最近1小时内的随机num条消息，然后学习这些消息的表达方式
        type: "style" or "grammar"
        """
        if type == "style":
            type_str = "语言风格"
        elif type == "grammar":
            type_str = "句法特点"
        else:
            raise ValueError(f"Invalid type: {type}")
        
        res = await self.learn_expression(type, num)

        if res is None:
            return []
        learnt_expressions, chat_id = res

        chat_stream = chat_manager.get_stream(chat_id)
        if chat_stream.group_info:
            group_name = chat_stream.group_info.group_name
        else:
            group_name = f"{chat_stream.user_info.user_nickname}的私聊"
        learnt_expressions_str = ""
        for _chat_id, situation, style in learnt_expressions:
            learnt_expressions_str += f"{situation}->{style}\n"
        logger.info(f"在 {group_name} 学习到{type_str}:\n{learnt_expressions_str}")

        if not learnt_expressions:
            logger.info(f"没有学习到{type_str}")
            return []

        # 按chat_id分组
        chat_dict: Dict[str, List[Dict[str, str]]] = {}
        for chat_id, situation, style in learnt_expressions:
            if chat_id not in chat_dict:
                chat_dict[chat_id] = []
            chat_dict[chat_id].append({"situation": situation, "style": style})
        
        current_time = time.time()
        
        # 存储到/data/expression/对应chat_id/expressions.json
        for chat_id, expr_list in chat_dict.items():
            dir_path = os.path.join("data", "expression", f"learnt_{type}", str(chat_id))
            os.makedirs(dir_path, exist_ok=True)
            file_path = os.path.join(dir_path, "expressions.json")
            
            # 若已存在，先读出合并
            old_data: List[Dict[str, Any]] = []
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                except Exception:
                    old_data = []
            
            # 应用衰减
            # old_data = self.apply_decay_to_expressions(old_data, current_time)
            
            # 合并逻辑
            for new_expr in expr_list:
                found = False
                for old_expr in old_data:
                    if self.is_similar(new_expr["situation"], old_expr.get("situation", "")) and self.is_similar(
                        new_expr["style"], old_expr.get("style", "")
                    ):
                        found = True
                        # 50%概率替换
                        if random.random() < 0.5:
                            old_expr["situation"] = new_expr["situation"]
                            old_expr["style"] = new_expr["style"]
                        old_expr["count"] = old_expr.get("count", 1) + 1
                        old_expr["last_active_time"] = current_time
                        break
                if not found:
                    new_expr["count"] = 1
                    new_expr["last_active_time"] = current_time
                    old_data.append(new_expr)
            
            # 处理超限问题
            if len(old_data) > MAX_EXPRESSION_COUNT:
                # 计算每个表达方式的权重（count的倒数，这样count越小的越容易被选中）
                weights = [1 / (expr.get("count", 1) + 0.1) for expr in old_data]
                
                # 随机选择要移除的表达方式，避免重复索引
                remove_count = len(old_data) - MAX_EXPRESSION_COUNT
                
                # 使用一种不会选到重复索引的方法
                indices = list(range(len(old_data)))
                
                # 方法1：使用numpy.random.choice
                # 把列表转成一个映射字典，保证不会有重复
                remove_set = set()
                total_attempts = 0
                
                # 尝试按权重随机选择，直到选够数量
                while len(remove_set) < remove_count and total_attempts < len(old_data) * 2:
                    idx = random.choices(indices, weights=weights, k=1)[0]
                    remove_set.add(idx)
                    total_attempts += 1
                
                # 如果没选够，随机补充
                if len(remove_set) < remove_count:
                    remaining = set(indices) - remove_set
                    remove_set.update(random.sample(list(remaining), remove_count - len(remove_set)))
                
                remove_indices = list(remove_set)
                
                # 从后往前删除，避免索引变化
                for idx in sorted(remove_indices, reverse=True):
                    old_data.pop(idx)
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(old_data, f, ensure_ascii=False, indent=2)
        
        return learnt_expressions

    async def learn_expression(self, type: str, num: int = 10) -> Optional[Tuple[List[Tuple[str, str, str]], str]]:
        """选择从当前到最近1小时内的随机num条消息，然后学习这些消息的表达方式

        Args:
            type: "style" or "grammar"
        """
        if type == "style":
            type_str = "语言风格"
            prompt = "learn_style_prompt"
        elif type == "grammar":
            type_str = "句法特点"
            prompt = "learn_grammar_prompt"
        else:
            raise ValueError(f"Invalid type: {type}")

        current_time = time.time()
        random_msg: Optional[List[Dict[str, Any]]] = get_raw_msg_by_timestamp_random(
            current_time - 3600 * 24, current_time, limit=num
        )
        # print(random_msg)
        if not random_msg or random_msg == []:
            return None
        # 转化成str
        chat_id: str = random_msg[0]["chat_id"]
        # random_msg_str: str = build_readable_messages(random_msg, timestamp_mode="normal")
        random_msg_str: str = await build_anonymous_messages(random_msg)
        # print(f"random_msg_str:{random_msg_str}")

        prompt: str = await global_prompt_manager.format_prompt(
            prompt,
            chat_str=random_msg_str,
        )

        logger.debug(f"学习{type_str}的prompt: {prompt}")

        try:
            response, _ = await self.express_learn_model.generate_response_async(prompt)
        except Exception as e:
            logger.error(f"学习{type_str}失败: {e}")
            return None

        logger.debug(f"学习{type_str}的response: {response}")

        expressions: List[Tuple[str, str, str]] = self.parse_expression_response(response, chat_id)

        return expressions, chat_id

    def parse_expression_response(self, response: str, chat_id: str) -> List[Tuple[str, str, str]]:
        """
        解析LLM返回的表达风格总结，每一行提取"当"和"使用"之间的内容，存储为(situation, style)元组
        """
        expressions: List[Tuple[str, str, str]] = []
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            # 查找"当"和下一个引号
            idx_when = line.find('当"')
            if idx_when == -1:
                continue
            idx_quote1 = idx_when + 1
            idx_quote2 = line.find('"', idx_quote1 + 1)
            if idx_quote2 == -1:
                continue
            situation = line[idx_quote1 + 1 : idx_quote2]
            # 查找"使用"
            idx_use = line.find('使用"', idx_quote2)
            if idx_use == -1:
                continue
            idx_quote3 = idx_use + 2
            idx_quote4 = line.find('"', idx_quote3 + 1)
            if idx_quote4 == -1:
                continue
            style = line[idx_quote3 + 1 : idx_quote4]
            expressions.append((chat_id, situation, style))
        return expressions


init_prompt()

expression_learner = ExpressionLearner()
