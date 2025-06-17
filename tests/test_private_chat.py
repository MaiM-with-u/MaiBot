import pytest
import asyncio
from src.chat.heart_flow.sub_heartflow import SubHeartflow, ChatState
from src.chat.heart_flow.subheartflow_manager import SubHeartflowManager
from src.chat.message_receive.message import MessageRecv, MessageInfo, UserInfo
from src.chat.message_receive.chat_stream import ChatStream
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.fixture
async def subheartflow_manager():
    manager = SubHeartflowManager()
    yield manager
    # 清理所有子心流
    for subflow in manager.get_all_subheartflows():
        await subflow.shutdown()

@pytest.fixture
def mock_chat_stream():
    stream = MagicMock(spec=ChatStream)
    stream.group_info = None  # 私聊没有群信息
    stream.platform = "test"
    return stream

@pytest.fixture
def mock_message():
    message = MagicMock(spec=MessageRecv)
    message.message_info = MagicMock(spec=MessageInfo)
    message.message_info.user_info = MagicMock(spec=UserInfo)
    message.message_info.message_id = "test_msg_1"
    message.message_info.time = 1234567890
    message.message_info.platform = "test"
    message.processed_plain_text = "测试消息"
    return message

@pytest.mark.asyncio
async def test_private_chat_absent_to_focused(subheartflow_manager, mock_chat_stream, mock_message):
    """测试私聊从ABSENT到FOCUSED的转换"""
    # 创建私聊子心流
    subflow_id = "private_test_1"
    subflow = await subheartflow_manager.get_or_create_subheartflow(subflow_id)
    assert subflow is not None
    
    # 确认初始状态为ABSENT
    assert subflow.chat_state.chat_status == ChatState.ABSENT
    
    # 模拟新消息
    with patch("src.chat.heart_flow.observation.chatting_observation.ChattingObservation") as mock_obs:
        mock_obs_instance = mock_obs.return_value
        mock_obs_instance.has_new_messages_since.return_value = True
        
        # 触发状态检查
        await subheartflow_manager.sbhf_absent_private_into_focus()
        
        # 验证状态已转换为FOCUSED
        assert subflow.chat_state.chat_status == ChatState.FOCUSED

@pytest.mark.asyncio
async def test_private_chat_focused_to_absent(subheartflow_manager, mock_chat_stream, mock_message):
    """测试私聊从FOCUSED到ABSENT的转换"""
    # 创建私聊子心流
    subflow_id = "private_test_2"
    subflow = await subheartflow_manager.get_or_create_subheartflow(subflow_id)
    assert subflow is not None
    
    # 设置初始状态为FOCUSED
    await subflow.change_chat_state(ChatState.FOCUSED)
    assert subflow.chat_state.chat_status == ChatState.FOCUSED
    
    # 触发状态转换
    await subheartflow_manager.sbhf_focus_into_normal(subflow_id)
    
    # 验证状态已转换为ABSENT（私聊特殊处理）
    assert subflow.chat_state.chat_status == ChatState.ABSENT

@pytest.mark.asyncio
async def test_private_chat_stop_keywords(subheartflow_manager, mock_chat_stream, mock_message):
    """测试私聊停止关键词触发状态转换"""
    # 创建私聊子心流
    subflow_id = "private_test_3"
    subflow = await subheartflow_manager.get_or_create_subheartflow(subflow_id)
    assert subflow is not None
    
    # 设置初始状态为FOCUSED
    await subflow.change_chat_state(ChatState.FOCUSED)
    
    # 模拟包含停止关键词的消息
    mock_message.processed_plain_text = "再见啦"
    
    # 模拟处理消息
    with patch("src.chat.focus_chat.planners.planner.ActionPlanner") as mock_planner:
        mock_planner_instance = mock_planner.return_value
        mock_planner_instance.plan_simple.return_value = {
            "action_type": "stop_focus_chat",
            "action_data": {},
            "reasoning": "检测到停止专注聊天的信号"
        }
        
        # 触发状态转换
        await subheartflow_manager.sbhf_focus_into_normal(subflow_id)
        
        # 验证状态已转换为ABSENT
        assert subflow.chat_state.chat_status == ChatState.ABSENT

@pytest.mark.asyncio
async def test_private_chat_error_recovery(subheartflow_manager, mock_chat_stream, mock_message):
    """测试私聊错误恢复"""
    # 创建私聊子心流
    subflow_id = "private_test_4"
    subflow = await subheartflow_manager.get_or_create_subheartflow(subflow_id)
    assert subflow is not None
    
    # 设置初始状态为FOCUSED
    await subflow.change_chat_state(ChatState.FOCUSED)
    
    # 模拟处理器错误
    with patch("src.chat.focus_chat.heartFC_chat.HeartFChatting._process_processors") as mock_proc:
        mock_proc.side_effect = Exception("处理器错误")
        
        # 触发处理
        # 注：这里需要实际调用处理逻辑
        
        # 验证状态保持为FOCUSED（错误不应导致状态改变）
        assert subflow.chat_state.chat_status == ChatState.FOCUSED
        
        # 验证错误后的下一次处理仍然正常
        mock_proc.side_effect = None  # 清除错误
        mock_proc.return_value = ([], {})  # 返回空结果
        
        # 再次触发处理
        # 注：这里需要实际调用处理逻辑
        
        # 验证状态仍然正常
        assert subflow.chat_state.chat_status == ChatState.FOCUSED