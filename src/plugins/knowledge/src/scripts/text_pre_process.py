import json


with open("raw.txt", "r", encoding="utf-8") as f:
    raw = f.read()

# 一行一行的处理文件
paragraphs = []
paragraph = ""
for line in raw.split("\n"):
    if line.strip() == "":
        # 有空行，表示段落结束
        if paragraph != "":
            paragraphs.append(paragraph)
            paragraph = ""
    else:
        paragraph += line + "\n"

if paragraph != "":
    paragraphs.append(paragraph)

with open("raw.json", "w", encoding="utf-8") as f:
    json.dump(paragraphs, f, ensure_ascii=False, indent=4)
