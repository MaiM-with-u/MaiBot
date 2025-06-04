import unittest
import sys
import os
import time
import asyncio
import traceback
import copy

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_with_chat, build_readable_messages
from src.common.logger import get_module_logger

# 创建测试日志记录器
logger = get_module_logger("test_readable_msg")


class TestBuildReadableMessages(unittest.TestCase):
    def setUp(self):
        # 准备测试数据：从真实数据库获取消息
        self.chat_id = "5ed68437e28644da51f314f37df68d18"
        self.current_time = time.time()
        self.thirty_days_ago = self.current_time - (30 * 24 * 60 * 60)  # 30天前的时间戳

        # 获取最新的10条消息
        try:
            self.messages = get_raw_msg_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=self.thirty_days_ago,
                timestamp_end=self.current_time,
                limit=10,
                limit_mode="latest",
            )
            logger.info(f"已获取 {len(self.messages)} 条测试消息")

            # 打印消息样例
            if self.messages:
                sample_msg = self.messages[0]
                logger.info(f"消息样例: {list(sample_msg.keys())}")
                logger.info(f"消息内容: {sample_msg.get('processed_plain_text', '无文本内容')[:50]}...")
        except Exception as e:
            logger.error(f"获取消息失败: {e}")
            logger.error(traceback.format_exc())
            self.messages = []

    def test_manual_fix_messages(self):
        """创建一个手动修复版本的消息进行测试"""
        if not self.messages:
            self.skipTest("没有测试消息，跳过测试")
            return

        logger.info("开始手动修复消息...")

        # 创建修复版本的消息列表
        fixed_messages = []

        for msg in self.messages:
            # 深拷贝以避免修改原始数据
            fixed_msg = copy.deepcopy(msg)

            # 构建 user_info 对象
            if "user_info" not in fixed_msg:
                user_info = {
                    "platform": fixed_msg.get("user_platform", "qq"),
                    "user_id": fixed_msg.get("user_id", "10000"),
                    "user_nickname": fixed_msg.get("user_nickname", "测试用户"),
                    "user_cardname": fixed_msg.get("user_cardname", ""),
                }
                fixed_msg["user_info"] = user_info
                logger.info(f"为消息 {fixed_msg.get('message_id')} 添加了 user_info")

            fixed_messages.append(fixed_msg)

        logger.info(f"已修复 {len(fixed_messages)} 条消息")

        try:
            # 使用修复后的消息尝试格式化
            formatted_text = asyncio.run(
                build_readable_messages(
                    messages=fixed_messages,
                    replace_bot_name=True,
                    merge_messages=False,
                    timestamp_mode="absolute",
                    read_mark=0.0,
                    truncate=False,
                )
            )

            logger.info("使用修复后的消息格式化完成")
            logger.info(f"格式化结果长度: {len(formatted_text)}")
            if formatted_text:
                logger.info(f"格式化结果预览: {formatted_text[:200]}...")
            else:
                logger.warning("格式化结果为空")

            # 断言
            self.assertNotEqual(formatted_text, "", "有消息时不应返回空字符串")
        except Exception as e:
            logger.error(f"使用修复后的消息格式化失败: {e}")
            logger.error(traceback.format_exc())
            raise

    def test_debug_build_messages_internal(self):
        """调试_build_readable_messages_internal函数"""
        if not self.messages:
            self.skipTest("没有测试消息，跳过测试")
            return

        logger.info("开始调试内部构建函数...")

        try:
            # 直接导入内部函数进行测试
            from src.chat.utils.chat_message_builder import _build_readable_messages_internal

            # 手动创建一个简单的测试消息列表
            test_msg = self.messages[0].copy()  # 使用第一条消息作为模板

            # 检查消息结构
            logger.info(f"测试消息keys: {list(test_msg.keys())}")
            logger.info(f"user_info存在: {'user_info' in test_msg}")

            # 修复缺少的user_info字段
            if "user_info" not in test_msg:
                logger.warning("消息中缺少user_info字段，添加模拟数据")
                test_msg["user_info"] = {
                    "platform": test_msg.get("user_platform", "qq"),
                    "user_id": test_msg.get("user_id", "10000"),
                    "user_nickname": test_msg.get("user_nickname", "测试用户"),
                    "user_cardname": test_msg.get("user_cardname", ""),
                }
                logger.info(f"添加的user_info: {test_msg['user_info']}")

            simple_msgs = [test_msg]

            # 运行内部函数
            result_text, result_details = _build_readable_messages_internal(
                simple_msgs, replace_bot_name=True, merge_messages=False, timestamp_mode="absolute", truncate=False
            )

            logger.info(f"内部函数返回结果: {result_text[:200] if result_text else '空'}")
            logger.info(f"详情列表长度: {len(result_details)}")

            # 显示处理过程中的变量
            if not result_text and len(simple_msgs) > 0:
                logger.warning("消息处理可能有问题，检查关键步骤")
                msg = simple_msgs[0]

                # 打印关键变量的值
                user_info = msg.get("user_info", {})
                platform = user_info.get("platform")
                user_id = user_info.get("user_id")
                timestamp = msg.get("time")
                content = msg.get("processed_plain_text", "")

                logger.warning(f"平台: {platform}, 用户ID: {user_id}, 时间戳: {timestamp}")
                logger.warning(f"内容: {content[:50]}...")

                # 检查必要信息是否完整
                logger.warning(f"必要信息完整性检查: {all([platform, user_id, timestamp is not None])}")

        except Exception as e:
            logger.error(f"调试内部函数失败: {e}")
            logger.error(traceback.format_exc())
            raise


if __name__ == "__main__":
    unittest.main()
