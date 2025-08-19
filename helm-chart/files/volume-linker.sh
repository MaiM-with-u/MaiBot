#!/bin/sh
# 此脚本用于覆盖core容器的默认启动命令
# 由于k8s与docker-compose的卷挂载方式有所不同，需要利用此脚本为一些文件和目录提前创建好软链接
# /MaiMBot/data是麦麦数据的实际挂载路径
# /MaiMBot/statistics是统计数据的实际挂载路径

set -e
echo "[VolumeLinker]Preparing volume..."

# 初次启动，在存储卷中检查并创建关键文件和目录
if [ -d /MaiMBot/data/plugins ]
then
  echo "[VolumeLinker]    '/MaiMBot/data/plugins' exists."
else
  mkdir /MaiMBot/data/plugins
fi
if [ -d /MaiMBot/data/logs ]
then
  echo "[VolumeLinker]    '/MaiMBot/data/logs' exists."
else
  mkdir /MaiMBot/data/logs
fi
if [ -f /MaiMBot/statistics/index.html ]
then
  echo "[VolumeLinker]    '/MaiMBot/statistics/index.html' exists."
else
  if [ -d /MaiMBot/statistics ]
  then
    touch /MaiMBot/statistics/index.html
  else
    echo "[VolumeLinker]    Statistics volume disabled."
  fi
fi

# 删除空的插件目录，准备创建软链接
rm -rf /MaiMBot/plugins

# 创建软链接，从存储卷链接到实际位置
ln -s /MaiMBot/data/plugins /MaiMBot/plugins
ln -s /MaiMBot/data/logs /MaiMBot/logs
ln -s /MaiMBot/statistics/index.html /MaiMBot/maibot_statistics.html

# 启动麦麦
echo "[VolumeLinker]Starting MaiBot..."
python bot.py
