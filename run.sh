#!/bin/bash

# 麦麦Bot一键安装脚本 by Cookie_987
# 适用于Arch/Ubuntu 24.10/Debian 12/CentOS 9
# 请小心使用任何一键脚本！

INSTALLER_VERSION="0.0.3"
LANG=C.UTF-8

# 如无法访问GitHub请修改此处镜像地址
GITHUB_REPO="https://ghfast.top/https://github.com/SengokuCola/MaiMBot.git"

# 颜色输出
GREEN="\e[32m"
RED="\e[31m"
RESET="\e[0m"

# 需要的基本软件包

declare -A REQUIRED_PACKAGES=(
    ["common"]="git sudo python3 curl gnupg"
    ["debian"]="python3-venv python3-pip"
    ["ubuntu"]="python3-venv python3-pip"
    ["centos"]="python3-pip"
    ["arch"]="python-virtualenv python-pip"
)

# 默认项目目录
DEFAULT_INSTALL_DIR="/opt/maimbot"

# 服务名称
SERVICE_NAME="maimbot-daemon"
SERVICE_NAME_WEB="maimbot-web"

IS_INSTALL_MONGODB=false
IS_INSTALL_NAPCAT=false
IS_INSTALL_DEPENDENCIES=false

# 检查是否已安装
check_installed() {
    [[ -f /etc/systemd/system/${SERVICE_NAME}.service ]]
}

# 加载安装信息
load_install_info() {
    if [[ -f /etc/maimbot_install.conf ]]; then
        source /etc/maimbot_install.conf
    else
        INSTALL_DIR="$DEFAULT_INSTALL_DIR"
        BRANCH="main"
    fi
}

# 显示管理菜单
show_menu() {
    while true; do
        choice=$(whiptail --title "麦麦Bot管理菜单" --menu "请选择要执行的操作：" 15 60 7 \
            "1" "启动麦麦Bot" \
            "2" "停止麦麦Bot" \
            "3" "重启麦麦Bot" \
            "4" "启动WebUI" \
            "5" "停止WebUI" \
            "6" "重启WebUI" \
            "7" "更新麦麦Bot及其依赖" \
            "8" "切换分支" \
            "9" "更新配置文件" \
            "10" "退出" 3>&1 1>&2 2>&3)

        [[ $? -ne 0 ]] && exit 0

        case "$choice" in
            1)
                systemctl start ${SERVICE_NAME}
                whiptail --msgbox "✅麦麦Bot已启动" 10 60
                ;;
            2)
                systemctl stop ${SERVICE_NAME}
                whiptail --msgbox "🛑麦麦Bot已停止" 10 60
                ;;
            3)
                systemctl restart ${SERVICE_NAME}
                whiptail --msgbox "🔄麦麦Bot已重启" 10 60
                ;;
            4)
                systemctl start ${SERVICE_NAME_WEB}
                whiptail --msgbox "✅WebUI已启动" 10 60
                ;;
            5)
                systemctl stop ${SERVICE_NAME_WEB}
                whiptail --msgbox "🛑WebUI已停止" 10 60
                ;;
            6)
                systemctl restart ${SERVICE_NAME_WEB}
                whiptail --msgbox "🔄WebUI已重启" 10 60
                ;;
            7)
                update_dependencies
                ;;
            8)
                switch_branch
                ;;
            9)
                update_config
                ;;
            10)
                exit 0
                ;;
            *)
                whiptail --msgbox "无效选项！" 10 60
                ;;
        esac
    done
}

# 更新依赖
update_dependencies() {
    cd "${INSTALL_DIR}/repo" || {
        whiptail --msgbox "🚫 无法进入安装目录！" 10 60
        return 1
    }
    if ! git pull origin "${BRANCH}"; then
        whiptail --msgbox "🚫 代码更新失败！" 10 60
        return 1
    fi
    source "${INSTALL_DIR}/venv/bin/activate"
    if ! pip install -r requirements.txt; then
        whiptail --msgbox "🚫 依赖安装失败！" 10 60
        deactivate
        return 1
    fi
    deactivate
    systemctl restart ${SERVICE_NAME}
    whiptail --msgbox "✅ 依赖已更新并重启服务！" 10 60
}

# 切换分支
switch_branch() {
    new_branch=$(whiptail --inputbox "请输入要切换的分支名称：" 10 60 "${BRANCH}" 3>&1 1>&2 2>&3)
    [[ -z "$new_branch" ]] && {
        whiptail --msgbox "🚫 分支名称不能为空！" 10 60
        return 1
    }

    cd "${INSTALL_DIR}/repo" || {
        whiptail --msgbox "🚫 无法进入安装目录！" 10 60
        return 1
    }

    if ! git ls-remote --exit-code --heads origin "${new_branch}" >/dev/null 2>&1; then
        whiptail --msgbox "🚫 分支 ${new_branch} 不存在！" 10 60
        return 1
    fi

    if ! git checkout "${new_branch}"; then
        whiptail --msgbox "🚫 分支切换失败！" 10 60
        return 1
    fi

    if ! git pull origin "${new_branch}"; then
        whiptail --msgbox "🚫 代码拉取失败！" 10 60
        return 1
    fi

    source "${INSTALL_DIR}/venv/bin/activate"
    pip install -r requirements.txt
    deactivate

    sed -i "s/^BRANCH=.*/BRANCH=${new_branch}/" /etc/maimbot_install.conf
    BRANCH="${new_branch}"
    check_eula
    systemctl restart ${SERVICE_NAME}
    whiptail --msgbox "✅ 已切换到分支 ${new_branch} 并重启服务！" 10 60
}

# 更新配置文件
update_config() {
    cd "${INSTALL_DIR}/repo" || {
        whiptail --msgbox "🚫 无法进入安装目录！" 10 60
        return 1
    }
    if [[ -f config/bot_config.toml ]]; then
        cp config/bot_config.toml config/bot_config.toml.bak
        whiptail --msgbox "📁 原配置文件已备份为 bot_config.toml.bak" 10 60
        source "${INSTALL_DIR}/venv/bin/activate"
        python3 config/auto_update.py
        deactivate
        whiptail --msgbox "🆕 已更新配置文件，请重启麦麦Bot！" 10 60
        return 0
    else
        whiptail --msgbox "🚫 未找到配置文件 bot_config.toml\n 请先运行一次麦麦Bot" 10 60
        return 1
    fi
}

check_eula() {
    # 首先计算当前EULA的MD5值
    current_md5=$(md5sum "${INSTALL_DIR}/repo/EULA.md" | awk '{print $1}')

    # 首先计算当前隐私条款文件的哈希值
    current_md5_privacy=$(md5sum "${INSTALL_DIR}/repo/PRIVACY.md" | awk '{print $1}')

    # 如果当前的md5值为空，则直接返回
    if [[ -z $current_md5 || -z $current_md5_privacy ]]; then
        whiptail --msgbox "🚫 未找到使用协议\n 请检查PRIVACY.md和EULA.md是否存在" 10 60
    fi

    # 检查eula.confirmed文件是否存在
    if [[ -f ${INSTALL_DIR}/repo/eula.confirmed ]]; then
        # 如果存在则检查其中包含的md5与current_md5是否一致
        confirmed_md5=$(cat ${INSTALL_DIR}/repo/eula.confirmed)
    else
        confirmed_md5=""
    fi

    # 检查privacy.confirmed文件是否存在
    if [[ -f ${INSTALL_DIR}/repo/privacy.confirmed ]]; then
        # 如果存在则检查其中包含的md5与current_md5是否一致
        confirmed_md5_privacy=$(cat ${INSTALL_DIR}/repo/privacy.confirmed)
    else
        confirmed_md5_privacy=""
    fi

    # 如果EULA或隐私条款有更新，提示用户重新确认
    if [[ $current_md5 != $confirmed_md5 || $current_md5_privacy != $confirmed_md5_privacy ]]; then
        whiptail --title "📜 使用协议更新" --yesno "检测到麦麦Bot EULA或隐私条款已更新。\nhttps://github.com/SengokuCola/MaiMBot/blob/main/EULA.md\nhttps://github.com/SengokuCola/MaiMBot/blob/main/PRIVACY.md\n\n您是否同意上述协议？ \n\n " 12 70
        if [[ $? -eq 0 ]]; then
            echo -n $current_md5 > ${INSTALL_DIR}/repo/eula.confirmed
            echo -n $current_md5_privacy > ${INSTALL_DIR}/repo/privacy.confirmed
        else
            exit 1
        fi
    fi

}

# ----------- 主安装流程 -----------
run_installation() {
    # 1/6: 检测是否安装 whiptail
    if ! command -v whiptail &>/dev/null; then
        echo -e "${RED}[1/6] whiptail 未安装，正在安装...${RESET}"

        # 这里的多系统适配很神人，但是能用（）

        apt update && apt install -y whiptail

        pacman -S --noconfirm libnewt

        yum install -y newt
    fi

    # 协议确认
    if ! (whiptail --title "ℹ️ [1/6] 使用协议" --yes-button "我同意" --no-button "我拒绝" --yesno "使用麦麦Bot及此脚本前请先阅读EULA协议及隐私协议\nhttps://github.com/SengokuCola/MaiMBot/blob/main/EULA.md\nhttps://github.com/SengokuCola/MaiMBot/blob/main/PRIVACY.md\n\n您是否同意上述协议？" 12 70); then
        exit 1
    fi

    # 欢迎信息
    whiptail --title "[2/6] 欢迎使用麦麦Bot一键安装脚本 by Cookie987" --msgbox "检测到您未安装麦麦Bot，将自动进入安装流程，安装完成后再次运行此脚本即可进入管理菜单。\n\n项目处于活跃开发阶段，代码可能随时更改\n文档未完善，有问题可以提交 Issue 或者 Discussion\nQQ机器人存在被限制风险，请自行了解，谨慎使用\n由于持续迭代，可能存在一些已知或未知的bug\n由于开发中，可能消耗较多token\n\n本脚本可能更新不及时，如遇到bug请优先尝试手动部署以确定是否为脚本问题" 17 60

    # 系统检查
    check_system() {
        if [[ "$(id -u)" -ne 0 ]]; then
            whiptail --title "🚫 权限不足" --msgbox "请使用 root 用户运行此脚本！\n执行方式: sudo bash $0" 10 60
            exit 1
        fi

        if [[ -f /etc/os-release ]]; then
            source /etc/os-release
            if [[ "$ID" == "debian" && "$VERSION_ID" == "12" ]]; then
                return
            elif [[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.10" ]]; then
                return
            elif [[ "$ID" == "centos" && "$VERSION_ID" == "9" ]]; then
                return
            elif [[ "$ID" == "arch" ]]; then
                whiptail --title "⚠️ 兼容性警告" --msgbox "NapCat无可用的 Arch Linux 官方安装方法，将无法自动安装NapCat。\n\n您可尝试在AUR中搜索相关包。" 10 60
                whiptail --title "⚠️ 兼容性警告" --msgbox "MongoDB无可用的 Arch Linux 官方安装方法，将无法自动安装MongoDB。\n\n您可尝试在AUR中搜索相关包。" 10 60
                return
            else
                whiptail --title "🚫 不支持的系统" --msgbox "此脚本仅支持 Arch/Debian 12 (Bookworm)/Ubuntu 24.10 (Oracular Oriole)/CentOS9！\n当前系统: $PRETTY_NAME\n安装已终止。" 10 60
                exit 1
            fi
        else
            whiptail --title "⚠️ 无法检测系统" --msgbox "无法识别系统版本，安装已终止。" 10 60
            exit 1
        fi
    }
    check_system

    # 设置包管理器
    case "$ID" in
        debian|ubuntu)
            PKG_MANAGER="apt"
            ;;
        centos)
            PKG_MANAGER="yum"
            ;;
        arch)  
            # 添加arch包管理器
            PKG_MANAGER="pacman"
            ;;
    esac

    # 检查MongoDB
    check_mongodb() {
        if command -v mongod &>/dev/null; then
            MONGO_INSTALLED=true
        else
            MONGO_INSTALLED=false
        fi
    }
    check_mongodb

    # 检查NapCat
    check_napcat() {
        if command -v napcat &>/dev/null; then
            NAPCAT_INSTALLED=true
        else
            NAPCAT_INSTALLED=false
        fi
    }
    check_napcat

    # 安装必要软件包
    install_packages() {
        missing_packages=()
        # 检查 common 及当前系统专属依赖
        for package in ${REQUIRED_PACKAGES["common"]} ${REQUIRED_PACKAGES["$ID"]}; do
            case "$PKG_MANAGER" in
            apt)
                dpkg -s "$package" &>/dev/null || missing_packages+=("$package")
                ;;
            yum)
                rpm -q "$package" &>/dev/null || missing_packages+=("$package")
                ;;
            pacman)
                pacman -Qi "$package" &>/dev/null || missing_packages+=("$package")
                ;;
            esac
        done

        if [[ ${#missing_packages[@]} -gt 0 ]]; then
            whiptail --title "📦 [3/6] 依赖检查" --yesno "以下软件包缺失:\n${missing_packages[*]}\n\n是否自动安装？" 10 60
            if [[ $? -eq 0 ]]; then
                IS_INSTALL_DEPENDENCIES=true
            else
                whiptail --title "⚠️ 注意" --yesno "未安装某些依赖，可能影响运行！\n是否继续？" 10 60 || exit 1
            fi
        fi
    }
    install_packages

    # 安装MongoDB
    install_mongodb() {
        [[ $MONGO_INSTALLED == true ]] && return
        whiptail --title "📦 [3/6] 软件包检查" --yesno "检测到未安装MongoDB，是否安装？\n如果您想使用远程数据库，请跳过此步。" 10 60 && {
            IS_INSTALL_MONGODB=true
        }
    }

    # 仅在非Arch系统上安装MongoDB
    [[ "$ID" != "arch" ]] && install_mongodb
       

    # 安装NapCat
    install_napcat() {
        [[ $NAPCAT_INSTALLED == true ]] && return
        whiptail --title "📦 [3/6] 软件包检查" --yesno "检测到未安装NapCat，是否安装？\n如果您想使用远程NapCat，请跳过此步。" 10 60 && {
            IS_INSTALL_NAPCAT=true
        }
    }

    # 仅在非Arch系统上安装NapCat
    [[ "$ID" != "arch" ]] && install_napcat

    # Python版本检查
    check_python() {
        PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if ! python3 -c "import sys; exit(0) if sys.version_info >= (3,9) else exit(1)"; then
            whiptail --title "⚠️ [4/6] Python 版本过低" --msgbox "检测到 Python 版本为 $PYTHON_VERSION，需要 3.9 或以上！\n请升级 Python 后重新运行本脚本。" 10 60
            exit 1
        fi
    }

    # 如果没安装python则不检查python版本
    if command -v python3 &>/dev/null; then
        check_python
    fi
    

    # 选择分支
    choose_branch() {
        BRANCH=$(whiptail --title "🔀 [5/6] 选择麦麦Bot分支" --menu "请选择要安装的麦麦Bot分支：" 15 60 2 \
            "main" "稳定版本（推荐，供下载使用）" \
            "main-fix" "生产环境紧急修复" 3>&1 1>&2 2>&3)
        [[ -z "$BRANCH" ]] && BRANCH="main"
    }
    choose_branch

    # 选择安装路径
    choose_install_dir() {
        INSTALL_DIR=$(whiptail --title "📂 [6/6] 选择安装路径" --inputbox "请输入麦麦Bot的安装目录：" 10 60 "$DEFAULT_INSTALL_DIR" 3>&1 1>&2 2>&3)
        [[ -z "$INSTALL_DIR" ]] && {
            whiptail --title "⚠️ 取消输入" --yesno "未输入安装路径，是否退出安装？" 10 60 && exit 1
            INSTALL_DIR="$DEFAULT_INSTALL_DIR"
        }
    }
    choose_install_dir

    # 确认安装
    confirm_install() {
        local confirm_msg="请确认以下信息：\n\n"
        confirm_msg+="📂 安装麦麦Bot到: $INSTALL_DIR\n"
        confirm_msg+="🔀 分支: $BRANCH\n"
        [[ $IS_INSTALL_DEPENDENCIES == true ]] && confirm_msg+="📦 安装依赖：${missing_packages[@]}\n"
        [[ $IS_INSTALL_MONGODB == true || $IS_INSTALL_NAPCAT == true ]] && confirm_msg+="📦 安装额外组件：\n"
        
        [[ $IS_INSTALL_MONGODB == true ]] && confirm_msg+="  - MongoDB\n"
        [[ $IS_INSTALL_NAPCAT == true ]] && confirm_msg+="  - NapCat\n"
        confirm_msg+="\n注意：本脚本默认使用ghfast.top为GitHub进行加速，如不想使用请手动修改脚本开头的GITHUB_REPO变量。"

        whiptail --title "🔧 安装确认" --yesno "$confirm_msg" 20 60 || exit 1
    }
    confirm_install

    # 开始安装
    echo -e "${GREEN}安装${missing_packages[@]}...${RESET}"
    
    if [[ $IS_INSTALL_DEPENDENCIES == true ]]; then
        case "$PKG_MANAGER" in
        apt)
            apt update && apt install -y "${missing_packages[@]}"
            ;;
        yum)
            yum install -y "${missing_packages[@]}" --nobest
            ;;
        pacman)
            pacman -S --noconfirm "${missing_packages[@]}"
            ;;
        esac
    fi

    if [[ $IS_INSTALL_MONGODB == true ]]; then
        echo -e "${GREEN}安装 MongoDB...${RESET}"
        case "$ID" in
            debian)
                curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor
                echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" | tee /etc/apt/sources.list.d/mongodb-org-8.0.list
                apt update
                apt install -y mongodb-org
                systemctl enable --now mongod
                ;;
            ubuntu)
                curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor
                echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" | tee /etc/apt/sources.list.d/mongodb-org-8.0.list
                apt update
                apt install -y mongodb-org
                systemctl enable --now mongod
                ;;
            centos)
                cat > /etc/yum.repos.d/mongodb-org-8.0.repo <<EOF
[mongodb-org-8.0]
name=MongoDB Repository
baseurl=https://repo.mongodb.org/yum/redhat/9/mongodb-org/8.0/x86_64/
gpgcheck=1
enabled=1
gpgkey=https://pgp.mongodb.com/server-8.0.asc
EOF
                yum install -y mongodb-org
                systemctl enable --now mongod
                ;;
        esac

    fi

    if [[ $IS_INSTALL_NAPCAT == true ]]; then
        echo -e "${GREEN}安装 NapCat...${RESET}"
        curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh --cli y --docker n
    fi

    echo -e "${GREEN}创建安装目录...${RESET}"
    mkdir -p "$INSTALL_DIR"
    cd "$INSTALL_DIR" || exit 1

    echo -e "${GREEN}设置Python虚拟环境...${RESET}"
    python3 -m venv venv
    source venv/bin/activate

    echo -e "${GREEN}克隆仓库...${RESET}"
    git clone -b "$BRANCH" "$GITHUB_REPO" repo || {
        echo -e "${RED}克隆仓库失败！${RESET}"
        exit 1
    }

    echo -e "${GREEN}安装Python依赖...${RESET}"
    pip install -r repo/requirements.txt

    echo -e "${GREEN}同意协议...${RESET}"

    # 首先计算当前EULA的MD5值
    current_md5=$(md5sum "repo/EULA.md" | awk '{print $1}')

    # 首先计算当前隐私条款文件的哈希值
    current_md5_privacy=$(md5sum "repo/PRIVACY.md" | awk '{print $1}')

    echo -n $current_md5 > repo/eula.confirmed
    echo -n $current_md5_privacy > repo/privacy.confirmed

    echo -e "${GREEN}创建系统服务...${RESET}"
    cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=麦麦Bot 主进程
After=network.target mongod.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/repo
ExecStart=$INSTALL_DIR/venv/bin/python3 bot.py
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/${SERVICE_NAME_WEB}.service <<EOF
[Unit]
Description=麦麦Bot WebUI
After=network.target mongod.service ${SERVICE_NAME}.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/repo
ExecStart=$INSTALL_DIR/venv/bin/python3 webui.py
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable ${SERVICE_NAME}

    # 保存安装信息
    echo "INSTALLER_VERSION=${INSTALLER_VERSION}" > /etc/maimbot_install.conf
    echo "INSTALL_DIR=${INSTALL_DIR}" >> /etc/maimbot_install.conf
    echo "BRANCH=${BRANCH}" >> /etc/maimbot_install.conf

    whiptail --title "🎉 安装完成" --msgbox "麦麦Bot安装完成！\n已创建系统服务：${SERVICE_NAME}，${SERVICE_NAME_WEB}\n\n使用以下命令管理服务：\n启动服务：systemctl start ${SERVICE_NAME}\n查看状态：systemctl status ${SERVICE_NAME}" 14 60
}

# ----------- 主执行流程 -----------
# 检查root权限
[[ $(id -u) -ne 0 ]] && {
    echo -e "${RED}请使用root用户运行此脚本！${RESET}"
    exit 1
}

# 如果已安装显示菜单，并检查协议是否更新
if check_installed; then
    load_install_info
    check_eula
    show_menu
else
    run_installation
    # 安装完成后询问是否启动
    if whiptail --title "安装完成" --yesno "是否立即启动麦麦Bot服务？" 10 60; then
        systemctl start ${SERVICE_NAME}
        whiptail --msgbox "✅ 服务已启动！\n使用 systemctl status ${SERVICE_NAME} 查看状态" 10 60
    fi
fi
