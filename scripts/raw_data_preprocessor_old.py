import json
import os
from pathlib import Path
import sys  # 新增系统模块导入
import datetime  # 新增导入

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.common.logger import get_logger
from src.chat.knowledge.lpmmconfig import global_config

logger = get_logger("lpmm")
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_DATA_PATH = os.path.join(ROOT_PATH, "data/lpmm_raw_data")
# 新增：确保 RAW_DATA_PATH 存在
if not os.path.exists(RAW_DATA_PATH):
    os.makedirs(RAW_DATA_PATH, exist_ok=True)
    logger.info(f"已创建目录: {RAW_DATA_PATH}")

if global_config.get("persistence", {}).get("raw_data_path") is not None:
    IMPORTED_DATA_PATH = os.path.join(ROOT_PATH, global_config["persistence"]["raw_data_path"])
else:
    IMPORTED_DATA_PATH = os.path.join(ROOT_PATH, "data/imported_lpmm_data")

# 添加项目根目录到 sys.path


def check_and_create_dirs():
    """检查并创建必要的目录"""
    required_dirs = [RAW_DATA_PATH, IMPORTED_DATA_PATH]

    for dir_path in required_dirs:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            logger.info(f"已创建目录: {dir_path}")


def process_text_file(file_path):
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


def main():
    # 新增用户确认提示
    print("=== 数据预处理脚本 ===")
    print(f"本脚本将处理 '{RAW_DATA_PATH}' 目录下的所有 .txt 文件。")
    print(f"处理后的段落数据将合并，并以 MM-DD-HH-SS-imported-data.json 的格式保存在 '{IMPORTED_DATA_PATH}' 目录中。")
    print("请确保原始数据已放置在正确的目录中。")
    confirm = input("确认继续执行？(y/n): ").strip().lower()
    if confirm != "y":
        logger.info("操作已取消")
        sys.exit(1)
    print("\n" + "=" * 40 + "\n")

    # 检查并创建必要的目录
    check_and_create_dirs()

    # # 检查输出文件是否存在
    # if os.path.exists(RAW_DATA_PATH):
    #     logger.error("错误: data/import.json 已存在，请先处理或删除该文件")
    #     sys.exit(1)

    # if os.path.exists(RAW_DATA_PATH):
    #     logger.error("错误: data/openie.json 已存在，请先处理或删除该文件")
    #     sys.exit(1)

    # 获取所有原始文本文件
    raw_files = list(Path(RAW_DATA_PATH).glob("*.txt"))
    if not raw_files:
        logger.warning("警告: data/lpmm_raw_data 中没有找到任何 .txt 文件")
        sys.exit(1)

    # 处理所有文件
    all_paragraphs = []
    for file in raw_files:
        logger.info(f"正在处理文件: {file.name}")
        paragraphs = process_text_file(file)
        all_paragraphs.extend(paragraphs)

    # 保存合并后的结果到 IMPORTED_DATA_PATH，文件名格式为 MM-DD-HH-ss-imported-data.json
    now = datetime.datetime.now()
    filename = now.strftime("%m-%d-%H-%S-imported-data.json")
    output_path = os.path.join(IMPORTED_DATA_PATH, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_paragraphs, f, ensure_ascii=False, indent=4)

    logger.info(f"处理完成，结果已保存到: {output_path}")


if __name__ == "__main__":
    logger.info(f"原始数据路径: {RAW_DATA_PATH}")
    logger.info(f"处理后的数据路径: {IMPORTED_DATA_PATH}")
    main()