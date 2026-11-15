# 食刻 · 部署包

一次部署，之后手机直接打开一个链接就能用。不需要每次开电脑、设环境变量、跑 Flask。

## 这个包里有什么

| 文件 | 作用 |
|---|---|
| `app.py` | 后端：`/api/analyze` 调华为云 MaaS，`/api/health` 健康检查，`/` 直接返回前端页面 |
| `nutrition.csv` | 营养库 35 道菜 + 别名 |
| `食堂营养分析工作台.html` | 前端（示例图已内联，220 KB，无外部依赖） |
| `requirements.txt` | flask / requests / gunicorn |
| `Dockerfile` | 容器化，任何支持 Docker 的平台都能跑 |

前端打开时会自动连「当前域名」下的后端，所以**部署完不用填任何地址**，链接发出去就能用。

---

## 路线 A：Render（推荐，免费，10 分钟）

1. 把这些文件推到一个 GitHub 仓库（可以私有）。
2. 打开 https://render.com ，用 GitHub 登录 → `New +` → `Web Service` → 选这个仓库。
3. 平台会自动识别 `Dockerfile`，语言选 `Docker` 即可。
4. 在 `Environment` 里加三个变量：

| Key | Value |
|---|---|
| `MAAS_BASE_URL` | `https://api.modelarts-maas.com/v2` |
| `MAAS_API_KEY` | 你的 API Key |
| `MAAS_MODEL` | `deepseek-v4.1-flash-1017` |

5. 点 `Deploy`，等 2–3 分钟，拿到 `https://xxx.onrender.com`。
6. 手机浏览器打开这个链接，直接就是工作台。

**注意**：免费层闲置 15 分钟会休眠，冷启动约 30–60 秒。答辩前 10 分钟先打开一次把服务唤醒。

---

## 路线 B：华为云 ECS（比赛叙事更好）

1. 买一台学生机（2 核 2G 够用），系统选 Ubuntu 22.04，安全组放行 8080 端口。
2. SSH 登录后装 Docker：

```bash
curl -fsSL https://get.docker.com | sh
```

3. 把 `部署包` 传上去（`scp` 或 `rz`），进目录执行：

```bash
docker build -t shike .
docker run -d --restart=always -p 8080:8080 \
  -e MAAS_BASE_URL="https://api.modelarts-maas.com/v2" \
  -e MAAS_API_KEY="你的Key" \
  -e MAAS_MODEL="deepseek-v4.1-flash-1017" \
  --name shike shike
```

4. 手机访问 `http://你的公网IP:8080`。

`--restart=always` 让机器重启后服务自动起来，这是"做一次不用管"的关键。

---

## 路线 C：先不起服务器，只验证包没坏

在本地（装了 Docker 的电脑）：

```bash
cd 部署包
docker build -t shike .
docker run --rm -p 8080:8080 \
  -e MAAS_BASE_URL="https://api.modelarts-maas.com/v2" \
  -e MAAS_API_KEY="你的Key" \
  -e MAAS_MODEL="deepseek-v4.1-flash-1017" shike
```

浏览器打开 `http://127.0.0.1:8080` —— 页面和后端就同源了，连「接入后端」都不用点。

---

## 为什么不用华为云函数工作流

FunctionGraph 本身每月前 100 万次调用免费，但它的 HTTP 触发器必须搭配 **APIG 专享版实例**，实例按需计费，对学生项目不划算。等比赛结束如果要正式部署，再考虑这条路。

## 安全提醒

- API Key 只放在平台的环境变量里，**永远不要**写进 HTML、源码、截图、PPT。
- 部署平台的日志里也不要有 Key；`app.py` 只在异常时打印状态码，不打印 Key。
- Key 泄露了就去 MaaS 控制台删掉重建，一分钟的事。

## 部署后自检

```bash
curl https://你的域名/api/health
```

返回 `"ready":true` 就算成功。返回 `false` 说明环境变量没读到，去平台后台检查拼写（注意 `MAAS_BASE_URL` 结尾是 `/v2`，不是 `/v1`）。

> （注：部分内容由豆包 AI 生成）