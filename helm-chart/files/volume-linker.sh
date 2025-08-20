#!/bin/sh
# 此脚本用于覆盖core容器的默认启动命令
# 由于k8s与docker-compose的卷挂载方式有所不同，需要利用此脚本为一些文件和目录提前创建好软链接
# /MaiMBot/data是麦麦数据的实际挂载路径
# /MaiMBot/statistics是统计数据的实际挂载路径

set -e
echo "[VolumeLinker]Preparing volume..."

# 初次启动，在存储卷中检查并创建关键文件和目录
mkdir -p /MaiMBot/data/plugins
mkdir -p /MaiMBot/data/logs
if [ ! -d "/MaiMBot/statistics" ]
then
  echo "[VolumeLinker]    Statistics volume disabled."
else
  touch /MaiMBot/statistics/index.html
fi

# 删除空的插件目录，准备创建软链接
rm -rf /MaiMBot/plugins

# 创建软链接，从存储卷链接到实际位置
ln -s /MaiMBot/data/plugins /MaiMBot/plugins
ln -s /MaiMBot/data/logs /MaiMBot/logs
if [ -f "/MaiMBot/statistics/index.html" ]
then
  ln -s /MaiMBot/statistics/index.html /MaiMBot/maibot_statistics.html
fi

# 启动麦麦
echo "[VolumeLinker]Starting MaiBot..."
python bot.py
