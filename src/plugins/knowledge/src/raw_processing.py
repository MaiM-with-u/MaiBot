import json
import os

from .global_logger import logger
from .lpmmconfig import global_config
from .utils.hash import get_sha256


def load_raw_data() -> tuple[list[str], list[str]]:
    """加载原始数据文件

    读取原始数据文件，将原始数据加载到内存中

    Returns:
        - raw_data: 原始数据字典
        - md5_set: 原始数据的SHA256集合
    """
    # 读取import.json文件
    if os.path.exists(global_config["persistence"]["raw_data_path"]) is True:
        with open(global_config["persistence"]["raw_data_path"], "r", encoding="utf-8") as f:
            import_json = json.loads(f.read())
    else:
        raise Exception("原始数据文件读取失败")
    # import_json内容示例：
    # import_json = [
    #       "The capital of China is Beijing. The capital of France is Paris.",
    # ]
    raw_data = []
    sha256_list = []
    sha256_set = set()
    for item in import_json:
        if not isinstance(item, str):
            logger.warning("数据类型错误：{}".format(item))
            continue
        pg_hash = get_sha256(item)
        if pg_hash in sha256_set:
            logger.warning("重复数据：{}".format(item))
            continue
        sha256_set.add(pg_hash)
        sha256_list.append(pg_hash)
        raw_data.append(item)
    logger.info("共读取到{}条数据".format(len(raw_data)))

    return sha256_list, raw_data
