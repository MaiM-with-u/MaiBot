# 🐳 Docker 部署指南

## 部署步骤 (推荐，但不一定是最新)

**"更新镜像与容器"部分在本文档 [Part 6](#6-更新镜像与容器)**

### 0. 前提说明

**本文假设读者已具备一定的 Docker 基础知识。若您对 Docker 不熟悉，建议先参考相关教程或文档进行学习，或选择使用 [📦Linux手动部署指南](./manual_deploy_linux.md) 或 [📦Windows手动部署指南](./manual_deploy_windows.md) 。**


### 1. 获取Docker配置文件

- 建议先单独创建好一个文件夹并进入，作为工作目录

```bash
wget https://raw.githubusercontent.com/SengokuCola/MaiMBot/main/docker-compose.yml -O docker-compose.yml
```

- 若需要启用MongoDB数据库的用户名和密码，可进入docker-compose.yml，取消MongoDB处的注释并修改变量旁 `=` 后方的值为你的用户名和密码\
修改后请注意在之后配置 `.env.prod` 文件时指定MongoDB数据库的用户名密码

### 2. 启动服务

- **!!! 请在第一次启动前确保当前工作目录下 `.env.prod` 与 `bot_config.toml` 文件存在 !!!**\
由于Docker文件映射行为的特殊性，若宿主机的映射路径不存在，可能导致意外的目录创建，而不会创建文件，由于此处需要文件映射到文件，需提前确保文件存在且路径正确，可使用如下命令:

```bash
touch .env.prod
touch bot_config.toml
```

- 启动Docker容器:

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
# 旧版Docker中可能找不到docker compose，请使用docker-compose工具替代
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker-compose up -d
```


### 3. 修改配置并重启Docker

- 请前往 [🎀 新手配置指南](./installation_cute.md) 或 [⚙️ 标准配置指南](./installation_standard.md) 完成`.env.prod`与`bot_config.toml`配置文件的编写\
**需要注意`.env.prod`中HOST处IP的填写，Docker中部署和系统中直接安装的配置会有所不同**

- 重启Docker容器:

```bash
docker restart maimbot  # 若修改过容器名称则替换maimbot为你自定的名称
```

- 下方命令可以但不推荐，只是同时重启NapCat、MongoDB、MaiMBot三个服务

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose restart
# 旧版Docker中可能找不到docker compose，请使用docker-compose工具替代
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker-compose restart
```

### 4. 登入NapCat管理页添加反向WebSocket

- 在浏览器地址栏输入 `http://<宿主机IP>:6099/` 进入NapCat的管理Web页，添加一个Websocket客户端

> 网络配置 -> 新建 -> Websocket客户端

- Websocket客户端的名称自定，URL栏填入 `ws://maimbot:8080/onebot/v11/ws`，启用并保存即可\
(若修改过容器名称则替换maimbot为你自定的名称)

### 5. 部署完成，愉快地和麦麦对话吧!


### 6. 更新镜像与容器

- 拉取最新镜像

```bash
docker-compose pull
```

- 执行启动容器指令，该指令会自动重建镜像有更新的容器并启动

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
# 旧版Docker中可能找不到docker compose，请使用docker-compose工具替代
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker-compose up -d
```

## ⚠️ 注意事项

- 目前部署方案仍在测试中，可能存在未知问题
- 配置文件中的API密钥请妥善保管，不要泄露
- 建议先在测试环境中运行，确认无误后再部署到生产环境
