import json
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import matplotlib.pyplot as plt
import seaborn as sns
import sqlite3

# 设置中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]  # 使用微软雅黑
plt.rcParams["axes.unicode_minus"] = False  # 用来正常显示负号
plt.rcParams["font.family"] = "sans-serif"

# 获取脚本所在目录
SCRIPT_DIR = Path(__file__).parent


def get_group_name(stream_id):
    """从数据库中获取群组名称"""
    conn = sqlite3.connect("data/maibot.db")
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT group_name, user_nickname, platform 
        FROM chat_streams 
        WHERE stream_id = ?
    """,
        (stream_id,),
    )

    result = cursor.fetchone()
    conn.close()

    if result:
        group_name, user_nickname, platform = result
        if group_name:
            return group_name
        if user_nickname:
            return user_nickname
        if platform:
            return f"{platform}-{stream_id[:8]}"
    return stream_id


def load_group_data(group_dir):
    """加载单个群组的数据"""
    json_path = Path(group_dir) / "expressions.json"
    if not json_path.exists():
        return [], [], []

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    situations = []
    styles = []
    combined = []

    for item in data:
        count = item["count"]
        situations.extend([item["situation"]] * count)
        styles.extend([item["style"]] * count)
        combined.extend([f"{item['situation']} {item['style']}"] * count)

    return situations, styles, combined


def analyze_group_similarity():
    # 获取所有群组目录
    base_dir = Path("data/expression/learnt_style")
    group_dirs = [d for d in base_dir.iterdir() if d.is_dir()]
    group_ids = [d.name for d in group_dirs]

    # 获取群组名称
    group_names = [get_group_name(group_id) for group_id in group_ids]

    # 加载所有群组的数据
    group_situations = []
    group_styles = []
    group_combined = []

    for d in group_dirs:
        situations, styles, combined = load_group_data(d)
        group_situations.append(" ".join(situations))
        group_styles.append(" ".join(styles))
        group_combined.append(" ".join(combined))

    # 创建TF-IDF向量化器
    vectorizer = TfidfVectorizer()

    # 计算三种相似度矩阵
    situation_matrix = cosine_similarity(vectorizer.fit_transform(group_situations))
    style_matrix = cosine_similarity(vectorizer.fit_transform(group_styles))
    combined_matrix = cosine_similarity(vectorizer.fit_transform(group_combined))

    # 对相似度矩阵进行对数变换
    log_situation_matrix = np.log1p(situation_matrix)
    log_style_matrix = np.log1p(style_matrix)
    log_combined_matrix = np.log1p(combined_matrix)

    # 创建一个大图，包含三个子图
    plt.figure(figsize=(45, 12))

    # 场景相似度热力图
    plt.subplot(1, 3, 1)
    sns.heatmap(
        log_situation_matrix,
        xticklabels=group_names,
        yticklabels=group_names,
        cmap="YlOrRd",
        annot=True,
        fmt=".2f",
        vmin=0,
        vmax=np.log1p(0.2),
    )
    plt.title("群组场景相似度热力图 (对数变换)")
    plt.xticks(rotation=45, ha="right")

    # 表达方式相似度热力图
    plt.subplot(1, 3, 2)
    sns.heatmap(
        log_style_matrix,
        xticklabels=group_names,
        yticklabels=group_names,
        cmap="YlOrRd",
        annot=True,
        fmt=".2f",
        vmin=0,
        vmax=np.log1p(0.2),
    )
    plt.title("群组表达方式相似度热力图 (对数变换)")
    plt.xticks(rotation=45, ha="right")

    # 组合相似度热力图
    plt.subplot(1, 3, 3)
    sns.heatmap(
        log_combined_matrix,
        xticklabels=group_names,
        yticklabels=group_names,
        cmap="YlOrRd",
        annot=True,
        fmt=".2f",
        vmin=0,
        vmax=np.log1p(0.2),
    )
    plt.title("群组场景+表达方式相似度热力图 (对数变换)")
    plt.xticks(rotation=45, ha="right")

    plt.tight_layout()
    plt.savefig(SCRIPT_DIR / "group_similarity_heatmaps.png", dpi=300, bbox_inches="tight")
    plt.close()

    # 保存匹配详情到文本文件
    with open(SCRIPT_DIR / "group_similarity_details.txt", "w", encoding="utf-8") as f:
        f.write("群组相似度详情\n")
        f.write("=" * 50 + "\n\n")

        for i in range(len(group_ids)):
            for j in range(i + 1, len(group_ids)):
                if log_combined_matrix[i][j] > np.log1p(0.05):
                    f.write(f"群组1: {group_names[i]}\n")
                    f.write(f"群组2: {group_names[j]}\n")
                    f.write(f"场景相似度: {situation_matrix[i][j]:.4f}\n")
                    f.write(f"表达方式相似度: {style_matrix[i][j]:.4f}\n")
                    f.write(f"组合相似度: {combined_matrix[i][j]:.4f}\n")

                    # 获取两个群组的数据
                    situations1, styles1, _ = load_group_data(group_dirs[i])
                    situations2, styles2, _ = load_group_data(group_dirs[j])

                    # 找出共同的场景
                    common_situations = set(situations1) & set(situations2)
                    if common_situations:
                        f.write("\n共同场景:\n")
                        for situation in common_situations:
                            f.write(f"- {situation}\n")

                    # 找出共同的表达方式
                    common_styles = set(styles1) & set(styles2)
                    if common_styles:
                        f.write("\n共同表达方式:\n")
                        for style in common_styles:
                            f.write(f"- {style}\n")

                    f.write("\n" + "-" * 50 + "\n\n")


if __name__ == "__main__":
    analyze_group_similarity()
