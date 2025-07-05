import asyncio
import json
import urllib.request
import urllib.error
import base64
import traceback
from typing import List, Tuple, Type, Optional

# 导入新插件系统
from src.plugin_system.apis import chat_api
from src.plugin_system.apis import send_api
from src.plugin_system.apis import config_api
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.base_plugin import register_plugin
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField
from src.common.logger import get_logger


logger = get_logger("pic_action")

# 当此模块被加载时，尝试生成配置文件（如果它不存在）
# 注意：在某些插件加载机制下，这可能会在每次机器人启动或插件重载时执行
# 考虑是否需要更复杂的逻辑来决定何时运行 (例如，仅在首次安装时)






# ===== Action组件 =====

class Custom_Pic_Action(BaseAction):
    """生成一张图片并发送"""

    # 激活设置
    focus_activation_type = ActionActivationType.LLM_JUDGE  # Focus模式使用LLM判定，精确理解需求
    normal_activation_type = ActionActivationType.KEYWORD  # Normal模式使用关键词激活，快速响应
    mode_enable = ChatMode.ALL
    parallel_action = True

    # 动作基本信息
    action_name = "draw_picture"
    action_description = (
        "可以根据特定的描述，生成并发送一张图片，如果没提供描述，就根据聊天内容生成,你可以立刻画好，不用等待"
    )

    # 关键词设置（用于Normal模式）
    activation_keywords = ["画", "绘制", "生成图片", "画图", "draw", "paint", "图片生成"]

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

    keyword_case_sensitive = False

    # 动作参数定义
    action_parameters = {
        "description": "图片描述，输入你想要生成并发送的图片的描述，将描述翻译为英文单词组合，并用‘,‘分隔，描述中不要出现中文，必填",
        "size": "图片尺寸，例如 '512x512' (可选, 默认从配置或 '1024x1024')",
    }

    # 动作使用场景
    action_require = [
        #"当有人让你画东西时使用，你可以立刻画好，不用等待",
        "当有人要求你生成并发送一张图片时使用，不要频率太高",
        #"当有人让你画一张图时使用",
        "当你想要通过自画像表情包来表达自己情感时使用，不要频率太高",
        "重点：不要连续发，如果你在前10句内已经发送过[图片]或者[表情包]或记录出现过类似描述的[图片]，就不要不选择此动作",
    ]
    associated_types = ["text", "image"]
    # 简单的请求缓存，避免短时间内重复请求
    _request_cache = {}
    _cache_max_size = 10

    async def execute(self) -> Tuple[bool, Optional[str]]:
        """执行图片生成动作"""
        logger.info(f"{self.log_prefix} 执行绘图模型图片生成动作")

        # 配置验证
        http_base_url = self.get_config("api.base_url")
        http_api_key = self.get_config("api.api_key")

        if not (http_base_url and http_api_key):
            error_msg = "抱歉，图片生成功能所需的HTTP配置（如API地址或密钥）不完整，无法提供服务。"
            await self.send_text(error_msg)
            logger.error(f"{self.log_prefix} HTTP调用配置缺失: base_url 或 api_key.")
            return False, "HTTP配置不完整"

        # API密钥验证
        if http_api_key == "YOUR_DOUBAO_API_KEY_HERE":
            error_msg = "图片生成功能尚未配置，请设置正确的API密钥。"
            await self.send_text(error_msg)
            logger.error(f"{self.log_prefix} API密钥未配置")
            return False, "API密钥未配置"
        
        # 参数验证
        description = self.action_data.get("description")
        if not description or not description.strip():
            logger.warning(f"{self.log_prefix} 图片描述为空，无法生成图片。")
            await self.send_text("你需要告诉我想要画什么样的图片哦~ 比如说'画一只可爱的小猫'")
            return False, "图片描述为空"
        
        # 清理和验证描述
        description = description.strip()
        if len(description) > 1000:  # 限制描述长度
            description = description[:1000]
            logger.info(f"{self.log_prefix} 图片描述过长，已截断")

        # 获取配置
        default_model = self.get_config("generation.default_model", "doubao-seedream-3-0-t2i-250415")
        image_size = self.action_data.get("size", self.get_config("generation.default_size", "1024x1024"))

        # 验证图片尺寸格式
        if not self._validate_image_size(image_size):
            logger.warning(f"{self.log_prefix} 无效的图片尺寸: {image_size}，使用默认值")
            image_size = "1024x1024"

        # 检查缓存
        cache_key = self._get_cache_key(description, default_model, image_size)
        if cache_key in self._request_cache:
            cached_result = self._request_cache[cache_key]
            logger.info(f"{self.log_prefix} 使用缓存的图片结果")
            await self.send_text("我之前画过类似的图片，用之前的结果~")

            # 直接发送缓存的结果
            send_success = await self.send_image(cached_result)
            if send_success:
                await self.send_text("图片已发送！")
                return True, "图片已发送(缓存)"
            else:
                # 缓存失败，清除这个缓存项并继续正常流程
                del self._request_cache[cache_key]

        # 获取其他配置参数
        seed_val = self.get_config("generation.default_seed",42)#种子
        guidance_scale_val=self.get_config("default_guidance_scale",2.5)#强度
        watermark_val = self.get_config("generation.default_watermark", True)#水印


        await self.send_text(
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
            # 如果返回的是Base64数据（以"iVBORw"等开头），直接使用
            if result.startswith(("iVBORw", "/9j/", "UklGR", "R0lGOD")):  # 常见图片格式的Base64前缀
                #logger.info(f"{self.log_prefix} 获取到Base64图片数据，直接发送")
                #logger.info(f"{self.log_prefix} (HTTP) 获取到Base64图片数据，长度: {len(self.log_prefix)}")
                send_success = await self.send_image(result)
                if send_success:
                    await self.send_text("图片表情已发送！")
                    return True, "图片表情已发送(Base64)"
                else:
                    await self.send_text("图片已处理为Base64，但作为表情发送失败了")#("图片已处理为Base64，但作为表情发送失败了。")
                    return False, "图片表情发送失败 (Base64)"
            else:  # 否则认为是URL
                image_url = result
            # print(f"image_url: {image_url}")
            # print(f"result: {result}")
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
                    send_success = await self.send_image(base64_image_string)
                    if send_success:
                        # 缓存成功的结果
                        self._request_cache[cache_key] = base64_image_string
                        self._cleanup_cache()

                        await self.send_text("图片已发送！")
                        return True, "图片已成功生成并发送"
                    else:
                        print(f"send_success: {send_success}")
                        await self.send_text("图片已处理为Base64，但发送失败了。")
                        return False, "图片发送失败 (Base64)"
                else:
                    await self.send_text(f"获取到图片URL，但在处理图片时失败了：{encode_result}")
                    return False, f"图片处理失败(Base64): {encode_result}"
        else:
            error_message = result
            await self.send_text(f"哎呀，生成图片时遇到问题：{error_message}")
            return False, f"图片生成失败: {error_message}"

    def _download_and_encode_base64(self, image_url: str) -> Tuple[bool, str]:
        """下载图片并将其编码为Base64字符串"""
        logger.info(f"{self.log_prefix} (B64) 下载并编码图片: {image_url[:70]}...")
        try:
            with urllib.request.urlopen(image_url, timeout=60) as response:
                if response.status == 200:
                    image_bytes = response.read()
                    base64_encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                    logger.info(f"{self.log_prefix} (B64) 图片下载编码完成. Base64长度: {len(base64_encoded_image)}")
                    return True, base64_encoded_image
                else:
                    error_msg = f"下载图片失败 (状态: {response.status})"
                    logger.error(f"{self.log_prefix} (B64) {error_msg} URL: {image_url}")
                    return False, error_msg
        except Exception as e: 
            logger.error(f"{self.log_prefix} (B64) 下载或编码时错误: {e!r}", exc_info=True)
            traceback.print_exc()
            return False, f"下载或编码图片时发生错误: {str(e)[:100]}"        
        
    @classmethod
    def _get_cache_key(cls, description: str, model: str, size: str) -> str:
        """生成缓存键"""
        return f"{description[:100]}|{model}|{size}"

    @classmethod
    def _cleanup_cache(cls):
        """清理缓存，保持大小在限制内"""
        if len(cls._request_cache) > cls._cache_max_size:
            keys_to_remove = list(cls._request_cache.keys())[: -cls._cache_max_size // 2]
            for key in keys_to_remove:
                del cls._request_cache[key]

    def _validate_image_size(self, image_size: str) -> bool:
        """验证图片尺寸格式"""
        try:
            width, height = map(int, image_size.split("x"))
            return 100 <= width <= 10000 and 100 <= height <= 10000
        except (ValueError, TypeError):
            return False


    def _make_http_image_request(
        self, prompt: str, model: str, size: str, seed: int | None, guidance_scale: float, watermark: bool
    ) -> Tuple[bool, str]:
        """发送HTTP请求生成图片"""
        base_url = self.get_config("api.base_url")
        generate_api_key = self.get_config("api.api_key")

        endpoint = f"{base_url.rstrip('/')}/images/generations"

        # 获取配置参数 - 使用字符串键名
        custom_prompt_add = self.get_config("generation.custom_prompt_add", "")#附加正面提示词参数
        negative_prompt_add = self.get_config("generation.negative_prompt_add", "")#附加负面提示词参数

        prompt_add= prompt + ", " + custom_prompt_add
        negative_prompt = negative_prompt_add


        payload_dict = {
            "model": model,
            "prompt": prompt_add,  # 使用附加的正面提示词
            "negative_prompt":negative_prompt,
            #"response_format": "b64_json",# gpt-image-1 无法使用 url 返回为 “b64_json"，豆包默认返回为 "url"
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
            "Authorization": f"{generate_api_key}",
        }# gpt 或其他模型密钥要删除‘Bearer ’前缀

        logger.info(f"{self.log_prefix} (HTTP) 发起图片请求: {model}, custom_prompt_add:{custom_prompt_add[:30]}...,Prompt: {prompt_add[:30]}...,NegativePrompt: {negative_prompt[:30]}... To: {endpoint}")
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
                    b64_data = None
                    image_url = None #清理缓存
                    # 优先检查Base64数据
                    if (
                        isinstance(response_data.get("data"), list)
                        and response_data["data"]
                        and isinstance(response_data["data"][0], dict)
                        and "b64_json" in response_data["data"][0]
                    ):

                        b64_data = response_data["data"][0]["b64_json"]
                        logger.info(f"{self.log_prefix} (HTTP) 获取到Base64图片数据，长度: {len(b64_data)}")
                        return True, b64_data  # 直接返回Base64字符串
                    elif (
                            isinstance(response_data.get("data"), list)
                            and response_data["data"]
                            and isinstance(response_data["data"][0], dict)
                    ):
                        image_url = response_data["data"][0].get("url")
                    elif(#魔搭社区返回的 json
                            isinstance(response_data.get("images"), list)
                            and response_data["images"]
                            and isinstance(response_data["images"][0], dict)
                    ):
                        image_url = response_data["images"][0].get("url")
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


# ===== 插件注册 =====
@register_plugin
class CustomPicPlugin(BasePlugin):
    """根据描述使用不同的 绘图 API生成图片的动作处理类"""
    # 插件基本信息
    plugin_name = "custom_pic_plugin"# 内部标识符
    plugin_version = "1.0.2"
    plugin_author = "Ptrel"
    enable_plugin = True
    config_file_name = "config.toml"



    # 步骤1: 定义配置节的描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "api": "API的基础url",
        "generation": "图片生成参数配置，控制生成图片的各种参数",
        "cache": "结果缓存配置",
        "components": "组件启用配置",
    }

    # 步骤2: 使用ConfigField定义详细的配置Schema
    config_schema = {
        "plugin": {
            "name": ConfigField(type=str, default="custom_pic_plugin", description="自定义提示词绘图", required=True),
            "version": ConfigField(type=str, default="1.1.2", description="插件版本号"),
            "enabled": ConfigField(type=bool, default=False, description="是否启用插件")
        },
        "api": {
            "base_url": ConfigField(
                type=str,
                default="https://api-inference.modelscope.cn/v1",
                description="API的基础url",
                example="https://api.example.com/v1",
                choices=[
                    "https://api-inference.modelscope.cn/v1\"#魔搭可自选 lora，对应模型网址：https://modelscope.cn/models?page=1&tabKey=task&tasks=hotTask:text-to-image-synthesis&type=tasks", 
                    "\n#https://ark.cn-beijing.volces.com/api/v3\"#豆包火山方舟 API,对应网址：https://console.volcengine.com/auth/login?redirectURI=%2Fmessage%2Finnermsg",
                    "\n#https://api.chatanywhere.tech/v1\"#chatany API，对应文档网址：https://chatanywhere.apifox.cn/",]
            ),
            "api_key": ConfigField(
                type=str,                 
                default="Bearer xxxxxxxxxxxxxxxxxxxxxx",
                description="API 的 api 密钥，需要添加‘Bearer ’前缀，chatany 不需要前缀，根据不同 api 文档进行选择,如 chatanywhere 的 key 不需要 Berarer，即直接输入密钥‘xxxxxxxxxxxxxxxxxxxxxxxx’", 
                required=True
            ),
        },
        "generation": {
            "default_model": ConfigField(
                type=str,
                default="cancel13/liaocao\"#潦草 lora 图片生成模型（魔搭）",
                description="模型选择，可自定义，以下为示例",
                choices=[
                    "MusePublic/14_ckpt_SD_XL\"#万象熔炉 | Anything XL，社区模型(魔搭)","\n#cancel13/liaocao\"#潦草 lora 图片生成模型，社区模型（魔搭）","\n#doubao-seedream-3-0-t2i-250415\"#豆包图片生成模型约 0.3￥ 一张图（火山）",
                    "\n#gpt-image-1\"#GPT 生图，约 1.3￥ 一张图（chatany）"
                    ]
            ),
            "default_size": ConfigField(
                type=str,
                default="1024x1024",
                description="默认图片尺寸",
                example="1024x1024",
                choices=["1024x1024", "1024x1280", "1280x1024", "1024x1536", "1536x1024"],
            ),
            "default_watermark": ConfigField(
                type=bool, 
                default=True, 
                description="是否默认添加水印"),
            "default_guidance_scale": ConfigField(
                type=float, 
                default=2.5, 
                description="模型指导强度，影响图片与提示的关联性", example="2.0"
            ),
            "default_seed": ConfigField(
                type=int, 
                default=42, 
                description="随机种子，用于复现图片"),
            "custom_prompt_add": ConfigField(
                type=str,
                default="Nordic picture book art style, minimalist flat design, soft rounded lines, high saturation color blocks collision, dominant forest green and warm orange palette, low contrast lighting, hand-drawn pencil texture, healing fairy-tale atmosphere, geometric natural forms, ample white space composition, warm and clean aesthetic,liaocao\"#北欧绘本艺术风格，简约扁平设计，柔和圆润线条，高饱和度色块碰撞，森林绿与暖橙主色调，低对比度光影，手绘铅笔质感，治愈系童话氛围，几何化自然形态，留白构图，温暖干净画面",
                description="正面附加提示词（尽量使用英文，且用词语和逗号的形式，豆包可以使用中文句子提示词）"
            ),
            "negative_prompt_add": ConfigField(
                type=str,
                default="Pornography,nudity,lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry\"#色情，裸体，低分辨率，糟糕的解剖结构，糟糕的手，文字，错误，缺失的手指，多余的数字，更少的数字，裁剪，最差的质量，低质量，正常质量，jpeg文物，签名，水印，用户名，模糊色情，裸体，低分辨率，糟糕的解剖结构，糟糕的手，文字，错误，缺失的手指，多余的数字，更少的数字，裁剪，最差的质量，低质量，正常质量，jpeg文物，签名，水印，用户名，模糊",
                description="负面附加提示词，保持默认或使用豆包时可留空"
            ),
        },
        "cache": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用请求缓存"),
            "max_size": ConfigField(type=int, default=10, description="最大缓存数量"),
        },
        "components": {
            "enable_image_generation": ConfigField(type=bool, default=True, description="是否启用图片生成Action")
        },
        "logging": {
            "level": ConfigField(
                type=str,
                default="INFO",
                description="日志记录级别",
                choices=["DEBUG", "INFO", "WARNING", "ERROR"]
            ),
            "prefix": ConfigField(type=str, default="[custom_pic_Plugin]", description="日志记录前缀", example="[custom_pic_Plugin]")
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # 从配置获取组件启用状态
        enable_image_generation = self.get_config("components.enable_image_generation", True)

        components = []

        if enable_image_generation:
            # 添加我们的Action
            components.append((Custom_Pic_Action.get_action_info(), Custom_Pic_Action))

        return components


