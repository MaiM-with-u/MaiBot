import os
from pathlib import Path
import sys  # 新增系统模块导入
from src.chat.knowledge.utils.hash import get_sha256
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.common.logger import get_logger

logger = get_logger("lpmm")
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_DATA_PATH = os.path.join(ROOT_PATH, "data/lpmm_raw_data")
# IMPORTED_DATA_PATH = os.path.join(ROOT_PATH, "data/imported_lpmm_data")

def _process_text_file(file_path):
    """处理单个文本文件，返回段落列表"""
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()

    paragraphs = []
    paragraph = ""
    for line in raw.split("\n"):
        if line.strip() == "":
            if paragraph != "":
                paragraphs.append(paragraph.strip())
                paragraph = ""
        else:
            paragraph += line + "\n"

    if paragraph != "":
        paragraphs.append(paragraph.strip())

    return paragraphs


def _process_multi_files() -> list:
    raw_files = list(Path(RAW_DATA_PATH).glob("*.txt"))
    if not raw_files:
        logger.warning("警告: data/lpmm_raw_data 中没有找到任何 .txt 文件")
        sys.exit(1)
    # 处理所有文件
    all_paragraphs = []
    for file in raw_files:
        logger.info(f"正在处理文件: {file.name}")
        paragraphs = _process_text_file(file)
        all_paragraphs.extend(paragraphs)
    return all_paragraphs

def load_raw_data() -> tuple[list[str], list[str]]:
    """加载原始数据文件

    读取原始数据文件，将原始数据加载到内存中

    Args:
        path: 可选，指定要读取的json文件绝对路径

    Returns:
        - raw_data: 原始数据列表
        - sha256_list: 原始数据的SHA256集合
    """
    raw_data = _process_multi_files()
    sha256_list = []
    sha256_set = set()
    for item in raw_data:
        if not isinstance(item, str):
            logger.warning(f"数据类型错误：{item}")
            continue
        pg_hash = get_sha256(item)
        if pg_hash in sha256_set:
            logger.warning(f"重复数据：{item}")
            continue
        sha256_set.add(pg_hash)
        sha256_list.append(pg_hash)
        raw_data.append(item)
    logger.info(f"共读取到{len(raw_data)}条数据")

    return sha256_list, raw_data

# chatgpt
import json
from datetime import datetime

if __name__ == "__main__":
    sha256_list, raw_data = load_raw_data()

    # 构造导出路径
    output_dir = os.path.join(ROOT_PATH, "data/imported_lpmm_data")
    os.makedirs(output_dir, exist_ok=True)

    now = datetime.now().strftime("%m-%d-%H-%M")
    output_path = os.path.join(output_dir, f"{now}-imported-data.json")

    # 写入 JSON 文件
    # with open(output_path, "w", encoding="utf-8") as f:
    #     json.dump({
    #         "sha256_list": sha256_list,
    #         "raw_data": raw_data
    #     }, f, ensure_ascii=False, indent=2)
    # 上面那些是AI写的，我看了下旧版生成的文件格式，实际上只保存[raw_data]就行，后面的info_extraction.py和import_openie.py会正常运行
    # 下面这段只保存 raw_data，和旧版保存的文件格式一致
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    logger.info(f"数据处理完成，已写入：{output_path}")