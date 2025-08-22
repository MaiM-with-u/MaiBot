import time
import random
import json
import os
from datetime import datetime

from typing import List, Dict, Optional, Any, Tuple

from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.common.data_models.database_data_model import DatabaseMessages
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_with_chat_inclusive, build_anonymous_messages
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import get_chat_manager


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
2. 不要涉及具体的人名，但是可以涉及具体名词
3. 思考有没有特殊的梗，一并总结成语言风格
4. 例子仅供参考，请严格根据群聊内容总结!!!
注意：总结成如下格式的规律，总结的内容要详细，但具有概括性：
例如：当"AAAAA"时，可以"BBBBB", AAAAA代表某个具体的场景，不超过20个字。BBBBB代表对应的语言风格，特定句式或表达方式，不超过20个字。

例如：
当"对某件事表示十分惊叹，有些意外"时，使用"我嘞个xxxx"
当"表示讽刺的赞同，不想讲道理"时，使用"对对对"
当"想说明某个具体的事实观点，但懒得明说，或者不便明说，或表达一种默契"，使用"懂的都懂"
当"当涉及游戏相关时，表示意外的夸赞，略带戏谑意味"时，使用"这么强！"

请注意：不要总结你自己（SELF）的发言，尽量保证总结内容的逻辑性
现在请你概括
"""
    Prompt(learn_style_prompt, "learn_style_prompt")


class ExpressionLearner:
    def __init__(self, chat_id: str) -> None:
        self.express_learn_model: LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.replyer, request_type="expression.learner"
        )
        self.chat_id = chat_id
        self.chat_name = get_chat_manager().get_stream_name(chat_id) or chat_id

        # 维护每个chat的上次学习时间
        self.last_learning_time: float = time.time()

        # 学习参数
        self.min_messages_for_learning = 25  # 触发学习所需的最少消息数
        self.min_learning_interval = 300  # 最短学习时间间隔（秒）

    def can_learn_for_chat(self) -> bool:
        """
        检查指定聊天流是否允许学习表达

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否允许学习
        """
        try:
            use_expression, enable_learning, _ = global_config.expression.get_expression_config_for_chat(self.chat_id)
            return enable_learning
        except Exception as e:
            logger.error(f"检查学习权限失败: {e}")
            return False

    def should_trigger_learning(self) -> bool:
        """
        检查是否应该触发学习

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否应该触发学习
        """
        current_time = time.time()

        # 获取该聊天流的学习强度
        try:
            _, enable_learning, learning_intensity = global_config.expression.get_expression_config_for_chat(
                self.chat_id
            )
        except Exception as e:
            logger.error(f"获取聊天流 {self.chat_id} 的学习配置失败: {e}")
            return False

        # 检查是否允许学习
        if not enable_learning:
            return False

        # 根据学习强度计算最短学习时间间隔
        min_interval = self.min_learning_interval / learning_intensity

        # 检查时间间隔
        time_diff = current_time - self.last_learning_time
        if time_diff < min_interval:
            return False

        # 检查消息数量（只检查指定聊天流的消息）
        recent_messages = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_learning_time,
            timestamp_end=time.time(),
        )

        if not recent_messages or len(recent_messages) < self.min_messages_for_learning:
            return False

        return True

    async def trigger_learning_for_chat(self) -> bool:
        """
        为指定聊天流触发学习

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否成功触发学习
        """
        if not self.should_trigger_learning():
            return False

        try:
            logger.info(f"为聊天流 {self.chat_name} 触发表达学习")

            # 学习语言风格
            learnt_style = await self.learn_and_store(num=25)

            # 更新学习时间
            self.last_learning_time = time.time()

            if learnt_style:
                logger.info(f"聊天流 {self.chat_name} 表达学习完成")
                return True
            else:
                logger.warning(f"聊天流 {self.chat_name} 表达学习未获得有效结果")
                return False

        except Exception as e:
            logger.error(f"为聊天流 {self.chat_name} 触发学习失败: {e}")
            return False

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

    async def learn_and_store(self, num: int = 10) -> List[Tuple[str, str, str]]:
        """
        学习并存储表达方式
        """
        # 检查是否允许在此聊天流中学习（在函数最前面检查）
        if not self.can_learn_for_chat():
            logger.debug(f"聊天流 {self.chat_name} 不允许学习表达，跳过学习")
            return []

        res = await self.learn_expression(num)

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
        logger.info(f"在 {group_name} 学习到表达风格:\n{learnt_expressions_str}")

        if not learnt_expressions:
            logger.info("没有学习到表达风格")
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
                    & (Expression.type == "style")
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
                        type="style",
                        create_date=current_time,  # 手动设置创建日期
                    )
            # 限制最大数量
            exprs = list(
                Expression.select()
                .where((Expression.chat_id == chat_id) & (Expression.type == "style"))
                .order_by(Expression.count.asc())
            )
            if len(exprs) > MAX_EXPRESSION_COUNT:
                # 删除count最小的多余表达方式
                for expr in exprs[: len(exprs) - MAX_EXPRESSION_COUNT]:
                    expr.delete_instance()
        return learnt_expressions

    async def learn_expression(self, num: int = 10) -> Optional[Tuple[List[Tuple[str, str, str]], str]]:
        """从指定聊天流学习表达方式

        Args:
            num: 学习数量
        """
        type_str = "语言风格"
        prompt = "learn_style_prompt"

        current_time = time.time()

        # 获取上次学习时间
        random_msg = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_learning_time,
            timestamp_end=current_time,
            limit=num,
        )
        # print(random_msg)
        if not random_msg or random_msg == []:
            return None
        # 转化成str
        chat_id: str = random_msg[0].chat_id
        # random_msg_str: str = build_readable_messages(random_msg, timestamp_mode="normal")
        random_msg_str: str = await build_anonymous_messages(random_msg)
        # print(f"random_msg_str:{random_msg_str}")

        prompt: str = await global_prompt_manager.format_prompt(
            prompt,
            chat_str=random_msg_str,
        )

        logger.debug(f"学习{type_str}的prompt: {prompt}")

        try:
            response, _ = await self.express_learn_model.generate_response_async(prompt, temperature=0.3)
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


class ExpressionLearnerManager:
    def __init__(self):
        self.expression_learners = {}

        self._ensure_expression_directories()
        self._auto_migrate_json_to_db()
        self._migrate_old_data_create_date()

    def get_expression_learner(self, chat_id: str) -> ExpressionLearner:
        if chat_id not in self.expression_learners:
            self.expression_learners[chat_id] = ExpressionLearner(chat_id)
        return self.expression_learners[chat_id]

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
        然后检查done.done2，如果没有就删除所有grammar表达并创建该标记文件。
        """
        base_dir = os.path.join("data", "expression")
        done_flag = os.path.join(base_dir, "done.done")
        done_flag2 = os.path.join(base_dir, "done.done2")

        # 确保基础目录存在
        try:
            os.makedirs(base_dir, exist_ok=True)
            logger.debug(f"确保目录存在: {base_dir}")
        except Exception as e:
            logger.error(f"创建表达方式目录失败: {e}")
            return

        if os.path.exists(done_flag):
            logger.info("表达方式JSON已迁移，无需重复迁移。")
        else:
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

        # 检查并处理grammar表达删除
        if not os.path.exists(done_flag2):
            logger.info("开始删除所有grammar类型的表达...")
            try:
                deleted_count = self.delete_all_grammar_expressions()
                logger.info(f"grammar表达删除完成，共删除 {deleted_count} 个表达")

                # 创建done.done2标记文件
                with open(done_flag2, "w", encoding="utf-8") as f:
                    f.write("done\n")
                logger.info("已创建done.done2标记文件，grammar表达删除标记完成")
            except Exception as e:
                logger.error(f"删除grammar表达或创建标记文件失败: {e}")
        else:
            logger.info("grammar表达已删除，跳过重复删除")

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

    def delete_all_grammar_expressions(self) -> int:
        """
        检查expression库中所有type为"grammar"的表达并全部删除

        Returns:
            int: 删除的grammar表达数量
        """
        try:
            # 查询所有type为"grammar"的表达
            grammar_expressions = Expression.select().where(Expression.type == "grammar")
            grammar_count = grammar_expressions.count()

            if grammar_count == 0:
                logger.info("expression库中没有找到grammar类型的表达")
                return 0

            logger.info(f"找到 {grammar_count} 个grammar类型的表达，开始删除...")

            # 删除所有grammar类型的表达
            deleted_count = 0
            for expr in grammar_expressions:
                try:
                    expr.delete_instance()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"删除grammar表达失败: {e}")
                    continue

            logger.info(f"成功删除 {deleted_count} 个grammar类型的表达")
            return deleted_count

        except Exception as e:
            logger.error(f"删除grammar表达过程中发生错误: {e}")
            return 0


expression_learner_manager = ExpressionLearnerManager()
