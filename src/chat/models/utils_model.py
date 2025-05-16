import asyncio
import json
import re
from datetime import datetime
from typing import Tuple, Union, Dict, Any

import aiohttp
from aiohttp.client import ClientResponse

from src.common.logger import get_module_logger
import base64
from PIL import Image
import io
import os
from ...common.database import db
from ...config.config import global_config
from rich.traceback import install

install(extra_lines=3)

logger = get_module_logger("model_utils")


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
    image_base64: str = request_content.get("image_base64")
    image_format: str = request_content.get("image_format")
    if (
        image_base64
        and payload
        and isinstance(payload, dict)
        and "messages" in payload
        and len(payload["messages"]) > 0
    ):
        if isinstance(payload["messages"][0], dict) and "content" in payload["messages"][0]:
            content = payload["messages"][0]["content"]
            if isinstance(content, list) and len(content) > 1 and "image_url" in content[1]:
                payload["messages"][0]["content"][1]["image_url"]["url"] = (
                    f"data:image/{image_format.lower() if image_format else 'jpeg'};base64,"
                    f"{image_base64[:10]}...{image_base64[-10:]}"
                )
            # if isinstance(content, str) and len(content) > 100:
            #     payload["messages"][0]["content"] = content[:100]
    return payload


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
        "o3-mini-2025-01-31o4-mini",
        "o4-mini-2025-04-16",
    ]

    def __init__(self, model: dict, **kwargs):
        # 将大写的配置键转换为小写并从config中获取实际值
        try:
            self.api_key = os.environ[model["key"]]
            self.base_url = os.environ[model["base_url"]]
        except AttributeError as e:
            logger.error(f"原始 model dict 信息：{model}")
            logger.error(f"配置错误：找不到对应的配置项 - {str(e)}")
            raise ValueError(f"配置错误：找不到对应的配置项 - {str(e)}") from e
        self.model_name: str = model["name"]
        self.params = kwargs

        self.stream = model.get("stream", False)
        self.pri_in = model.get("pri_in", 0)
        self.pri_out = model.get("pri_out", 0)

        # 获取数据库实例
        self._init_database()

        # 从 kwargs 中提取 request_type，如果没有提供则默认为 "default"
        self.request_type = kwargs.pop("request_type", "default")

    @staticmethod
    def _init_database():
        """初始化数据库集合"""
        try:
            # 创建llm_usage集合的索引
            db.llm_usage.create_index([("timestamp", 1)])
            db.llm_usage.create_index([("model_name", 1)])
            db.llm_usage.create_index([("user_id", 1)])
            db.llm_usage.create_index([("request_type", 1)])
        except Exception as e:
            logger.error(f"创建数据库索引失败: {str(e)}")

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
            usage_data = {
                "model_name": self.model_name,
                "user_id": user_id,
                "request_type": request_type,
                "endpoint": endpoint,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost": self._calculate_cost(prompt_tokens, completion_tokens),
                "status": "success",
                "timestamp": datetime.now(),
            }
            db.llm_usage.insert_one(usage_data)
            logger.trace(
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
        payload: dict = None,
        retry_policy: dict = None,
    ) -> Dict[str, Any]:
        """配置请求参数
        Args:
            endpoint: API端点路径 (如 "chat/completions")
            prompt: prompt文本
            image_base64: 图片的base64编码
            image_format: 图片格式
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
        elif payload is None:
            payload = await self._build_payload(prompt)

        if stream_mode:
            payload["stream"] = stream_mode

        return {
            "policy": policy,
            "payload": payload,
            "api_url": api_url,
            "stream_mode": stream_mode,
            "image_base64": image_base64,  # 保留必要的exception处理所需的原始数据
            "image_format": image_format,
            "prompt": prompt,
        }

    async def _execute_request(
        self,
        endpoint: str,
        prompt: str = None,
        image_base64: str = None,
        image_format: str = None,
        payload: dict = None,
        retry_policy: dict = None,
        response_handler: callable = None,
        user_id: str = "system",
        request_type: str = None,
    ):
        """统一请求执行入口
        Args:
            endpoint: API端点路径 (如 "chat/completions")
            prompt: prompt文本
            image_base64: 图片的base64编码
            image_format: 图片格式
            payload: 请求体数据
            retry_policy: 自定义重试策略
            response_handler: 自定义响应处理器
            user_id: 用户ID
            request_type: 请求类型
        """
        # 获取请求配置
        request_content = await self._prepare_request(
            endpoint, prompt, image_base64, image_format, payload, retry_policy
        )
        if request_type is None:
            request_type = self.request_type
        for retry in range(request_content["policy"]["max_retries"]):
            try:
                # 使用上下文管理器处理会话
                headers = await self._build_headers()
                # 似乎是openai流式必须要的东西,不过阿里云的qwq-plus加了这个没有影响
                if request_content["stream_mode"]:
                    headers["Accept"] = "text/event-stream"
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        request_content["api_url"], headers=headers, json=request_content["payload"]
                    ) as response:
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
        response_handler: callable,
        user_id,
        request_type,
        endpoint,
    ) -> Union[Dict[str, Any], None]:
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

    async def _handle_error_response(
        self, response: ClientResponse, retry_count: int, policy: Dict[str, Any]
    ) -> Union[Dict[str, any]]:
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
                raise RuntimeError("服务器负载过高，模型恢复失败QAQ")
            else:
                logger.warning(f"模型 {self.model_name} 请求限制(429)，等待{wait_time}秒后重试...")
                raise RuntimeError("请求限制(429)")
        elif response.status in policy["abort_codes"]:
            if response.status != 403:
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
                if global_config.llm_normal.get("name") == old_model_name:
                    global_config.llm_normal["name"] = self.model_name
                    logger.warning(f"将全局配置中的 llm_normal 模型临时降级至{self.model_name}")
                if global_config.llm_reasoning.get("name") == old_model_name:
                    global_config.llm_reasoning["name"] = self.model_name
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
                logger.critical(f"请求头: {await self._build_headers(no_key=True)} 请求体: {handled_payload[:100]}")
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
                logger.critical(f"请求头: {await self._build_headers(no_key=True)} 请求体: {handled_payload[:100]}")
                raise RuntimeError(f"模型 {self.model_name} API请求失败: {str(exception)}")

    async def _transform_parameters(self, params: dict) -> dict:
        """
        根据模型名称转换参数：
        - 对于需要转换的OpenAI CoT系列模型（例如 "o3-mini"），删除 'temperature' 参数，
        并将 'max_tokens' 重命名为 'max_completion_tokens'
        """
        # 复制一份参数，避免直接修改原始数据
        new_params = dict(params)

        if self.model_name.lower() in self.MODELS_NEEDING_TRANSFORMATION:
            # 删除 'temperature' 参数（如果存在）
            new_params.pop("temperature", None)
            # 如果存在 'max_tokens'，则重命名为 'max_completion_tokens'
            if "max_tokens" in new_params:
                new_params["max_completion_tokens"] = new_params.pop("max_tokens")
        return new_params

    async def _build_payload(self, prompt: str, image_base64: str = None, image_format: str = None) -> dict:
        """构建请求体"""
        # 复制一份参数，避免直接修改 self.params
        params_copy = await self._transform_parameters(self.params)
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
        if "max_tokens" not in payload and "max_completion_tokens" not in payload:
            payload["max_tokens"] = global_config.model_max_output_length
        # 如果 payload 中依然存在 max_tokens 且需要转换，在这里进行再次检查
        if self.model_name.lower() in self.MODELS_NEEDING_TRANSFORMATION and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
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

    async def _build_headers(self, no_key: bool = False) -> dict:
        """构建请求头"""
        if no_key:
            return {"Authorization": "Bearer **********", "Content-Type": "application/json"}
        else:
            return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            # 防止小朋友们截图自己的key

    async def generate_response(self, prompt: str) -> Tuple:
        """根据输入的提示生成模型的异步响应"""

        response = await self._execute_request(endpoint="/chat/completions", prompt=prompt)
        # 根据返回值的长度决定怎么处理
        if len(response) == 3:
            content, reasoning_content, tool_calls = response
            return content, reasoning_content, self.model_name, tool_calls
        else:
            content, reasoning_content = response
            return content, reasoning_content, self.model_name

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

        return response

    async def generate_response_tool_async(self, prompt: str, tools: list, **kwargs) -> tuple[str, str, list]:
        """异步方式根据输入的提示生成模型的响应"""
        # 构建请求体，不硬编码max_tokens
        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            **self.params,
            **kwargs,
            "tools": tools,
        }

        response = await self._execute_request(endpoint="/chat/completions", payload=data, prompt=prompt)
        logger.debug(f"向模型 {self.model_name} 发送工具调用请求，包含 {len(tools)} 个工具，返回结果: {response}")
        # 检查响应是否包含工具调用
        if len(response) == 3:
            content, reasoning_content, tool_calls = response
            logger.debug(f"收到工具调用响应，包含 {len(tool_calls) if tool_calls else 0} 个工具调用")
            return content, reasoning_content, tool_calls
        else:
            content, reasoning_content = response
            logger.debug("收到普通响应，无工具调用")
            return content, reasoning_content, None

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
        logger.success(f"压缩图片: {original_width}x{original_height} -> {new_width}x{new_height}")
        logger.info(f"压缩前大小: {len(image_data) / 1024:.1f}KB, 压缩后大小: {len(compressed_data) / 1024:.1f}KB")

        return base64.b64encode(compressed_data).decode("utf-8")

    except Exception as e:
        logger.error(f"压缩图片失败: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        return base64_data