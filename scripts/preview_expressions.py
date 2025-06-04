import tkinter as tk
from tkinter import ttk
import json
import os
from pathlib import Path
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from collections import defaultdict


class ExpressionViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("表达方式预览器")
        self.root.geometry("1200x800")

        # 创建主框架
        self.main_frame = ttk.Frame(root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 创建左侧控制面板
        self.control_frame = ttk.Frame(self.main_frame)
        self.control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # 创建搜索框
        self.search_frame = ttk.Frame(self.control_frame)
        self.search_frame.pack(fill=tk.X, pady=(0, 10))

        self.search_var = tk.StringVar()
        self.search_var.trace("w", self.filter_expressions)
        self.search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(self.search_frame, text="搜索:").pack(side=tk.LEFT, padx=(0, 5))

        # 创建文件选择下拉框
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(self.search_frame, textvariable=self.file_var)
        self.file_combo.pack(side=tk.LEFT, padx=5)
        self.file_combo.bind("<<ComboboxSelected>>", self.load_file)

        # 创建排序选项
        self.sort_frame = ttk.LabelFrame(self.control_frame, text="排序选项")
        self.sort_frame.pack(fill=tk.X, pady=5)

        self.sort_var = tk.StringVar(value="count")
        ttk.Radiobutton(
            self.sort_frame, text="按计数排序", variable=self.sort_var, value="count", command=self.apply_sort
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            self.sort_frame, text="按情境排序", variable=self.sort_var, value="situation", command=self.apply_sort
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            self.sort_frame, text="按风格排序", variable=self.sort_var, value="style", command=self.apply_sort
        ).pack(anchor=tk.W)

        # 创建分群选项
        self.group_frame = ttk.LabelFrame(self.control_frame, text="分群选项")
        self.group_frame.pack(fill=tk.X, pady=5)

        self.group_var = tk.StringVar(value="none")
        ttk.Radiobutton(
            self.group_frame, text="不分群", variable=self.group_var, value="none", command=self.apply_grouping
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            self.group_frame, text="按情境分群", variable=self.group_var, value="situation", command=self.apply_grouping
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            self.group_frame, text="按风格分群", variable=self.group_var, value="style", command=self.apply_grouping
        ).pack(anchor=tk.W)

        # 创建相似度阈值滑块
        self.similarity_frame = ttk.LabelFrame(self.control_frame, text="相似度设置")
        self.similarity_frame.pack(fill=tk.X, pady=5)

        self.similarity_var = tk.DoubleVar(value=0.5)
        self.similarity_scale = ttk.Scale(
            self.similarity_frame,
            from_=0.0,
            to=1.0,
            variable=self.similarity_var,
            orient=tk.HORIZONTAL,
            command=self.update_similarity,
        )
        self.similarity_scale.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(self.similarity_frame, text="相似度阈值: 0.5").pack()

        # 创建显示选项
        self.view_frame = ttk.LabelFrame(self.control_frame, text="显示选项")
        self.view_frame.pack(fill=tk.X, pady=5)

        self.show_graph_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.view_frame, text="显示关系图", variable=self.show_graph_var, command=self.toggle_graph
        ).pack(anchor=tk.W)

        # 创建右侧内容区域
        self.content_frame = ttk.Frame(self.main_frame)
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 创建文本显示区域
        self.text_area = tk.Text(self.content_frame, wrap=tk.WORD)
        self.text_area.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 添加滚动条
        scrollbar = ttk.Scrollbar(self.text_area, command=self.text_area.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_area.config(yscrollcommand=scrollbar.set)

        # 创建图形显示区域
        self.graph_frame = ttk.Frame(self.content_frame)
        self.graph_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 初始化数据
        self.current_data = []
        self.graph = nx.Graph()
        self.canvas = None

        # 加载文件列表
        self.load_file_list()

    def load_file_list(self):
        expression_dir = Path("data/expression")
        files = []
        for root, _, filenames in os.walk(expression_dir):
            for filename in filenames:
                if filename.endswith(".json"):
                    rel_path = os.path.relpath(os.path.join(root, filename), expression_dir)
                    files.append(rel_path)

        self.file_combo["values"] = files
        if files:
            self.file_combo.set(files[0])
            self.load_file(None)

    def load_file(self, event):
        selected_file = self.file_var.get()
        if not selected_file:
            return

        file_path = os.path.join("data/expression", selected_file)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self.current_data = json.load(f)

            self.apply_sort()
            self.update_similarity()

        except Exception as e:
            self.text_area.delete(1.0, tk.END)
            self.text_area.insert(tk.END, f"加载文件时出错: {str(e)}")

    def apply_sort(self):
        if not self.current_data:
            return

        sort_key = self.sort_var.get()
        reverse = sort_key == "count"

        self.current_data.sort(key=lambda x: x.get(sort_key, ""), reverse=reverse)
        self.apply_grouping()

    def apply_grouping(self):
        if not self.current_data:
            return

        group_key = self.group_var.get()
        if group_key == "none":
            self.display_data(self.current_data)
            return

        grouped_data = defaultdict(list)
        for item in self.current_data:
            key = item.get(group_key, "未分类")
            grouped_data[key].append(item)

        self.text_area.delete(1.0, tk.END)
        for group, items in grouped_data.items():
            self.text_area.insert(tk.END, f"\n=== {group} ===\n\n")
            for item in items:
                self.text_area.insert(tk.END, f"情境: {item.get('situation', 'N/A')}\n")
                self.text_area.insert(tk.END, f"风格: {item.get('style', 'N/A')}\n")
                self.text_area.insert(tk.END, f"计数: {item.get('count', 'N/A')}\n")
                self.text_area.insert(tk.END, "-" * 50 + "\n")

    def display_data(self, data):
        self.text_area.delete(1.0, tk.END)
        for item in data:
            self.text_area.insert(tk.END, f"情境: {item.get('situation', 'N/A')}\n")
            self.text_area.insert(tk.END, f"风格: {item.get('style', 'N/A')}\n")
            self.text_area.insert(tk.END, f"计数: {item.get('count', 'N/A')}\n")
            self.text_area.insert(tk.END, "-" * 50 + "\n")

    def update_similarity(self, *args):
        if not self.current_data:
            return

        threshold = self.similarity_var.get()
        self.similarity_frame.winfo_children()[-1].config(text=f"相似度阈值: {threshold:.2f}")

        # 计算相似度
        texts = [f"{item['situation']} {item['style']}" for item in self.current_data]
        vectorizer = TfidfVectorizer()
        tfidf_matrix = vectorizer.fit_transform(texts)
        similarity_matrix = cosine_similarity(tfidf_matrix)

        # 创建图
        self.graph.clear()
        for i, item in enumerate(self.current_data):
            self.graph.add_node(i, label=f"{item['situation']}\n{item['style']}")

        # 添加边
        for i in range(len(self.current_data)):
            for j in range(i + 1, len(self.current_data)):
                if similarity_matrix[i, j] > threshold:
                    self.graph.add_edge(i, j, weight=similarity_matrix[i, j])

        if self.show_graph_var.get():
            self.draw_graph()

    def draw_graph(self):
        if self.canvas:
            self.canvas.get_tk_widget().destroy()

        fig = plt.figure(figsize=(8, 6))
        pos = nx.spring_layout(self.graph)

        # 绘制节点
        nx.draw_networkx_nodes(self.graph, pos, node_color="lightblue", node_size=1000, alpha=0.6)

        # 绘制边
        nx.draw_networkx_edges(self.graph, pos, alpha=0.4)

        # 添加标签
        labels = nx.get_node_attributes(self.graph, "label")
        nx.draw_networkx_labels(self.graph, pos, labels, font_size=8)

        plt.title("表达方式关系图")
        plt.axis("off")

        self.canvas = FigureCanvasTkAgg(fig, master=self.graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def toggle_graph(self):
        if self.show_graph_var.get():
            self.draw_graph()
        else:
            if self.canvas:
                self.canvas.get_tk_widget().destroy()
                self.canvas = None

    def filter_expressions(self, *args):
        search_text = self.search_var.get().lower()
        if not search_text:
            self.apply_sort()
            return

        filtered_data = []
        for item in self.current_data:
            situation = item.get("situation", "").lower()
            style = item.get("style", "").lower()
            if search_text in situation or search_text in style:
                filtered_data.append(item)

        self.display_data(filtered_data)


def main():
    root = tk.Tk()
    # app = ExpressionViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
