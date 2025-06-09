import asyncio
import json
import urllib.request
import urllib.error
import base64  # 新增：用于Base64编码
import traceback  # 新增：用于打印堆栈跟踪
from typing import Tuple
from src.chat.actions.plugin_action import PluginAction, register_action
from src.chat.actions.base_action import ActionActivationType, ChatMode
from src.common.logger_manager import get_logger
from .generate_pic_config import generate_config

logger = get_logger("pic_action")

# 当此模块被加载时，尝试生成配置文件（如果它不存在）
# 注意：在某些插件加载机制下，这可能会在每次机器人启动或插件重载时执行
# 考虑是否需要更复杂的逻辑来决定何时运行 (例如，仅在首次安装时)
generate_config()


@register_action
class PicAction(PluginAction):
    """根据描述使用火山引擎HTTP API生成图片的动作处理类"""

    action_name = "pic_action"
    action_description = (
        "可以根据特定的描述，生成并发送一张图片，如果没提供描述，就根据聊天内容生成,你可以立刻画好，不用等待"
    )
    action_parameters = {
        "description": "图片描述，输入你想要生成并发送的图片的描述，必填",
        "size": "图片尺寸，例如 '1024x1024' (可选, 默认从配置或 '1024x1024')",
    }
    action_require = [
        "当有人让你画东西时使用，你可以立刻画好，不用等待",
        "当有人要求你生成并发送一张图片时使用",
        "当有人让你画一张图时使用",
    ]
    enable_plugin = True
    action_config_file_name = "pic_action_config.toml"
    
    # 激活类型设置
    focus_activation_type = ActionActivationType.LLM_JUDGE  # Focus模式使用LLM判定，精确理解需求
    normal_activation_type = ActionActivationType.KEYWORD   # Normal模式使用关键词激活，快速响应
    
    # 关键词设置（用于Normal模式）
    activation_keywords = ["画", "绘制", "生成图片", "画图", "draw", "paint", "图片生成"]
    keyword_case_sensitive = False
    
    # LLM判定提示词（用于Focus模式）
    llm_judge_prompt = """
判定是否需要使用图片生成动作的条件：
1. 用户明确要求画图、生成图片或创作图像
2. 用户描述了想要看到的画面或场景
3. 对话中提到需要视觉化展示某些概念
4. 用户想要创意图片或艺术作品

适合使用的情况：
- "画一张..."、"画个..."、"生成图片"
- "我想看看...的样子"
- "能画出...吗"
- "创作一幅..."

绝对不要使用的情况：
1. 纯文字聊天和问答
2. 只是提到"图片"、"画"等词但不是要求生成
3. 谈论已存在的图片或照片
4. 技术讨论中提到绘图概念但无生成需求
5. 用户明确表示不需要图片时
"""
    
    # Random激活概率（备用）
    random_activation_probability = 0.15  # 适中概率，图片生成比较有趣
    
    # 简单的请求缓存，避免短时间内重复请求
    _request_cache = {}
    _cache_max_size = 10
    
    # 模式启用设置 - 图片生成在所有模式下可用
    mode_enable = ChatMode.ALL
    
    # 并行执行设置 - 图片生成可以与回复并行执行，不覆盖回复内容
    parallel_action = False
    
    @classmethod
    def _get_cache_key(cls, description: str, model: str, size: str) -> str:
        """生成缓存键"""
        return f"{description[:100]}|{model}|{size}"  # 限制描述长度避免键过长
    
    @classmethod
    def _cleanup_cache(cls):
        """清理缓存，保持大小在限制内"""
        if len(cls._request_cache) > cls._cache_max_size:
            # 简单的FIFO策略，移除最旧的条目
            keys_to_remove = list(cls._request_cache.keys())[:-cls._cache_max_size//2]
            for key in keys_to_remove:
                del cls._request_cache[key]

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        global_config: dict = None,
        **kwargs,
    ):
        super().__init__(action_data, reasoning, cycle_timers, thinking_id, global_config, **kwargs)

        logger.info(f"{self.log_prefix} 开始绘图！原因是：{self.reasoning}")

        http_base_url = self.config.get("base_url")
        http_api_key = self.config.get("volcano_generate_api_key")

        if not (http_base_url and http_api_key):
            logger.error(
                f"{self.log_prefix} PicAction初始化, 但HTTP配置 (base_url 或 volcano_generate_api_key) 缺失. HTTP图片生成将失败."
            )
        else:
            logger.info(f"{self.log_prefix} HTTP方式初始化完成. Base URL: {http_base_url}, API Key已配置.")

    # _restore_env_vars 方法不再需要，已移除

    async def process(self) -> Tuple[bool, str]:
        """处理图片生成动作（通过HTTP API）"""
        logger.info(f"{self.log_prefix} 执行 pic_action (HTTP): {self.reasoning}")

        # 配置验证
        http_base_url = self.config.get("base_url")
        http_api_key = self.config.get("volcano_generate_api_key")

        if not (http_base_url and http_api_key):
            error_msg = "抱歉，图片生成功能所需的HTTP配置（如API地址或密钥）不完整，无法提供服务。"
            await self.send_message_by_expressor(error_msg)
            logger.error(f"{self.log_prefix} HTTP调用配置缺失: base_url 或 volcano_generate_api_key.")
            return False, "HTTP配置不完整"

        # API密钥验证
        if http_api_key == "YOUR_VOLCANO_GENERATE_API_KEY_HERE":
            error_msg = "图片生成功能尚未配置，请设置正确的API密钥。"
            await self.send_message_by_expressor(error_msg)
            logger.error(f"{self.log_prefix} API密钥未配置")
            return False, "API密钥未配置"

        # 参数验证
        description = self.action_data.get("description")
        if not description or not description.strip():
            logger.warning(f"{self.log_prefix} 图片描述为空，无法生成图片。")
            await self.send_message_by_expressor("你需要告诉我想要画什么样的图片哦~ 比如说'画一只可爱的小猫'")
            return False, "图片描述为空"

        # 清理和验证描述
        description = description.strip()
        if len(description) > 1000:  # 限制描述长度
            description = description[:1000]
            logger.info(f"{self.log_prefix} 图片描述过长，已截断")

        # 获取配置
        default_model = self.config.get("default_model", "doubao-seedream-3-0-t2i-250415")
        image_size = self.action_data.get("size", self.config.get("default_size", "1024x1024"))

        # 验证图片尺寸格式
        if not self._validate_image_size(image_size):
            logger.warning(f"{self.log_prefix} 无效的图片尺寸: {image_size}，使用默认值")
            image_size = "1024x1024"

        # 检查缓存
        cache_key = self._get_cache_key(description, default_model, image_size)
        if cache_key in self._request_cache:
            cached_result = self._request_cache[cache_key]
            logger.info(f"{self.log_prefix} 使用缓存的图片结果")
            await self.send_message_by_expressor("我之前画过类似的图片，用之前的结果~")
            
            # 直接发送缓存的结果
            send_success = await self.send_message(type="image", data=cached_result)
            if send_success:
                await self.send_message_by_expressor("图片表情已发送！")
                return True, "图片表情已发送(缓存)"
            else:
                # 缓存失败，清除这个缓存项并继续正常流程
                del self._request_cache[cache_key]

        # guidance_scale 现在完全由配置文件控制
        guidance_scale_input = self.config.get("default_guidance_scale", 2.5)  # 默认2.5
        guidance_scale_val = 2.5  # Fallback default
        try:
            guidance_scale_val = float(guidance_scale_input)
        except (ValueError, TypeError):
            logger.warning(
                f"{self.log_prefix} 配置文件中的 default_guidance_scale 值 '{guidance_scale_input}' 无效 (应为浮点数)，使用默认值 2.5。"
            )
            guidance_scale_val = 2.5

        # Seed parameter - ensure it's always an integer
        seed_config_value = self.config.get("default_seed")
        seed_val = 42  # Default seed if not configured or invalid
        if seed_config_value is not None:
            try:
                seed_val = int(seed_config_value)
            except (ValueError, TypeError):
                logger.warning(
                    f"{self.log_prefix} 配置文件中的 default_seed ('{seed_config_value}') 无效，将使用默认种子 42。"
                )
                # seed_val is already 42
        else:
            logger.info(
                f"{self.log_prefix} 未在配置中找到 default_seed，将使用默认种子 42。建议在配置文件中添加 default_seed。"
            )
            # seed_val is already 42

        # Watermark 现在完全由配置文件控制
        effective_watermark_source = self.config.get("default_watermark", True)  # 默认True
        if isinstance(effective_watermark_source, bool):
            watermark_val = effective_watermark_source
        elif isinstance(effective_watermark_source, str):
            watermark_val = effective_watermark_source.lower() == "true"
        else:
            logger.warning(
                f"{self.log_prefix} 配置文件中的 default_watermark 值 '{effective_watermark_source}' 无效 (应为布尔值或 'true'/'false')，使用默认值 True。"
            )
            watermark_val = True

        await self.send_message_by_expressor(
            f"收到！正在为您生成关于 '{description}' 的图片，请稍候...（模型: {default_model}, 尺寸: {image_size}）"
        )

        try:
            success, result = await asyncio.to_thread(
                self._make_http_image_request,
                prompt=description,
                model=default_model,
                size=image_size,
                seed=seed_val,
                guidance_scale=guidance_scale_val,
                watermark=watermark_val,
            )
        except Exception as e:
            logger.error(f"{self.log_prefix} (HTTP) 异步请求执行失败: {e!r}", exc_info=True)
            traceback.print_exc()
            success = False
            result = f"图片生成服务遇到意外问题: {str(e)[:100]}"

        if success:
            image_url = result
            logger.info(f"{self.log_prefix} 图片URL获取成功: {image_url[:70]}... 下载并编码.")

            try:
                encode_success, encode_result = await asyncio.to_thread(self._download_and_encode_base64, image_url)
            except Exception as e:
                logger.error(f"{self.log_prefix} (B64) 异步下载/编码失败: {e!r}", exc_info=True)
                traceback.print_exc()
                encode_success = False
                encode_result = f"图片下载或编码时发生内部错误: {str(e)[:100]}"

            if encode_success:
                base64_image_string = encode_result
                send_success = await self.send_message(type="image", data=base64_image_string)
                if send_success:
                    # 缓存成功的结果
                    self._request_cache[cache_key] = base64_image_string
                    self._cleanup_cache()
                    
                    await self.send_message_by_expressor("图片表情已发送！")
                    return True, "图片表情已发送"
                else:
                    await self.send_message_by_expressor("图片已处理为Base64，但作为表情发送失败了。")
                    return False, "图片表情发送失败 (Base64)"
            else:
                await self.send_message_by_expressor(f"获取到图片URL，但在处理图片时失败了：{encode_result}")
                return False, f"图片处理失败(Base64): {encode_result}"
        else:
            error_message = result
            await self.send_message_by_expressor(f"哎呀，生成图片时遇到问题：{error_message}")
            return False, f"图片生成失败: {error_message}"

    def _download_and_encode_base64(self, image_url: str) -> Tuple[bool, str]:
        """下载图片并将其编码为Base64字符串"""
        logger.info(f"{self.log_prefix} (B64) 下载并编码图片: {image_url[:70]}...")
        try:
            with urllib.request.urlopen(image_url, timeout=30) as response:
                if response.status == 200:
                    image_bytes = response.read()
                    base64_encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                    logger.info(f"{self.log_prefix} (B64) 图片下载编码完成. Base64长度: {len(base64_encoded_image)}")
                    return True, base64_encoded_image
                else:
                    error_msg = f"下载图片失败 (状态: {response.status})"
                    logger.error(f"{self.log_prefix} (B64) {error_msg} URL: {image_url}")
                    return False, error_msg
        except Exception as e:  # Catches all exceptions from urlopen, b64encode, etc.
            logger.error(f"{self.log_prefix} (B64) 下载或编码时错误: {e!r}", exc_info=True)
            traceback.print_exc()
            return False, f"下载或编码图片时发生错误: {str(e)[:100]}"

    def _make_http_image_request(
        self, prompt: str, model: str, size: str, seed: int | None, guidance_scale: float, watermark: bool
    ) -> Tuple[bool, str]:
        base_url = self.config.get("base_url")
        generate_api_key = self.config.get("volcano_generate_api_key")

        endpoint = f"{base_url.rstrip('/')}/images/generations"

        payload_dict = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
            "size": size,
            "guidance_scale": guidance_scale,
            "watermark": watermark,
            "seed": seed,  # seed is now always an int from process()
            "api-key": generate_api_key,
        }
        # if seed is not None: # No longer needed, seed is always an int
        #     payload_dict["seed"] = seed

        data = json.dumps(payload_dict).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {generate_api_key}",
        }

        logger.info(f"{self.log_prefix} (HTTP) 发起图片请求: {model}, Prompt: {prompt[:30]}... To: {endpoint}")
        logger.debug(
            f"{self.log_prefix} (HTTP) Request Headers: {{...Authorization: Bearer {generate_api_key[:10]}...}}"
        )
        logger.debug(
            f"{self.log_prefix} (HTTP) Request Body (api-key omitted): {json.dumps({k: v for k, v in payload_dict.items() if k != 'api-key'})}"
        )

        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                response_status = response.status
                response_body_bytes = response.read()
                response_body_str = response_body_bytes.decode("utf-8")

                logger.info(f"{self.log_prefix} (HTTP) 响应: {response_status}. Preview: {response_body_str[:150]}...")

                if 200 <= response_status < 300:
                    response_data = json.loads(response_body_str)
                    image_url = None
                    if (
                        isinstance(response_data.get("data"), list)
                        and response_data["data"]
                        and isinstance(response_data["data"][0], dict)
                    ):
                        image_url = response_data["data"][0].get("url")
                    elif response_data.get("url"):
                        image_url = response_data.get("url")

                    if image_url:
                        logger.info(f"{self.log_prefix} (HTTP) 图片生成成功，URL: {image_url[:70]}...")
                        return True, image_url
                    else:
                        logger.error(
                            f"{self.log_prefix} (HTTP) API成功但无图片URL. 响应预览: {response_body_str[:300]}..."
                        )
                        return False, "图片生成API响应成功但未找到图片URL"
                else:
                    logger.error(
                        f"{self.log_prefix} (HTTP) API请求失败. 状态: {response.status}. 正文: {response_body_str[:300]}..."
                    )
                    return False, f"图片API请求失败(状态码 {response.status})"
        except Exception as e:
            logger.error(f"{self.log_prefix} (HTTP) 图片生成时意外错误: {e!r}", exc_info=True)
            traceback.print_exc()
            return False, f"图片生成HTTP请求时发生意外错误: {str(e)[:100]}"

    def _validate_image_size(self, image_size: str) -> bool:
        """验证图片尺寸格式"""
        try:
            width, height = map(int, image_size.split('x'))
            return 100 <= width <= 10000 and 100 <= height <= 10000
        except (ValueError, TypeError):
            return False
