import time
import random
import json
import os
from datetime import datetime

from typing import List, Dict, Optional, Any, Tuple

from src.common.logger import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_random, build_anonymous_messages
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.database.database_model import Expression


MAX_EXPRESSION_COUNT = 300
DECAY_DAYS = 30  # 30天衰减到0.01
DECAY_MIN = 0.01  # 最小衰减值

logger = get_logger("expressor")


def format_create_date(timestamp: float) -> str:
    """
    将时间戳格式化为可读的日期字符串
    """
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return "未知时间"


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
例如：当"AAAAA"时，可以"BBBBB", AAAAA代表某个具体的场景，不超过20个字。BBBBB代表对应的语言风格，特定句式或表达方式，不超过20个字。

例如：
当"对某件事表示十分惊叹，有些意外"时，使用"我嘞个xxxx"
当"表示讽刺的赞同，不想讲道理"时，使用"对对对"
当"想说明某个具体的事实观点，但懒得明说，或者不便明说，或表达一种默契"，使用"懂的都懂"
当"当涉及游戏相关时，表示意外的夸赞，略带戏谑意味"时，使用"这么强！"

请注意：不要总结你自己（SELF）的发言
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
            temperature=0.3,
            request_type="expressor.learner",
        )
        self.llm_model = None
        self._ensure_expression_directories()
        self._auto_migrate_json_to_db()
        self._migrate_old_data_create_date()

    def _ensure_expression_directories(self):
        """
        确保表达方式相关的目录结构存在
        """
        base_dir = os.path.join("data", "expression")
        directories_to_create = [
            base_dir,
            os.path.join(base_dir, "learnt_style"),
            os.path.join(base_dir, "learnt_grammar"),
        ]
        
        for directory in directories_to_create:
            try:
                os.makedirs(directory, exist_ok=True)
                logger.debug(f"确保目录存在: {directory}")
            except Exception as e:
                logger.error(f"创建目录失败 {directory}: {e}")

    def _auto_migrate_json_to_db(self):
        """
        自动将/data/expression/learnt_style 和 learnt_grammar 下所有expressions.json迁移到数据库。
        迁移完成后在/data/expression/done.done写入标记文件，存在则跳过。
        """
        base_dir = os.path.join("data", "expression")
        done_flag = os.path.join(base_dir, "done.done")
        
        # 确保基础目录存在
        try:
            os.makedirs(base_dir, exist_ok=True)
            logger.debug(f"确保目录存在: {base_dir}")
        except Exception as e:
            logger.error(f"创建表达方式目录失败: {e}")
            return
        
        if os.path.exists(done_flag):
            logger.info("表达方式JSON已迁移，无需重复迁移。")
            return
            
        logger.info("开始迁移表达方式JSON到数据库...")
        migrated_count = 0
        
        for type in ["learnt_style", "learnt_grammar"]:
            type_str = "style" if type == "learnt_style" else "grammar"
            type_dir = os.path.join(base_dir, type)
            if not os.path.exists(type_dir):
                logger.debug(f"目录不存在，跳过: {type_dir}")
                continue
                
            try:
                chat_ids = os.listdir(type_dir)
                logger.debug(f"在 {type_dir} 中找到 {len(chat_ids)} 个聊天ID目录")
            except Exception as e:
                logger.error(f"读取目录失败 {type_dir}: {e}")
                continue
                
            for chat_id in chat_ids:
                expr_file = os.path.join(type_dir, chat_id, "expressions.json")
                if not os.path.exists(expr_file):
                    continue
                try:
                    with open(expr_file, "r", encoding="utf-8") as f:
                        expressions = json.load(f)
                    
                    if not isinstance(expressions, list):
                        logger.warning(f"表达方式文件格式错误，跳过: {expr_file}")
                        continue
                        
                    for expr in expressions:
                        if not isinstance(expr, dict):
                            continue
                            
                        situation = expr.get("situation")
                        style_val = expr.get("style")
                        count = expr.get("count", 1)
                        last_active_time = expr.get("last_active_time", time.time())
                        
                        if not situation or not style_val:
                            logger.warning(f"表达方式缺少必要字段，跳过: {expr}")
                            continue
                        
                        # 查重：同chat_id+type+situation+style
                        from src.common.database.database_model import Expression

                        query = Expression.select().where(
                            (Expression.chat_id == chat_id)
                            & (Expression.type == type_str)
                            & (Expression.situation == situation)
                            & (Expression.style == style_val)
                        )
                        if query.exists():
                            expr_obj = query.get()
                            expr_obj.count = max(expr_obj.count, count)
                            expr_obj.last_active_time = max(expr_obj.last_active_time, last_active_time)
                            expr_obj.save()
                        else:
                            Expression.create(
                                situation=situation,
                                style=style_val,
                                count=count,
                                last_active_time=last_active_time,
                                chat_id=chat_id,
                                type=type_str,
                                create_date=last_active_time,  # 迁移时使用last_active_time作为创建时间
                            )
                            migrated_count += 1
                    logger.info(f"已迁移 {expr_file} 到数据库，包含 {len(expressions)} 个表达方式")
                except json.JSONDecodeError as e:
                    logger.error(f"JSON解析失败 {expr_file}: {e}")
                except Exception as e:
                    logger.error(f"迁移表达方式 {expr_file} 失败: {e}")
        
        # 标记迁移完成
        try:
            # 确保done.done文件的父目录存在
            done_parent_dir = os.path.dirname(done_flag)
            if not os.path.exists(done_parent_dir):
                os.makedirs(done_parent_dir, exist_ok=True)
                logger.debug(f"为done.done创建父目录: {done_parent_dir}")
            
            with open(done_flag, "w", encoding="utf-8") as f:
                f.write("done\n")
            logger.info(f"表达方式JSON迁移已完成，共迁移 {migrated_count} 个表达方式，已写入done.done标记文件")
        except PermissionError as e:
            logger.error(f"权限不足，无法写入done.done标记文件: {e}")
        except OSError as e:
            logger.error(f"文件系统错误，无法写入done.done标记文件: {e}")
        except Exception as e:
            logger.error(f"写入done.done标记文件失败: {e}")

    def _migrate_old_data_create_date(self):
        """
        为没有create_date的老数据设置创建日期
        使用last_active_time作为create_date的默认值
        """
        try:
            # 查找所有create_date为空的表达方式
            old_expressions = Expression.select().where(Expression.create_date.is_null())
            updated_count = 0
            
            for expr in old_expressions:
                # 使用last_active_time作为create_date
                expr.create_date = expr.last_active_time
                expr.save()
                updated_count += 1
            
            if updated_count > 0:
                logger.info(f"已为 {updated_count} 个老的表达方式设置创建日期")
        except Exception as e:
            logger.error(f"迁移老数据创建日期失败: {e}")

    def get_expression_by_chat_id(self, chat_id: str) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
        """
        获取指定chat_id的style和grammar表达方式
        返回的每个表达方式字典中都包含了source_id, 用于后续的更新操作
        """
        learnt_style_expressions = []
        learnt_grammar_expressions = []

        # 直接从数据库查询
        style_query = Expression.select().where((Expression.chat_id == chat_id) & (Expression.type == "style"))
        for expr in style_query:
            # 确保create_date存在，如果不存在则使用last_active_time
            create_date = expr.create_date if expr.create_date is not None else expr.last_active_time
            learnt_style_expressions.append(
                {
                    "situation": expr.situation,
                    "style": expr.style,
                    "count": expr.count,
                    "last_active_time": expr.last_active_time,
                    "source_id": chat_id,
                    "type": "style",
                    "create_date": create_date,
                }
            )
        grammar_query = Expression.select().where((Expression.chat_id == chat_id) & (Expression.type == "grammar"))
        for expr in grammar_query:
            # 确保create_date存在，如果不存在则使用last_active_time
            create_date = expr.create_date if expr.create_date is not None else expr.last_active_time
            learnt_grammar_expressions.append(
                {
                    "situation": expr.situation,
                    "style": expr.style,
                    "count": expr.count,
                    "last_active_time": expr.last_active_time,
                    "source_id": chat_id,
                    "type": "grammar",
                    "create_date": create_date,
                }
            )
        return learnt_style_expressions, learnt_grammar_expressions

    def get_expression_create_info(self, chat_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取指定chat_id的表达方式创建信息，按创建日期排序
        """
        try:
            expressions = (Expression.select()
                         .where(Expression.chat_id == chat_id)
                         .order_by(Expression.create_date.desc())
                         .limit(limit))
            
            result = []
            for expr in expressions:
                create_date = expr.create_date if expr.create_date is not None else expr.last_active_time
                result.append({
                    "situation": expr.situation,
                    "style": expr.style,
                    "type": expr.type,
                    "count": expr.count,
                    "create_date": create_date,
                    "create_date_formatted": format_create_date(create_date),
                    "last_active_time": expr.last_active_time,
                    "last_active_formatted": format_create_date(expr.last_active_time),
                })
            
            return result
        except Exception as e:
            logger.error(f"获取表达方式创建信息失败: {e}")
            return []

    def is_similar(self, s1: str, s2: str) -> bool:
        """
        判断两个字符串是否相似（只考虑长度大于5且有80%以上重合，不考虑子串）
        """
        if not s1 or not s2:
            return False
        min_len = min(len(s1), len(s2))
        if min_len < 5:
            return False
        same = sum(a == b for a, b in zip(s1, s2, strict=False))
        return same / min_len > 0.8

    async def learn_and_store_expression(self) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]]]:
        """
        学习并存储表达方式，分别学习语言风格和句法特点
        同时对所有已存储的表达方式进行全局衰减
        """
        current_time = time.time()

        # 全局衰减所有已存储的表达方式（直接操作数据库）
        self._apply_global_decay_to_database(current_time)

        learnt_style: Optional[List[Tuple[str, str, str]]] = []
        learnt_grammar: Optional[List[Tuple[str, str, str]]] = []
        # 学习新的表达方式（这里会进行局部衰减）
        for _ in range(3):
            learnt_style = await self.learn_and_store(type="style", num=25)
            if not learnt_style:
                return [], []

        for _ in range(1):
            learnt_grammar = await self.learn_and_store(type="grammar", num=10)
            if not learnt_grammar:
                return [], []

        return learnt_style, learnt_grammar

    def _apply_global_decay_to_database(self, current_time: float) -> None:
        """
        对数据库中的所有表达方式应用全局衰减
        """
        try:
            # 获取所有表达方式
            all_expressions = Expression.select()
            
            updated_count = 0
            deleted_count = 0
            
            for expr in all_expressions:
                # 计算时间差
                last_active = expr.last_active_time
                time_diff_days = (current_time - last_active) / (24 * 3600)  # 转换为天
                
                # 计算衰减值
                decay_value = self.calculate_decay_factor(time_diff_days)
                new_count = max(0.01, expr.count - decay_value)
                
                if new_count <= 0.01:
                    # 如果count太小，删除这个表达方式
                    expr.delete_instance()
                    deleted_count += 1
                else:
                    # 更新count
                    expr.count = new_count
                    expr.save()
                    updated_count += 1
            
            if updated_count > 0 or deleted_count > 0:
                logger.info(f"全局衰减完成：更新了 {updated_count} 个表达方式，删除了 {deleted_count} 个表达方式")
                
        except Exception as e:
            logger.error(f"数据库全局衰减失败: {e}")

    def calculate_decay_factor(self, time_diff_days: float) -> float:
        """
        计算衰减值
        当时间差为0天时，衰减值为0（最近活跃的不衰减）
        当时间差为7天时，衰减值为0.002（中等衰减）
        当时间差为30天或更长时，衰减值为0.01（高衰减）
        使用二次函数进行曲线插值
        """
        if time_diff_days <= 0:
            return 0.0  # 刚激活的表达式不衰减

        if time_diff_days >= DECAY_DAYS:
            return 0.01  # 长时间未活跃的表达式大幅衰减

        # 使用二次函数插值：在0-30天之间从0衰减到0.01
        # 使用简单的二次函数：y = a * x^2
        # 当x=30时，y=0.01，所以 a = 0.01 / (30^2) = 0.01 / 900
        a = 0.01 / (DECAY_DAYS**2)
        decay = a * (time_diff_days**2)

        return min(0.01, decay)

    async def learn_and_store(self, type: str, num: int = 10) -> List[Tuple[str, str, str]]:
        # sourcery skip: use-join
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

        chat_stream = get_chat_manager().get_stream(chat_id)
        if chat_stream is None:
            group_name = f"聊天流 {chat_id}"
        elif chat_stream.group_info:
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
        chat_dict: Dict[str, List[Dict[str, Any]]] = {}
        for chat_id, situation, style in learnt_expressions:
            if chat_id not in chat_dict:
                chat_dict[chat_id] = []
            chat_dict[chat_id].append({"situation": situation, "style": style})

        current_time = time.time()

        # 存储到数据库 Expression 表
        for chat_id, expr_list in chat_dict.items():
            for new_expr in expr_list:
                # 查找是否已存在相似表达方式
                query = Expression.select().where(
                    (Expression.chat_id == chat_id)
                    & (Expression.type == type)
                    & (Expression.situation == new_expr["situation"])
                    & (Expression.style == new_expr["style"])
                )
                if query.exists():
                    expr_obj = query.get()
                    # 50%概率替换内容
                    if random.random() < 0.5:
                        expr_obj.situation = new_expr["situation"]
                        expr_obj.style = new_expr["style"]
                    expr_obj.count = expr_obj.count + 1
                    expr_obj.last_active_time = current_time
                    expr_obj.save()
                else:
                    Expression.create(
                        situation=new_expr["situation"],
                        style=new_expr["style"],
                        count=1,
                        last_active_time=current_time,
                        chat_id=chat_id,
                        type=type,
                        create_date=current_time,  # 手动设置创建日期
                    )
            # 限制最大数量
            exprs = list(
                Expression.select()
                .where((Expression.chat_id == chat_id) & (Expression.type == type))
                .order_by(Expression.count.asc())
            )
            if len(exprs) > MAX_EXPRESSION_COUNT:
                # 删除count最小的多余表达方式
                for expr in exprs[: len(exprs) - MAX_EXPRESSION_COUNT]:
                    expr.delete_instance()
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


expression_learner = None


def get_expression_learner():
    global expression_learner
    if expression_learner is None:
        expression_learner = ExpressionLearner()
    return expression_learner
