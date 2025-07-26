import asyncio
import json
import re
from datetime import datetime
from typing import Tuple, Union, Dict, Any, Callable
import aiohttp
from aiohttp.client import ClientResponse
from src.common.logger import get_logger
import base64
from PIL import Image
import io
import os
import copy  # 添加copy模块用于深拷贝
from src.common.database.database import db  # 确保 db 被导入用于 create_tables
from src.common.database.database_model import LLMUsage  # 导入 LLMUsage 模型
from src.config.config import global_config
from src.common.tcp_connector import get_tcp_connector
from rich.traceback import install
from dotenv import load_dotenv


install(extra_lines=3)

logger = get_logger("model_utils")


class PayLoadTooLargeError(Exception):
    """自定义异常类，用于处理请求体过大错误"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return "请求体过大，请尝试压缩图片或减少输入内容。"


class RequestAbortException(Exception):
    """自定义异常类，用于处理请求中断异常"""

    def __init__(self, message: str, response: ClientResponse):
        super().__init__(message)
        self.message = message
        self.response = response

    def __str__(self):
        return self.message


class PermissionDeniedException(Exception):
    """自定义异常类，用于处理访问拒绝的异常"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return self.message


# 常见Error Code Mapping
error_code_mapping = {
    400: "参数不正确",
    401: "API key 错误，认证失败，请检查/config/bot_config.toml和.env中的配置是否正确哦~",
    402: "账号余额不足",
    403: "需要实名,或余额不足",
    404: "Not Found",
    429: "请求过于频繁，请稍后再试",
    500: "服务器内部故障",
    503: "服务器负载过高",
}


async def _safely_record(request_content: Dict[str, Any], payload: Dict[str, Any]):
    """安全地记录请求体，用于调试日志，不会修改原始payload对象"""
    # 创建payload的深拷贝，避免修改原始对象
    safe_payload = copy.deepcopy(payload)
    
    image_base64: str = request_content.get("image_base64")
    image_format: str = request_content.get("image_format")
    if (
        image_base64
        and safe_payload
        and isinstance(safe_payload, dict)
        and "messages" in safe_payload
        and len(safe_payload["messages"]) > 0
    ):
        if isinstance(safe_payload["messages"][0], dict) and "content" in safe_payload["messages"][0]:
            content = safe_payload["messages"][0]["content"]
            if isinstance(content, list) and len(content) > 1 and "image_url" in content[1]:
                # 只修改拷贝的对象，用于安全的日志记录
                safe_payload["messages"][0]["content"][1]["image_url"]["url"] = (
                    f"data:image/{image_format.lower() if image_format else 'jpeg'};base64,"
                    f"{image_base64[:10]}...{image_base64[-10:]}"
                )
    return safe_payload


class LLMRequest:
    # 定义需要转换的模型列表，作为类变量避免重复
    MODELS_NEEDING_TRANSFORMATION = [
        "o1",
        "o1-2024-12-17",
        "o1-mini",
        "o1-mini-2024-09-12",
        "o1-preview",
        "o1-preview-2024-09-12",
        "o1-pro",
        "o1-pro-2025-03-19",
        "o3",
        "o3-2025-04-16",
        "o3-mini",
        "o3-mini-2025-01-31",
        "o4-mini",
        "o4-mini-2025-04-16",
    ]

    def __init__(self, model: dict, **kwargs):
        # 将大写的配置键转换为小写并从config中获取实际值
        logger.debug(f"🔍 [模型初始化] 开始初始化模型: {model.get('name', 'Unknown')}")
        logger.debug(f"🔍 [模型初始化] 模型配置: {model}")
        logger.debug(f"🔍 [模型初始化] 额外参数: {kwargs}")
        
        try:
            # print(f"model['provider']: {model['provider']}")
            # self.api_key = os.environ[f"{model['provider']}_KEY"] # 这是原来的写法，没法读取变量
            # self.base_url = os.environ[f"{model['provider']}_BASE_URL"]
            self.api_key = os.environ.get(f"{model['provider']}_KEY") # 改成这种写法，能读取变量
            self.base_url = os.environ.get(f"{model['provider']}_BASE_URL")
            logger.debug(f"🔍 [模型初始化] 成功获取环境变量: {model['provider']}_KEY 和 {model['provider']}_BASE_URL")
        except AttributeError as e:
            logger.error(f"原始 model dict 信息：{model}")
            logger.error(f"配置错误：找不到对应的配置项 - {str(e)}")
            raise ValueError(f"配置错误：找不到对应的配置项 - {str(e)}") from e
        except KeyError:
            logger.warning(
                f"找不到{model['provider']}_KEY或{model['provider']}_BASE_URL环境变量，请检查配置文件或环境变量设置。"
            )
        self.model_name: str = model["name"]
        self.params = kwargs

        # 记录配置文件中声明了哪些参数（不管值是什么）
        self.has_enable_thinking = "enable_thinking" in model
        self.has_thinking_budget = "thinking_budget" in model
        
        self.enable_thinking = model.get("enable_thinking", False)
        self.temp = model.get("temp", 0.7)
        self.thinking_budget = model.get("thinking_budget", 4096)
        self.stream = model.get("stream", False)
        self.pri_in = model.get("pri_in", 0)
        self.pri_out = model.get("pri_out", 0)
        self.max_tokens = model.get("max_tokens", global_config.model.model_max_output_length)
        # print(f"max_tokens: {self.max_tokens}")
        
        logger.debug("🔍 [模型初始化] 模型参数设置完成:")
        logger.debug(f"   - model_name: {self.model_name}")
        logger.debug(f"   - has_enable_thinking: {self.has_enable_thinking}")
        logger.debug(f"   - enable_thinking: {self.enable_thinking}")
        logger.debug(f"   - has_thinking_budget: {self.has_thinking_budget}")
        logger.debug(f"   - thinking_budget: {self.thinking_budget}")
        logger.debug(f"   - temp: {self.temp}")
        logger.debug(f"   - stream: {self.stream}")
        logger.debug(f"   - max_tokens: {self.max_tokens}")
        logger.debug(f"   - base_url: {self.base_url}")

        # 获取数据库实例
        self._init_database()

        # 从 kwargs 中提取 request_type，如果没有提供则默认为 "default"
        self.request_type = kwargs.pop("request_type", "default")
        logger.debug(f"🔍 [模型初始化] 初始化完成，request_type: {self.request_type}")

    @staticmethod
    def _init_database():
        """初始化数据库集合"""
        try:
            # 使用 Peewee 创建表，safe=True 表示如果表已存在则不会抛出错误
            db.create_tables([LLMUsage], safe=True)
            # logger.debug("LLMUsage 表已初始化/确保存在。")
        except Exception as e:
            logger.error(f"创建 LLMUsage 表失败: {str(e)}")

    def _record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        user_id: str = "system",
        request_type: str = None,
        endpoint: str = "/chat/completions",
    ):
        """记录模型使用情况到数据库
        Args:
            prompt_tokens: 输入token数
            completion_tokens: 输出token数
            total_tokens: 总token数
            user_id: 用户ID，默认为system
            request_type: 请求类型
            endpoint: API端点
        """
        # 如果 request_type 为 None，则使用实例变量中的值
        if request_type is None:
            request_type = self.request_type

        try:
            # 使用 Peewee 模型创建记录
            LLMUsage.create(
                model_name=self.model_name,
                user_id=user_id,
                request_type=request_type,
                endpoint=endpoint,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=self._calculate_cost(prompt_tokens, completion_tokens),
                status="success",
                timestamp=datetime.now(),  # Peewee 会处理 DateTimeField
            )
            logger.debug(
                f"Token使用情况 - 模型: {self.model_name}, "
                f"用户: {user_id}, 类型: {request_type}, "
                f"提示词: {prompt_tokens}, 完成: {completion_tokens}, "
                f"总计: {total_tokens}"
            )
        except Exception as e:
            logger.error(f"记录token使用情况失败: {str(e)}")

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """计算API调用成本
        使用模型的pri_in和pri_out价格计算输入和输出的成本

        Args:
            prompt_tokens: 输入token数量
            completion_tokens: 输出token数量

        Returns:
            float: 总成本（元）
        """
        # 使用模型的pri_in和pri_out计算成本
        input_cost = (prompt_tokens / 1000000) * self.pri_in
        output_cost = (completion_tokens / 1000000) * self.pri_out
        return round(input_cost + output_cost, 6)

    async def _prepare_request(
        self,
        endpoint: str,
        prompt: str = None,
        image_base64: str = None,
        image_format: str = None,
        file_bytes: bytes = None,
        file_format: str = None,
        payload: dict = None,
        retry_policy: dict = None,
    ) -> Dict[str, Any]:
        """配置请求参数
        Args:
            endpoint: API端点路径 (如 "chat/completions")
            prompt: prompt文本
            image_base64: 图片的base64编码
            image_format: 图片格式
            file_bytes: 文件的二进制数据
            file_format: 文件格式
            payload: 请求体数据
            retry_policy: 自定义重试策略
            request_type: 请求类型
        """

        # 合并重试策略
        default_retry = {
            "max_retries": 3,
            "base_wait": 10,
            "retry_codes": [429, 413, 500, 503],
            "abort_codes": [400, 401, 402, 403],
        }
        policy = {**default_retry, **(retry_policy or {})}

        api_url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

        stream_mode = self.stream

        # 构建请求体
        if image_base64:
            payload = await self._build_payload(prompt, image_base64, image_format)
        elif file_bytes:
            payload = await self._build_formdata_payload(file_bytes, file_format)
        elif payload is None:
            payload = await self._build_payload(prompt)

        if not file_bytes:
            if stream_mode:
                payload["stream"] = stream_mode

            if self.temp != 0.7:
                payload["temperature"] = self.temp

            # 添加enable_thinking参数（只有配置文件中声明了才添加，不管值是true还是false）
            if self.has_enable_thinking:
                payload["enable_thinking"] = self.enable_thinking

            # 添加thinking_budget参数（只有配置文件中声明了才添加）
            if self.has_thinking_budget:
                payload["thinking_budget"] = self.thinking_budget

            if self.max_tokens:
                payload["max_tokens"] = self.max_tokens

            # if "max_tokens" not in payload and "max_completion_tokens" not in payload:
            # payload["max_tokens"] = global_config.model.model_max_output_length
            # 如果 payload 中依然存在 max_tokens 且需要转换，在这里进行再次检查
            if self.model_name.lower() in self.MODELS_NEEDING_TRANSFORMATION and "max_tokens" in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens")

        return {
            "policy": policy,
            "payload": payload,
            "api_url": api_url,
            "stream_mode": stream_mode,
            "image_base64": image_base64,  # 保留必要的exception处理所需的原始数据
            "image_format": image_format,
            "file_bytes": file_bytes,
            "file_format": file_format,
            "prompt": prompt,
        }

    async def _execute_request(
        self,
        endpoint: str,
        prompt: str = None,
        image_base64: str = None,
        image_format: str = None,
        file_bytes: bytes = None,
        file_format: str = None,
        payload: dict = None,
        retry_policy: dict = None,
        response_handler: Callable = None,
        user_id: str = "system",
        request_type: str = None,
    ):
        """统一请求执行入口
        Args:
            endpoint: API端点路径 (如 "chat/completions")
            prompt: prompt文本
            image_base64: 图片的base64编码
            image_format: 图片格式
            file_bytes: 文件的二进制数据
            file_format: 文件格式
            payload: 请求体数据
            retry_policy: 自定义重试策略
            response_handler: 自定义响应处理器
            user_id: 用户ID
            request_type: 请求类型
        """
        # 获取请求配置
        request_content = await self._prepare_request(
            endpoint, prompt, image_base64, image_format, file_bytes, file_format, payload, retry_policy
        )
        if request_type is None:
            request_type = self.request_type
        for retry in range(request_content["policy"]["max_retries"]):
            try:
                # 使用上下文管理器处理会话
                if file_bytes:
                    headers = await self._build_headers(is_formdata=True)
                else:
                    headers = await self._build_headers(is_formdata=False)
                # 似乎是openai流式必须要的东西,不过阿里云的qwq-plus加了这个没有影响
                if request_content["stream_mode"]:
                    headers["Accept"] = "text/event-stream"
                
                # 添加请求发送前的调试信息
                logger.debug(f"🔍 [请求调试] 模型 {self.model_name} 准备发送请求")
                logger.debug(f"🔍 [请求调试] API URL: {request_content['api_url']}")
                logger.debug(f"🔍 [请求调试] 请求头: {await self._build_headers(no_key=True, is_formdata=file_bytes is not None)}")
                
                if not file_bytes:
                    # 安全地记录请求体（隐藏敏感信息）
                    safe_payload = await _safely_record(request_content, request_content["payload"])
                    logger.debug(f"🔍 [请求调试] 请求体: {json.dumps(safe_payload, indent=2, ensure_ascii=False)}")
                else:
                    logger.debug(f"🔍 [请求调试] 文件上传请求，文件格式: {request_content['file_format']}")
                
                async with aiohttp.ClientSession(connector=await get_tcp_connector()) as session:
                    post_kwargs = {"headers": headers}
                    # form-data数据上传方式不同
                    if file_bytes:
                        post_kwargs["data"] = request_content["payload"]
                    else:
                        post_kwargs["json"] = request_content["payload"]

                    async with session.post(request_content["api_url"], **post_kwargs) as response:
                        handled_result = await self._handle_response(
                            response, request_content, retry, response_handler, user_id, request_type, endpoint
                        )
                        return handled_result

            except Exception as e:
                handled_payload, count_delta = await self._handle_exception(e, retry, request_content)
                retry += count_delta  # 降级不计入重试次数
                if handled_payload:
                    # 如果降级成功，重新构建请求体
                    request_content["payload"] = handled_payload
                continue

        logger.error(f"模型 {self.model_name} 达到最大重试次数，请求仍然失败")
        raise RuntimeError(f"模型 {self.model_name} 达到最大重试次数，API请求仍然失败")

    async def _handle_response(
        self,
        response: ClientResponse,
        request_content: Dict[str, Any],
        retry_count: int,
        response_handler: Callable,
        user_id,
        request_type,
        endpoint,
    ):
        policy = request_content["policy"]
        stream_mode = request_content["stream_mode"]
        if response.status in policy["retry_codes"] or response.status in policy["abort_codes"]:
            await self._handle_error_response(response, retry_count, policy)
            return None

        response.raise_for_status()
        result = {}
        if stream_mode:
            # 将流式输出转化为非流式输出
            result = await self._handle_stream_output(response)
        else:
            result = await response.json()
        return (
            response_handler(result)
            if response_handler
            else self._default_response_handler(result, user_id, request_type, endpoint)
        )

    async def _handle_stream_output(self, response: ClientResponse) -> Dict[str, Any]:
        flag_delta_content_finished = False
        accumulated_content = ""
        usage = None  # 初始化usage变量，避免未定义错误
        reasoning_content = ""
        content = ""
        tool_calls = None  # 初始化工具调用变量

        async for line_bytes in response.content:
            try:
                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        if flag_delta_content_finished:
                            chunk_usage = chunk.get("usage", None)
                            if chunk_usage:
                                usage = chunk_usage  # 获取token用量
                        else:
                            delta = chunk["choices"][0]["delta"]
                            delta_content = delta.get("content")
                            if delta_content is None:
                                delta_content = ""
                            accumulated_content += delta_content

                            # 提取工具调用信息
                            if "tool_calls" in delta:
                                if tool_calls is None:
                                    tool_calls = delta["tool_calls"]
                                else:
                                    # 合并工具调用信息
                                    tool_calls.extend(delta["tool_calls"])

                            # 检测流式输出文本是否结束
                            finish_reason = chunk["choices"][0].get("finish_reason")
                            if delta.get("reasoning_content", None):
                                reasoning_content += delta["reasoning_content"]
                            if finish_reason == "stop" or finish_reason == "tool_calls":
                                chunk_usage = chunk.get("usage", None)
                                if chunk_usage:
                                    usage = chunk_usage
                                    break
                                # 部分平台在文本输出结束前不会返回token用量，此时需要再获取一次chunk
                                flag_delta_content_finished = True
                    except Exception as e:
                        logger.exception(f"模型 {self.model_name} 解析流式输出错误: {str(e)}")
            except Exception as e:
                if isinstance(e, GeneratorExit):
                    log_content = f"模型 {self.model_name} 流式输出被中断，正在清理资源..."
                else:
                    log_content = f"模型 {self.model_name} 处理流式输出时发生错误: {str(e)}"
                logger.warning(log_content)
                # 确保资源被正确清理
                try:
                    await response.release()
                except Exception as cleanup_error:
                    logger.error(f"清理资源时发生错误: {cleanup_error}")
                # 返回已经累积的内容
                content = accumulated_content
        if not content:
            content = accumulated_content
        think_match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
        if think_match:
            reasoning_content = think_match.group(1).strip()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        # 构建消息对象
        message = {
            "content": content,
            "reasoning_content": reasoning_content,
        }

        # 如果有工具调用，添加到消息中
        if tool_calls:
            message["tool_calls"] = tool_calls

        result = {
            "choices": [{"message": message}],
            "usage": usage,
        }
        return result

    async def _handle_error_response(self, response: ClientResponse, retry_count: int, policy: Dict[str, Any]):
        if response.status in policy["retry_codes"]:
            wait_time = policy["base_wait"] * (2**retry_count)
            logger.warning(f"模型 {self.model_name} 错误码: {response.status}, 等待 {wait_time}秒后重试")
            if response.status == 413:
                logger.warning("请求体过大，尝试压缩...")
                raise PayLoadTooLargeError("请求体过大")
            elif response.status in [500, 503]:
                logger.error(
                    f"模型 {self.model_name} 错误码: {response.status} - {error_code_mapping.get(response.status)}"
                )
                raise RuntimeError("服务器负载过高，模型回复失败QAQ")
            else:
                logger.warning(f"模型 {self.model_name} 请求限制(429)，等待{wait_time}秒后重试...")
                raise RuntimeError("请求限制(429)")
        elif response.status in policy["abort_codes"]:
            # 特别处理400错误，添加详细调试信息
            if response.status == 400:
                logger.error(f"🔍 [调试信息] 模型 {self.model_name} 参数错误 (400) - 开始详细诊断")
                logger.error(f"🔍 [调试信息] 模型名称: {self.model_name}")
                logger.error(f"🔍 [调试信息] API地址: {self.base_url}")
                logger.error("🔍 [调试信息] 模型配置参数:")
                logger.error(f"   - enable_thinking: {self.enable_thinking}")
                logger.error(f"   - temp: {self.temp}")
                logger.error(f"   - thinking_budget: {self.thinking_budget}")
                logger.error(f"   - stream: {self.stream}")
                logger.error(f"   - max_tokens: {self.max_tokens}")
                logger.error(f"   - pri_in: {self.pri_in}")
                logger.error(f"   - pri_out: {self.pri_out}")
                logger.error(f"🔍 [调试信息] 原始params: {self.params}")
                
                # 尝试获取服务器返回的详细错误信息
                try:
                    error_text = await response.text()
                    logger.error(f"🔍 [调试信息] 服务器返回的原始错误内容: {error_text}")
                    
                    try:
                        error_json = json.loads(error_text)
                        logger.error(f"🔍 [调试信息] 解析后的错误JSON: {json.dumps(error_json, indent=2, ensure_ascii=False)}")
                    except json.JSONDecodeError:
                        logger.error("🔍 [调试信息] 错误响应不是有效的JSON格式")
                except Exception as e:
                    logger.error(f"🔍 [调试信息] 无法读取错误响应内容: {str(e)}")
                
                raise RequestAbortException("参数错误，请检查调试信息", response)
            elif response.status != 403:
                raise RequestAbortException("请求出现错误，中断处理", response)
            else:
                raise PermissionDeniedException("模型禁止访问")

    async def _handle_exception(
        self, exception, retry_count: int, request_content: Dict[str, Any]
    ) -> Union[Tuple[Dict[str, Any], int], Tuple[None, int]]:
        policy = request_content["policy"]
        payload = request_content["payload"]
        wait_time = policy["base_wait"] * (2**retry_count)
        keep_request = False
        if retry_count < policy["max_retries"] - 1:
            keep_request = True
        if isinstance(exception, RequestAbortException):
            response = exception.response
            logger.error(
                f"模型 {self.model_name} 错误码: {response.status} - {error_code_mapping.get(response.status)}"
            )
            
            # 如果是400错误，额外输出请求体信息用于调试
            if response.status == 400:
                logger.error("🔍 [异常调试] 400错误 - 请求体调试信息:")
                try:
                    safe_payload = await _safely_record(request_content, payload)
                    logger.error(f"🔍 [异常调试] 发送的请求体: {json.dumps(safe_payload, indent=2, ensure_ascii=False)}")
                except Exception as debug_error:
                    logger.error(f"🔍 [异常调试] 无法安全记录请求体: {str(debug_error)}")
                    logger.error(f"🔍 [异常调试] 原始payload类型: {type(payload)}")
                    if isinstance(payload, dict):
                        logger.error(f"🔍 [异常调试] 原始payload键: {list(payload.keys())}")
            
            # print(request_content)
            # print(response)
            # 尝试获取并记录服务器返回的详细错误信息
            try:
                error_json = await response.json()
                if error_json and isinstance(error_json, list) and len(error_json) > 0:
                    # 处理多个错误的情况
                    for error_item in error_json:
                        if "error" in error_item and isinstance(error_item["error"], dict):
                            error_obj: dict = error_item["error"]
                            error_code = error_obj.get("code")
                            error_message = error_obj.get("message")
                            error_status = error_obj.get("status")
                            logger.error(
                                f"服务器错误详情: 代码={error_code}, 状态={error_status}, 消息={error_message}"
                            )
                elif isinstance(error_json, dict) and "error" in error_json:
                    # 处理单个错误对象的情况
                    error_obj = error_json.get("error", {})
                    error_code = error_obj.get("code")
                    error_message = error_obj.get("message")
                    error_status = error_obj.get("status")
                    logger.error(f"服务器错误详情: 代码={error_code}, 状态={error_status}, 消息={error_message}")
                else:
                    # 记录原始错误响应内容
                    logger.error(f"服务器错误响应: {error_json}")
            except Exception as e:
                logger.warning(f"无法解析服务器错误响应: {str(e)}")
            raise RuntimeError(f"请求被拒绝: {error_code_mapping.get(response.status)}")

        elif isinstance(exception, PermissionDeniedException):
            # 只针对硅基流动的V3和R1进行降级处理
            if self.model_name.startswith("Pro/deepseek-ai") and self.base_url == "https://api.siliconflow.cn/v1/":
                old_model_name = self.model_name
                self.model_name = self.model_name[4:]  # 移除"Pro/"前缀
                logger.warning(f"检测到403错误，模型从 {old_model_name} 降级为 {self.model_name}")

                # 对全局配置进行更新
                if global_config.model.replyer_2.get("name") == old_model_name:
                    global_config.model.replyer_2["name"] = self.model_name
                    logger.warning(f"将全局配置中的 llm_normal 模型临时降级至{self.model_name}")
                if global_config.model.replyer_1.get("name") == old_model_name:
                    global_config.model.replyer_1["name"] = self.model_name
                    logger.warning(f"将全局配置中的 llm_reasoning 模型临时降级至{self.model_name}")

                if payload and "model" in payload:
                    payload["model"] = self.model_name

                await asyncio.sleep(wait_time)
                return payload, -1
            raise RuntimeError(f"请求被拒绝: {error_code_mapping.get(403)}")

        elif isinstance(exception, PayLoadTooLargeError):
            if keep_request:
                image_base64 = request_content["image_base64"]
                compressed_image_base64 = compress_base64_image_by_scale(image_base64)
                new_payload = await self._build_payload(
                    request_content["prompt"], compressed_image_base64, request_content["image_format"]
                )
                return new_payload, 0
            else:
                return None, 0

        elif isinstance(exception, aiohttp.ClientError) or isinstance(exception, asyncio.TimeoutError):
            if keep_request:
                logger.error(f"模型 {self.model_name} 网络错误，等待{wait_time}秒后重试... 错误: {str(exception)}")
                await asyncio.sleep(wait_time)
                return None, 0
            else:
                logger.critical(f"模型 {self.model_name} 网络错误达到最大重试次数: {str(exception)}")
                raise RuntimeError(f"网络请求失败: {str(exception)}")

        elif isinstance(exception, aiohttp.ClientResponseError):
            # 处理aiohttp抛出的，除了policy中的status的响应错误
            if keep_request:
                logger.error(
                    f"模型 {self.model_name} HTTP响应错误，等待{wait_time}秒后重试... 状态码: {exception.status}, 错误: {exception.message}"
                )
                try:
                    error_text = await exception.response.text()
                    error_json = json.loads(error_text)
                    if isinstance(error_json, list) and len(error_json) > 0:
                        # 处理多个错误的情况
                        for error_item in error_json:
                            if "error" in error_item and isinstance(error_item["error"], dict):
                                error_obj = error_item["error"]
                                logger.error(
                                    f"模型 {self.model_name} 服务器错误详情: 代码={error_obj.get('code')}, "
                                    f"状态={error_obj.get('status')}, "
                                    f"消息={error_obj.get('message')}"
                                )
                    elif isinstance(error_json, dict) and "error" in error_json:
                        error_obj = error_json.get("error", {})
                        logger.error(
                            f"模型 {self.model_name} 服务器错误详情: 代码={error_obj.get('code')}, "
                            f"状态={error_obj.get('status')}, "
                            f"消息={error_obj.get('message')}"
                        )
                    else:
                        logger.error(f"模型 {self.model_name} 服务器错误响应: {error_json}")
                except (json.JSONDecodeError, TypeError) as json_err:
                    logger.warning(
                        f"模型 {self.model_name} 响应不是有效的JSON: {str(json_err)}, 原始内容: {error_text[:200]}"
                    )
                except Exception as parse_err:
                    logger.warning(f"模型 {self.model_name} 无法解析响应错误内容: {str(parse_err)}")

                await asyncio.sleep(wait_time)
                return None, 0
            else:
                logger.critical(
                    f"模型 {self.model_name} HTTP响应错误达到最大重试次数: 状态码: {exception.status}, 错误: {exception.message}"
                )
                # 安全地检查和记录请求详情
                handled_payload = await _safely_record(request_content, payload)
                logger.critical(
                    f"请求头: {await self._build_headers(no_key=True)} 请求体: {str(handled_payload)[:100]}"
                )
                raise RuntimeError(
                    f"模型 {self.model_name} API请求失败: 状态码 {exception.status}, {exception.message}"
                )

        else:
            if keep_request:
                logger.error(f"模型 {self.model_name} 请求失败，等待{wait_time}秒后重试... 错误: {str(exception)}")
                await asyncio.sleep(wait_time)
                return None, 0
            else:
                logger.critical(f"模型 {self.model_name} 请求失败: {str(exception)}")
                # 安全地检查和记录请求详情
                handled_payload = await _safely_record(request_content, payload)
                logger.critical(
                    f"请求头: {await self._build_headers(no_key=True)} 请求体: {str(handled_payload)[:100]}"
                )
                raise RuntimeError(f"模型 {self.model_name} API请求失败: {str(exception)}")

    async def _transform_parameters(self, params: dict) -> dict:
        """
        根据模型名称转换参数：
        - 对于需要转换的OpenAI CoT系列模型（例如 "o3-mini"），删除 'temperature' 参数，
        并将 'max_tokens' 重命名为 'max_completion_tokens'
        """
        # 复制一份参数，避免直接修改原始数据
        new_params = dict(params)
        
        logger.debug(f"🔍 [参数转换] 模型 {self.model_name} 开始参数转换")
        logger.debug(f"🔍 [参数转换] 是否为CoT模型: {self.model_name.lower() in self.MODELS_NEEDING_TRANSFORMATION}")
        logger.debug(f"🔍 [参数转换] CoT模型列表: {self.MODELS_NEEDING_TRANSFORMATION}")

        if self.model_name.lower() in self.MODELS_NEEDING_TRANSFORMATION:
            logger.debug("🔍 [参数转换] 检测到CoT模型，开始参数转换")
            # 删除 'temperature' 参数（如果存在），但避免删除我们在_build_payload中添加的自定义温度
            if "temperature" in new_params and new_params["temperature"] == 0.7:
                removed_temp = new_params.pop("temperature")
                logger.debug(f"🔍 [参数转换] 移除默认temperature参数: {removed_temp}")
            # 如果存在 'max_tokens'，则重命名为 'max_completion_tokens'
            if "max_tokens" in new_params:
                old_value = new_params["max_tokens"]
                new_params["max_completion_tokens"] = new_params.pop("max_tokens")
                logger.debug(f"🔍 [参数转换] 参数重命名: max_tokens({old_value}) -> max_completion_tokens({new_params['max_completion_tokens']})")
        else:
            logger.debug("🔍 [参数转换] 非CoT模型，无需参数转换")
            
        logger.debug(f"🔍 [参数转换] 转换前参数: {params}")
        logger.debug(f"🔍 [参数转换] 转换后参数: {new_params}")
        return new_params

    async def _build_formdata_payload(self, file_bytes: bytes, file_format: str) -> aiohttp.FormData:
        """构建form-data请求体"""
        # 目前只适配了音频文件
        # 如果后续要支持其他类型的文件，可以在这里添加更多的处理逻辑
        data = aiohttp.FormData()
        content_type_list = {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "ogg": "audio/ogg",
            "flac": "audio/flac",
            "aac": "audio/aac",
        }

        content_type = content_type_list.get(file_format)
        if not content_type:
            logger.warning(f"暂不支持的文件类型: {file_format}")

        data.add_field(
            "file",
            io.BytesIO(file_bytes),
            filename=f"file.{file_format}",
            content_type=f"{content_type}",  # 根据实际文件类型设置
        )
        data.add_field("model", self.model_name)
        return data

    async def _build_payload(self, prompt: str, image_base64: str = None, image_format: str = None) -> dict:
        """构建请求体"""
        # 复制一份参数，避免直接修改 self.params
        logger.debug(f"🔍 [参数构建] 模型 {self.model_name} 开始构建请求体")
        logger.debug(f"🔍 [参数构建] 原始self.params: {self.params}")
        
        params_copy = await self._transform_parameters(self.params)
        logger.debug(f"🔍 [参数构建] 转换后的params_copy: {params_copy}")
        
        if image_base64:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{image_format.lower()};base64,{image_base64}"},
                        },
                    ],
                }
            ]
        else:
            messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": self.model_name,
            "messages": messages,
            **params_copy,
        }
        
        logger.debug(f"🔍 [参数构建] 基础payload构建完成: {list(payload.keys())}")

        # 添加temp参数（如果不是默认值0.7）
        if self.temp != 0.7:
            payload["temperature"] = self.temp
            logger.debug(f"🔍 [参数构建] 添加temperature参数: {self.temp}")

        # 添加enable_thinking参数（只有配置文件中声明了才添加，不管值是true还是false）
        if self.has_enable_thinking:
            payload["enable_thinking"] = self.enable_thinking
            logger.debug(f"🔍 [参数构建] 添加enable_thinking参数: {self.enable_thinking}")

        # 添加thinking_budget参数（只有配置文件中声明了才添加）
        if self.has_thinking_budget:
            payload["thinking_budget"] = self.thinking_budget
            logger.debug(f"🔍 [参数构建] 添加thinking_budget参数: {self.thinking_budget}")

        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
            logger.debug(f"🔍 [参数构建] 添加max_tokens参数: {self.max_tokens}")

        # if "max_tokens" not in payload and "max_completion_tokens" not in payload:
        # payload["max_tokens"] = global_config.model.model_max_output_length
        # 如果 payload 中依然存在 max_tokens 且需要转换，在这里进行再次检查
        if self.model_name.lower() in self.MODELS_NEEDING_TRANSFORMATION and "max_tokens" in payload:
            old_value = payload["max_tokens"]
            payload["max_completion_tokens"] = payload.pop("max_tokens")
            logger.debug(f"🔍 [参数构建] CoT模型参数转换: max_tokens({old_value}) -> max_completion_tokens({payload['max_completion_tokens']})")
        
        logger.debug(f"🔍 [参数构建] 最终payload键列表: {list(payload.keys())}")
        return payload

    def _default_response_handler(
        self, result: dict, user_id: str = "system", request_type: str = None, endpoint: str = "/chat/completions"
    ) -> Tuple:
        """默认响应解析"""
        if "choices" in result and result["choices"]:
            message = result["choices"][0]["message"]
            content = message.get("content", "")
            content, reasoning = self._extract_reasoning(content)
            reasoning_content = message.get("model_extra", {}).get("reasoning_content", "")
            if not reasoning_content:
                reasoning_content = message.get("reasoning_content", "")
                if not reasoning_content:
                    reasoning_content = reasoning

            # 提取工具调用信息
            tool_calls = message.get("tool_calls", None)

            # 记录token使用情况
            usage = result.get("usage", {})
            if usage:
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                self._record_usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    user_id=user_id,
                    request_type=request_type if request_type is not None else self.request_type,
                    endpoint=endpoint,
                )

            # 只有当tool_calls存在且不为空时才返回
            if tool_calls:
                logger.debug(f"检测到工具调用: {tool_calls}")
                return content, reasoning_content, tool_calls
            else:
                return content, reasoning_content
        elif "text" in result and result["text"]:
            return result["text"]
        return "没有返回结果", ""

    @staticmethod
    def _extract_reasoning(content: str) -> Tuple[str, str]:
        """CoT思维链提取"""
        match = re.search(r"(?:<think>)?(.*?)</think>", content, re.DOTALL)
        content = re.sub(r"(?:<think>)?.*?</think>", "", content, flags=re.DOTALL, count=1).strip()
        if match:
            reasoning = match.group(1).strip()
        else:
            reasoning = ""
        return content, reasoning

    async def _build_headers(self, no_key: bool = False, is_formdata: bool = False) -> dict:
        """构建请求头"""
        if no_key:
            if is_formdata:
                return {"Authorization": "Bearer **********"}
            return {"Authorization": "Bearer **********", "Content-Type": "application/json"}
        else:
            if is_formdata:
                return {"Authorization": f"Bearer {self.api_key}"}
            return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            # 防止小朋友们截图自己的key

    async def generate_response_for_image(self, prompt: str, image_base64: str, image_format: str) -> Tuple:
        """根据输入的提示和图片生成模型的异步响应"""

        response = await self._execute_request(
            endpoint="/chat/completions", prompt=prompt, image_base64=image_base64, image_format=image_format
        )
        # 根据返回值的长度决定怎么处理
        if len(response) == 3:
            content, reasoning_content, tool_calls = response
            return content, reasoning_content, tool_calls
        else:
            content, reasoning_content = response
            return content, reasoning_content

    async def generate_response_for_voice(self, voice_bytes: bytes) -> Tuple:
        """根据输入的语音文件生成模型的异步响应"""
        response = await self._execute_request(
            endpoint="/audio/transcriptions", file_bytes=voice_bytes, file_format="wav"
        )
        return response

    async def generate_response_async(self, prompt: str, **kwargs) -> Union[str, Tuple]:
        """异步方式根据输入的提示生成模型的响应"""
        # 构建请求体，不硬编码max_tokens
        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            **self.params,
            **kwargs,
        }

        response = await self._execute_request(endpoint="/chat/completions", payload=data, prompt=prompt)
        # 原样返回响应，不做处理

        if len(response) == 3:
            content, reasoning_content, tool_calls = response
            return content, (reasoning_content, self.model_name, tool_calls)
        else:
            content, reasoning_content = response
            return content, (reasoning_content, self.model_name)

    async def get_embedding(self, text: str) -> Union[list, None]:
        """异步方法：获取文本的embedding向量

        Args:
            text: 需要获取embedding的文本

        Returns:
            list: embedding向量，如果失败则返回None
        """

        if len(text) < 1:
            logger.debug("该消息没有长度，不再发送获取embedding向量的请求")
            return None

        def embedding_handler(result):
            """处理响应"""
            if "data" in result and len(result["data"]) > 0:
                # 提取 token 使用信息
                usage = result.get("usage", {})
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)
                    # 记录 token 使用情况
                    self._record_usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        user_id="system",  # 可以根据需要修改 user_id
                        # request_type="embedding",  # 请求类型为 embedding
                        request_type=self.request_type,  # 请求类型为 text
                        endpoint="/embeddings",  # API 端点
                    )
                    return result["data"][0].get("embedding", None)
                return result["data"][0].get("embedding", None)
            return None

        embedding = await self._execute_request(
            endpoint="/embeddings",
            prompt=text,
            payload={"model": self.model_name, "input": text, "encoding_format": "float"},
            retry_policy={"max_retries": 2, "base_wait": 6},
            response_handler=embedding_handler,
        )
        return embedding


def compress_base64_image_by_scale(base64_data: str, target_size: int = 0.8 * 1024 * 1024) -> str:
    """压缩base64格式的图片到指定大小
    Args:
        base64_data: base64编码的图片数据
        target_size: 目标文件大小（字节），默认0.8MB
    Returns:
        str: 压缩后的base64图片数据
    """
    try:
        # 将base64转换为字节数据
        # 确保base64字符串只包含ASCII字符
        if isinstance(base64_data, str):
            base64_data = base64_data.encode("ascii", errors="ignore").decode("ascii")
        image_data = base64.b64decode(base64_data)

        # 如果已经小于目标大小，直接返回原图
        if len(image_data) <= 2 * 1024 * 1024:
            return base64_data

        # 将字节数据转换为图片对象
        img = Image.open(io.BytesIO(image_data))

        # 获取原始尺寸
        original_width, original_height = img.size

        # 计算缩放比例
        scale = min(1.0, (target_size / len(image_data)) ** 0.5)

        # 计算新的尺寸
        new_width = int(original_width * scale)
        new_height = int(original_height * scale)

        # 创建内存缓冲区
        output_buffer = io.BytesIO()

        # 如果是GIF，处理所有帧
        if getattr(img, "is_animated", False):
            frames = []
            for frame_idx in range(img.n_frames):
                img.seek(frame_idx)
                new_frame = img.copy()
                new_frame = new_frame.resize((new_width // 2, new_height // 2), Image.Resampling.LANCZOS)  # 动图折上折
                frames.append(new_frame)

            # 保存到缓冲区
            frames[0].save(
                output_buffer,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                optimize=True,
                duration=img.info.get("duration", 100),
                loop=img.info.get("loop", 0),
            )
        else:
            # 处理静态图片
            resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # 保存到缓冲区，保持原始格式
            if img.format == "PNG" and img.mode in ("RGBA", "LA"):
                resized_img.save(output_buffer, format="PNG", optimize=True)
            else:
                resized_img.save(output_buffer, format="JPEG", quality=95, optimize=True)

        # 获取压缩后的数据并转换为base64
        compressed_data = output_buffer.getvalue()
        logger.info(f"压缩图片: {original_width}x{original_height} -> {new_width}x{new_height}")
        logger.info(f"压缩前大小: {len(image_data) / 1024:.1f}KB, 压缩后大小: {len(compressed_data) / 1024:.1f}KB")

        return base64.b64encode(compressed_data).decode("utf-8")

    except Exception as e:
        logger.error(f"压缩图片失败: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        return base64_data
