#!/bin/sh
# 这个脚本的作用是安装必要的python包，为adapter-cm-generator.py脚本做准备

pip3 install -i https://mirrors.ustc.edu.cn/pypi/simple kubernetes toml
python3 adapter-cm-generator.py
