import json
import os
from pathlib import Path
import sys  # 新增系统模块导入


def check_and_create_dirs():
    """检查并创建必要的目录"""
    required_dirs = ["data/lpmm_raw_data", "data/imported_lpmm_data"]

    for dir_path in required_dirs:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            print(f"已创建目录: {dir_path}")


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
    print("=== 重要操作确认 ===")
    print("如果你并非第一次导入知识")
    print("请先删除data/import.json文件，备份data/openie.json文件")
    print("在进行知识库导入之前")
    print("请修改config/lpmm_config.toml中的配置项")
    confirm = input("确认继续执行？(y/n): ").strip().lower()
    if confirm != "y":
        print("操作已取消")
        sys.exit(1)
    print("\n" + "=" * 40 + "\n")

    # 检查并创建必要的目录
    check_and_create_dirs()

    # 检查输出文件是否存在
    if os.path.exists("data/import.json"):
        print("错误: data/import.json 已存在，请先处理或删除该文件")
        sys.exit(1)

    if os.path.exists("data/openie.json"):
        print("错误: data/openie.json 已存在，请先处理或删除该文件")
        sys.exit(1)

    # 获取所有原始文本文件
    raw_files = list(Path("data/lpmm_raw_data").glob("*.txt"))
    if not raw_files:
        print("警告: data/lpmm_raw_data 中没有找到任何 .txt 文件")
        sys.exit(1)

    # 处理所有文件
    all_paragraphs = []
    for file in raw_files:
        print(f"正在处理文件: {file.name}")
        paragraphs = process_text_file(file)
        all_paragraphs.extend(paragraphs)

    # 保存合并后的结果
    output_path = "data/import.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_paragraphs, f, ensure_ascii=False, indent=4)

    print(f"处理完成，结果已保存到: {output_path}")


if __name__ == "__main__":
    main()
