import json
import re
from typing import Dict, Any, Optional, List, Tuple, Union
from src.common.logger import get_module_logger

logger = get_module_logger("pfc_utils")

def get_items_from_json(
    content: str,
    *items: str,
    default_values: Optional[Dict[str, Any]] = None,
    required_types: Optional[Dict[str, type]] = None
) -> Tuple[bool, Dict[str, Any]]:
    """从文本中提取JSON内容并获取指定字段
    
    Args:
        content: 包含JSON的文本
        *items: 要提取的字段名
        default_values: 字段的默认值，格式为 {字段名: 默认值}
        required_types: 字段的必需类型，格式为 {字段名: 类型}
        
    Returns:
        Tuple[bool, Dict[str, Any]]: (是否成功, 提取的字段字典)
    """
    content = content.strip()
    result = {}
    
    # 设置默认值
    if default_values:
        result.update(default_values)
    
    # 尝试解析JSON
    try:
        json_data = json.loads(content)
    except json.JSONDecodeError:
        # 如果直接解析失败，尝试查找和提取JSON部分
        json_pattern = r'\{[^{}]*\}'
        json_match = re.search(json_pattern, content)
        if json_match:
            try:
                json_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.error("提取的JSON内容解析失败")
                return False, result
        else:
            logger.error("无法在返回内容中找到有效的JSON")
            return False, result
    
    # 提取字段
    for item in items:
        if item in json_data:
            result[item] = json_data[item]
    
    # 验证必需字段
    if not all(item in result for item in items):
        logger.error(f"JSON缺少必要字段，实际内容: {json_data}")
        return False, result
    
    # 验证字段类型
    if required_types:
        for field, expected_type in required_types.items():
            if field in result and not isinstance(result[field], expected_type):
                logger.error(f"{field} 必须是 {expected_type.__name__} 类型")
                return False, result
    
    # 验证字符串字段不为空
    for field in items:
        if isinstance(result[field], str) and not result[field].strip():
            logger.error(f"{field} 不能为空")
            return False, result
    
    return True, result 