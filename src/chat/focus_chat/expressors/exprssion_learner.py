import time
import random
from typing import List, Dict, Optional, Any, Tuple
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_random, build_anonymous_messages
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
import os
import json


MAX_EXPRESSION_COUNT = 100

logger = get_logger("expressor")


def init_prompt() -> None:
    learn_style_prompt = """
{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语言风格，只考虑文字，不要考虑表情包和图片
不要涉及具体的人名，只考虑语言风格
语言风格包含特殊内容和情感
思考有没有特殊的梗，一并总结成语言风格
总结成如下格式的规律，总结的内容要详细，但具有概括性：
当"xxx"时，可以"xxx", xxx不超过10个字

例如：
当"表示十分惊叹"时，使用"我嘞个xxxx"
当"表示讽刺的赞同，不想讲道理"时，使用"对对对"
当"想说明某个观点，但懒得明说"，使用"懂的都懂"

注意不要总结你自己的发言
现在请你概括
"""
    Prompt(learn_style_prompt, "learn_style_prompt")

    learn_grammar_prompt = """
{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语法和句法特点，只考虑纯文字，不要考虑表情包和图片
不要总结【图片】，【动画表情】，[图片]，[动画表情]，不总结 表情符号 at @ 回复 和[回复]
不要涉及具体的人名，只考虑语法和句法特点,
语法和句法特点要包括，句子长短（具体字数），有何种语病，如何拆分句子。
总结成如下格式的规律，总结的内容要简洁，不浮夸：
当"xxx"时，可以"xxx"

例如：
当"表达观点较复杂"时，使用"省略主语(3-6个字)"的句法
当"不用详细说明的一般表达"时，使用"非常简洁的句子"的句法
当"需要单纯简单的确认"时，使用"单字或几个字的肯定(1-2个字)"的句法

注意不要总结你自己的发言
现在请你概括
"""
    Prompt(learn_grammar_prompt, "learn_grammar_prompt")


class ExpressionLearner:
    def __init__(self) -> None:
        # TODO: API-Adapter修改标记
        self.express_learn_model: LLMRequest = LLMRequest(
            model=global_config.model.focus_expressor,
            temperature=0.1,
            max_tokens=256,
            request_type="learn_expression",
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
        """
        learnt_style: Optional[List[Tuple[str, str, str]]] = await self.learn_and_store(type="style", num=15)
        if not learnt_style:
            return []

        learnt_grammar: Optional[List[Tuple[str, str, str]]] = await self.learn_and_store(type="grammar", num=15)
        if not learnt_grammar:
            return []

        return learnt_style, learnt_grammar

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
        logger.info(f"开始学习{type_str}...")
        learnt_expressions: Optional[List[Tuple[str, str, str]]] = await self.learn_expression(type, num)
        logger.info(f"学习到{len(learnt_expressions) if learnt_expressions else 0}条{type_str}")
        # learnt_expressions: List[(chat_id, situation, style)]

        if not learnt_expressions:
            logger.info(f"没有学习到{type_str}")
            return []

        # 按chat_id分组
        chat_dict: Dict[str, List[Dict[str, str]]] = {}
        for chat_id, situation, style in learnt_expressions:
            if chat_id not in chat_dict:
                chat_dict[chat_id] = []
            chat_dict[chat_id].append({"situation": situation, "style": style})
        # 存储到/data/expression/对应chat_id/expressions.json
        for chat_id, expr_list in chat_dict.items():
            dir_path = os.path.join("data", "expression", f"learnt_{type}", str(chat_id))
            os.makedirs(dir_path, exist_ok=True)
            file_path = os.path.join(dir_path, "expressions.json")
            # 若已存在，先读出合并
            if os.path.exists(file_path):
                old_data: List[Dict[str, str, str]] = []
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                except Exception:
                    old_data = []
            else:
                old_data = []
            # 超过最大数量时，20%概率移除count=1的项
            if len(old_data) >= MAX_EXPRESSION_COUNT:
                new_old_data = []
                for item in old_data:
                    if item.get("count", 1) == 1 and random.random() < 0.2:
                        continue  # 20%概率移除
                    new_old_data.append(item)
                old_data = new_old_data
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
                        break
                if not found:
                    new_expr["count"] = 1
                    old_data.append(new_expr)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(old_data, f, ensure_ascii=False, indent=2)
        return learnt_expressions

    async def learn_expression(self, type: str, num: int = 10) -> Optional[List[Tuple[str, str, str]]]:
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
        # random_msg_str: str = await build_readable_messages(random_msg, timestamp_mode="normal")
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

        return expressions

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
