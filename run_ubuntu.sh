#!/bin/bash

# 麦麦Bot一键安装脚本
# 适用于Ubuntu-latest

# 检查必要的库
REQUIRED_PACKAGES=("git" "sudo" "python3" "python3-venv" "curl" "gnupg" "python3-pip")

# 默认项目目录
DEFAULT_INSTALL_DIR="/opt/maimbot"

# 服务名称
SERVICE_NAME="MaiBot"

# 颜色输出
GREEN="\e[32m"
RED="\e[31m"
RESET="\e[0m"

# 需要的基本软件包

# 检查网络连接
check_network() {
    echo -e "${GREEN}正在检查网络连接...${RESET}"
    
    # 尝试连接 GitHub
    if curl -s --connect-timeout 5 https://github.com > /dev/null; then
        echo -e "${GREEN}网络连接正常！${RESET}"
        return 0
    else
        echo -e "${RED}无法连接到 GitHub，请检查网络连接或代理设置${RESET}"
        echo -e "${RED}如果使用代理，请确保代理服务器正常运行${RESET}"
        return 1
    fi  
}

# 主函数
main() {
    # 检查网络连接
    if ! check_network; then
        exit 1
    fi
    
    # 后续安装步骤...
}

# 运行主函数
main

