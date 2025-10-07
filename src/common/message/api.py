from src.common.server import get_global_server
import os
from maim_message import MessageServer
from src.common.logger import get_logger
from src.config.config import global_config

global_api = None


def get_global_api() -> MessageServer:  # sourcery skip: extract-method
    """获取全局MessageServer实例"""
    global global_api
    if global_api is None:
        # 读取配置项
        maim_message_config = global_config.maim_message

        # 设置基本参数
        kwargs = {
            "host": os.environ["HOST"],
            "port": int(os.environ["PORT"]),
            "app": get_global_server().get_app(),
        }

        # 添加自定义logger
        maim_message_logger = get_logger("maim_message")
        kwargs["custom_logger"] = maim_message_logger

        # 添加token认证
        if maim_message_config.auth_token and len(maim_message_config.auth_token) > 0:
            kwargs["enable_token"] = True

        if maim_message_config.use_custom:
            # 添加WSS模式支持
            del kwargs["app"]
            kwargs["host"] = maim_message_config.host
            kwargs["port"] = maim_message_config.port
            kwargs["mode"] = maim_message_config.mode
            if maim_message_config.use_wss:
                if maim_message_config.cert_file:
                    kwargs["ssl_certfile"] = maim_message_config.cert_file
                if maim_message_config.key_file:
                    kwargs["ssl_keyfile"] = maim_message_config.key_file
            kwargs["enable_custom_uvicorn_logger"] = False

        global_api = MessageServer(**kwargs)
        if maim_message_config.auth_token:
            for token in maim_message_config.auth_token:
                global_api.add_valid_token(token)
    return global_api
