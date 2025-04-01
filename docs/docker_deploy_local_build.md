# 麦麦的docker本地部署教程

> **适用系统**：Debian系Linux（Ubuntu/Debian及其衍生系统）
> **系统要求**：推荐2核2G内存
> **测试环境**：Ubuntu Server 24.04 LTS（全新安装）
> **部署方式**：本地镜像构建

## 1 更新系统并安装docker

### 更新系统

> apt换源(可选)
>
> ```shell
> cat <<'EOF' > /etc/apt/sources.list.d/ubuntu.sources
> Types: deb
> URIs: https://mirror.nju.edu.cn/ubuntu
> Suites: noble noble-updates noble-backports
> Components: main restricted universe multiverse
> Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
>
> Types: deb
> URIs: http://security.ubuntu.com/ubuntu/
> Suites: noble-security
> Components: main restricted universe multiverse
> Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
> EOF
> ```

更新系统

```shell
sudo apt-get update
sudo apt-get upgrade -y
```

安装一些基础软件包

```shell
sudo apt-get install ca-certificates wget curl gnupg vim -y
```

### 安装docker

使用脚本安装docker

```shell
# 此处使用了GitHub镜像代理
wget https://gh-proxy.net/https://raw.githubusercontent.com/docker/docker-install/master/install.sh
# 赋权
chmod +x install.sh
# 使用镜像源安装
./install.sh --mirror Aliyun
```

出现docker信息即为成功

```shell
+ sudo -E sh -c docker version
Client: Docker Engine - Community
 Version:           28.0.4
 API version:       1.48
 Go version:        go1.23.7
 Git commit:        b8034c0
 Built:             Tue Mar 25 15:07:16 2025
 OS/Arch:           linux/amd64
 Context:           default

Server: Docker Engine - Community
 Engine:
  Version:          28.0.4
  API version:      1.48 (minimum version 1.24)
  Go version:       go1.23.7
  Git commit:       6430e49
  Built:            Tue Mar 25 15:07:16 2025
  OS/Arch:          linux/amd64
  Experimental:     false
 containerd:
  Version:          1.7.26
  GitCommit:        753481ec61c7c8955a23d6ff7bc8e4daed455734
 runc:
  Version:          1.2.5
  GitCommit:        v1.2.5-0-g59923ef
 docker-init:
  Version:          0.19.0
  GitCommit:        de40ad0

================================================================================

To run Docker as a non-privileged user, consider setting up the
Docker daemon in rootless mode for your user:

    dockerd-rootless-setuptool.sh install

Visit https://docs.docker.com/go/rootless/ to learn about rootless mode.


To run the Docker daemon as a fully privileged service, but granting non-root
users access, refer to https://docs.docker.com/go/daemon-access/

WARNING: Access to the remote API on a privileged Docker daemon is equivalent
         to root access on the host. Refer to the 'Docker daemon attack surface'
         documentation for details: https://docs.docker.com/go/attack-surface/

================================================================================
```

docker跟换镜像源

```shell
sudo mkdir -p /etc/docker

sudo tee /etc/docker/daemon.json <<EOF
{
    "registry-mirrors": [
        "https://docker.1ms.run",
        "https://docker.xuanyuan.me"
    ]
}
EOF
```

重载docker

```shell
sudo systemctl daemon-reload
sudo systemctl restart docker
```

## 2 开始安装麦麦

选择分支：

> 稳定版： `main`

> 开发版：`main-fix`

### 克隆麦麦

稳定版执行：

```shell
git clone -b main https://gh-proxy.net/https://github.com/MaiM-with-u/MaiBot.git
```

开发版执行：

```shell
git clone -b main-fix https://gh-proxy.net/https://github.com/MaiM-with-u/MaiBot.git
```

克隆完成后会出现 `MaiBot`文件夹

成功执行示例

***非命令请勿执行***

```shell
test@test:~$ git clone -b main https://gh-proxy.net/https://github.com/MaiM-with-u/MaiBot.git
Cloning into 'MaiBot'...
remote: Enumerating objects: 6876, done.
remote: Counting objects: 100% (2139/2139), done.
remote: Compressing objects: 100% (328/328), done.
remote: Total 6876 (delta 1972), reused 1848 (delta 1807), pack-reused 4737 (from 2)
Receiving objects: 100% (6876/6876), 3.51 MiB | 120.00 KiB/s, done.
Resolving deltas: 100% (4701/4701), done.
test@test:~$ ll
total 60
drwxr-x--- 5 test test  4096 Mar 28 15:01 ./
drwxr-xr-x 3 root root  4096 Mar 28 14:17 ../
-rw-r--r-- 1 test test   220 Mar 31  2024 .bash_logout
-rw-r--r-- 1 test test  3771 Mar 31  2024 .bashrc
drwx------ 2 test test  4096 Mar 28 14:17 .cache/
-rwxrwxr-x 1 test test 22577 Mar 28 14:36 install.sh*
drwxrwxr-x 9 test test  4096 Mar 28 15:01 MaiBot/
-rw-r--r-- 1 test test   807 Mar 31  2024 .profile
drwx------ 2 test test  4096 Mar 28 14:17 .ssh/
-rw-r--r-- 1 test test     0 Mar 28 14:29 .sudo_as_admin_successful
-rw-rw-r-- 1 test test   167 Mar 28 14:36 .wget-hsts
```

然后进入到 `MaiBot`中并创建配置文件文件夹

```shell
# 进入到 MaiBot
cd MaiBot
# 创建文件夹docker-config
mkdir docker-config
```

### 配置文件

创建配置文件

```shell
# bot_config.toml
cp template/bot_config_template.toml docker-config/bot_config.toml
# .env.prod
cp template.env docker-config/.env.prod
```

修改配置文件

- 请前往 [🎀 新手配置指南](./installation_cute.md) 或 [⚙️ 标准配置指南](./installation_standard.md) 完成`.env.prod`与`bot_config.toml`配置文件的编写

```shell
vim docker-config/bot_config.toml
vim docker-config/.env.prod
```

> `vim`基础使用：
>
> `:w`保存
>
> `:wq` 保存退出
>
> `:q`退出
>
> `:q!`强制退出

#### docker的额外配置

配置 `.env.prod`

执行

```shell
vim docker-config/.env.prod
```

```env
HOST=0.0.0.0                                                     # 修改此处为0.0.0.0
PORT=8080

ENABLE_ADVANCE_OUTPUT=false

# 插件配置
PLUGINS=["src2.plugins.chat"]

# 默认配置
# 如果工作在Docker下，请改成 MONGODB_HOST=mongodb
MONGODB_HOST=mongodb                                             # 修改此处为mongodb
MONGODB_PORT=27017
DATABASE_NAME=MegBot

# 也可以使用 URI 连接数据库（优先级比上面的高）
# MONGODB_URI=mongodb://127.0.0.1:27017/MegBot

# MongoDB 认证信息，若需要认证，请取消注释以下三行并填写正确的信息    # 启用mongodb密码
MONGODB_USERNAME=maimbot
MONGODB_PASSWORD=maimbot@123
MONGODB_AUTH_SOURCE=admin

#key and url
CHAT_ANY_WHERE_BASE_URL=https://api.chatanywhere.tech/v1
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1/
DEEP_SEEK_BASE_URL=https://api.deepseek.com/v1

# 定义你要用的api的key(需要去对应网站申请哦) 
DEEP_SEEK_KEY=
CHAT_ANY_WHERE_KEY=
SILICONFLOW_KEY=

# 定义日志相关配置
CONSOLE_LOG_LEVEL=INFO # 自定义日志的默认控制台输出日志级别
FILE_LOG_LEVEL=DEBUG # 自定义日志的默认文件输出日志级别
DEFAULT_CONSOLE_LOG_LEVEL=SUCCESS # 原生日志的控制台输出日志级别（nonebot就是这一类）
DEFAULT_FILE_LOG_LEVEL=DEBUG # 原生日志的默认文件输出日志级别（nonebot就是这一类）   
```

`bot_config.toml`根据需要自行配置

执行

```shell
vim docker-config/bot_config.toml
```

修改 `docker-compose.yml`

执行

```shell
vim docker-compose.yml 
```

```yaml
services:
  napcat:
    container_name: napcat
    environment:
      - TZ=Asia/Shanghai
      - NAPCAT_UID=${NAPCAT_UID}
      - NAPCAT_GID=${NAPCAT_GID} # 让 NapCat 获取当前用户 GID,UID，防止权限问题
    ports:
      - 6099:6099
    restart: unless-stopped
    volumes:
      - napcatQQ:/app/.config/QQ # 持久化 QQ 本体
      - napcatCONFIG:/app/napcat/config # 持久化 NapCat 配置文件
      - ./docker-data:/MaiMBot/data # NapCat 和 NoneBot 共享此卷，否则发送图片会有问题  # 修改此处卷为直接挂载目录
    image: mlikiowa/napcat-docker:latest

  mongodb:
    container_name: mongodb
    environment:
      - TZ=Asia/Shanghai
      - MONGO_INITDB_ROOT_USERNAME=maimbot # 此处设置用户 
      - MONGO_INITDB_ROOT_PASSWORD=maimbot@123 # 此处设置密码
    expose:
      - "27017"
    ports:  # 此处新增port (可选) | 注意：映射此端口需注意安全问题，非常推荐去给mongo设置一个用户密码
      - 27017:27017 
    restart: unless-stopped
    volumes:
      - mongodb:/data/db # 持久化 MongoDB 数据库
      - mongodbCONFIG:/data/configdb # 持久化 MongoDB 配置文件
    image: mongo:latest

  maimbot:
    container_name: maimbot
    environment:
      - TZ=Asia/Shanghai
      - EULA_AGREE=35362b6ea30f12891d46ef545122e84a                                            # 此处增加eula哈希，填写这两行即为同意eula
      - PRIVACY_AGREE=2402af06e133d2d10d9c6c643fdc9333                                         # 此处增加eula哈希，填写这两行即为同意eula
    expose:
      - "8080"
    restart: unless-stopped
    depends_on:
      - mongodb
      - napcat
    volumes:
      - napcatCONFIG:/MaiMBot/napcat # 自动根据配置中的 QQ 号创建 ws 反向客户端配置
      - ./docker-config/bot_config.toml:/MaiMBot/config/bot_config.toml # Toml 配置文件映射      # 修改此处卷映射到./docker-config/bot_config.toml
      - ./docker-data:/MaiMBot/data # NapCat 和 NoneBot 共享此卷，否则发送图片会有问题  		# 修改此处卷映射，和napcat保持一致
      - ./docker-config/.env.prod:/MaiMBot/.env.prod # Toml 配置文件映射                         # 修改此处卷映射到./docker-config/.env.prod
    image: maimbot:local								        # 修改镜像为本地构建maimbot:local

volumes:
  maimbotCONFIG:
  maimbotDATA:
  napcatQQ:
  napcatCONFIG:
  mongodb:
  mongodbCONFIG:
```

修改Dockerfile

执行

```shell
vim Dockerfile
```

```Dockerfile
FROM nonebot/nb-cli:latest

# 设置工作目录
WORKDIR /MaiMBot

# 先复制依赖列表
COPY requirements.txt .

# 安装依赖（这层会被缓存直到requirements.txt改变）
RUN pip install --upgrade -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple # 此处使用pipy清华源

# 然后复制项目代码
COPY . .

VOLUME [ "/MaiMBot/config" ]
VOLUME [ "/MaiMBot/data" ]
EXPOSE 8080
ENTRYPOINT [ "nb","run" ]
```

### 构建和启动麦麦

拉取镜像

执行

```shell
sudo docker pull nonebot/nb-cli:latest
```

构建镜像

执行

```shell
# 注意不要漏了最后的点
sudo docker build -t maimbot:local .
```

成功执行示例

***非命令请勿执行***

```shell
test@test:~/MaiBot$ sudo docker pull nonebot/nb-cli:latest
latest: Pulling from nonebot/nb-cli
2f44b7a888fa: Pull complete 
3f00b3697662: Pull complete 
9f2fc8d8f9bd: Pull complete 
4ea8b799d366: Pull complete 
1adff455ff8c: Pull complete 
753ba2fdde1a: Pull complete 
3a27ebb4ce99: Pull complete 
55c18539212d: Pull complete 
Digest: sha256:53400b4e5ae9cb5bb516e0b002b05cffe8d3af7b79bd88398734077a1100376c
Status: Downloaded newer image for nonebot/nb-cli:latest
docker.io/nonebot/nb-cli:latest
test@test:~/MaiBot$ sudo docker build -t maimbot:local .
[+] Building 95.9s (10/10) FINISHED                                                                                                                                                docker:default
 => [internal] load build definition from Dockerfile                                                                                                                                         0.0s
 => => transferring dockerfile: 457B                                                                                                                                                         0.0s
 => [internal] load metadata for docker.io/nonebot/nb-cli:latest                                                                                                                             0.0s
 => [internal] load .dockerignore                                                                                                                                                            0.0s
 => => transferring context: 99B                                                                                                                                                             0.0s
 => [1/5] FROM docker.io/nonebot/nb-cli:latest                                                                                                                                               0.1s
 => [internal] load build context                                                                                                                                                            0.1s
 => => transferring context: 2.08MB                                                                                                                                                          0.1s
 => [2/5] WORKDIR /MaiMBot                                                                                                                                                                   1.6s
 => [3/5] COPY requirements.txt .                                                                                                                                                            0.0s
 => [4/5] RUN pip install --upgrade -r requirements.txt  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple                                                                            78.8s
 => [5/5] COPY . .                                                                                                                                                                           0.2s 
 => exporting to image                                                                                                                                                                      15.0s 
 => => exporting layers                                                                                                                                                                     15.0s 
 => => writing image sha256:6e1a6a83b50ae921d79f2dbe73fcc850c07612bcd88db811e76c0e1b8823ccef                                                                                                 0.0s 
 => => naming to docker.io/library/maimbot:local  
```

启动麦麦

执行

```shell
sudo NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
```

成功执行示例

***非命令请勿执行***

```shell
test@test:~/MaiBot$  sudo NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g)  docker compose up -d
[+] Running 16/16
 ✔ napcat Pulled                                                                                                                                                                           156.1s 
   ✔ 6414378b6477 Pull complete                                                                                                                                                             15.8s 
   ✔ 490f82e472ca Pull complete                                                                                                                                                             78.5s 
   ✔ d4e6c35d58ce Pull complete                                                                                                                                                             78.5s 
   ✔ f382006ebcb6 Pull complete                                                                                                                                                             78.6s 
   ✔ 60f813bb54e0 Pull complete                                                                                                                                                             79.1s 
   ✔ 34e85b395e26 Pull complete                                                                                                                                                            155.8s 
 ✔ mongodb Pulled                                                                                                                                                                           95.6s 
   ✔ 5a7813e071bf Pull complete                                                                                                                                                             22.3s 
   ✔ cf12757b6444 Pull complete                                                                                                                                                             22.4s 
   ✔ 20cfb5e922d1 Pull complete                                                                                                                                                             27.1s 
   ✔ d11968535d8a Pull complete                                                                                                                                                             29.4s 
   ✔ c711ee204b1d Pull complete                                                                                                                                                             31.1s 
   ✔ 4fc65ca4253f Pull complete                                                                                                                                                             33.7s 
   ✔ dacd77ad2ef6 Pull complete                                                                                                                                                             95.3s 
   ✔ 5fa69bd3db1e Pull complete                                                                                                                                                             95.3s 
[+] Running 8/8
 ✔ Network maibot_default         Created                                                                                                                                                    0.2s 
 ✔ Volume "maibot_napcatCONFIG"   Created                                                                                                                                                    0.0s 
 ✔ Volume "maibot_napcatQQ"       Created                                                                                                                                                    0.0s 
 ✔ Volume "maibot_mongodb"        Created                                                                                                                                                    0.0s 
 ✔ Volume "maibot_mongodbCONFIG"  Created                                                                                                                                                    0.0s 
 ✔ Container napcat               Started                                                                                                                                                    3.0s 
 ✔ Container mongodb              Started                                                                                                                                                    3.1s 
 ✔ Container maimbot              Started                                                                                                                                                    1.2s 
```

查看麦麦状态

执行

```shell
sudo docker compose ps
```

成功执行示例

***非命令请勿执行***

```shell
test@test:~/MaiBot$ sudo docker compose ps
WARN[0000] The "NAPCAT_UID" variable is not set. Defaulting to a blank string. 
WARN[0000] The "NAPCAT_GID" variable is not set. Defaulting to a blank string. 
NAME      IMAGE                           COMMAND                  SERVICE   CREATED         STATUS                          PORTS
maimbot   maimbot:local                   "nb run"                 maimbot   4 minutes ago   Up 4 minutes  
mongodb   mongo:latest                    "docker-entrypoint.s…"   mongodb   4 minutes ago   Up 4 minutes                    0.0.0.0:27017->27017/tcp, [::]:27017->27017/tcp
napcat    mlikiowa/napcat-docker:latest   "bash entrypoint.sh"     napcat    4 minutes ago   Up 4 minutes                    0.0.0.0:6099->6099/tcp, [::]:6099->6099/tcp
```

然后进入napcat的web页面进行配置

地址为：`<你的ip>:6099`

websocket地址为：`ws://maimbot:8080/onebot/v11/ws`

## 3. 常用管理命令

| 功能     | 命令                                                |
| -------- | --------------------------------------------------- |
| 查看状态 | `sudo docker compose ps`                          |
| 查看日志 | `sudo docker compose logs -f maimbot --tail=1000` |
| 停止麦麦 | `sudo docker compose down`                        |
| 重启麦麦 | `sudo docker compose restart`                     |
