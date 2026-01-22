# MoviePilot 订阅 & Emby 入库查询插件

> 本项目是基于 AstrBot 开发的插件，部署请参考此项目
> https://github.com/Soulter/AstrBot

## 功能

- MoviePilot 影片搜索与订阅
- MoviePilot 下载进度查看
- Emby 最新入库查询
- Emby 媒体库搜索
- Emby 媒体库统计

## 配置

### MoviePilot 配置

| 配置项 | 说明 |
|--------|------|
| mp_url | 公网能够访问的 MoviePilot 地址 |
| mp_token | MoviePilot 中的 token |
| mp_username | MoviePilot 用户名 |
| mp_password | MoviePilot 密码 |

### Emby 配置

| 配置项 | 说明 |
|--------|------|
| emby_url | Emby 服务器地址，如 `http://192.168.1.100:8096` |
| emby_api_key | Emby API Key，在 Emby 后台 -> 设置 -> API密钥 中生成 |
| emby_user_id | Emby 用户ID，可在 Emby 后台用户 URL 中获取 |
| emby_max_results | 查询结果最大数量，默认 10 |

## 指令

### MoviePilot 相关

| 指令 | 说明 |
|------|------|
| `/sub [片名]` | 搜索并订阅影片 |
| `/download` | 查看下载进度 |

### Emby 相关

| 指令 | 说明 |
|------|------|
| `/emby [类型]` | 查看最新入库，类型可选: `movie`/`电影`, `series`/`电视剧`, `all`/`全部`(默认) |
| `/emby_search [关键词]` | 在 Emby 媒体库中搜索 |
| `/emby_stats` | 查看媒体库统计信息 |

### 其他

| 指令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |

## 版本历史

- v1.2.0: 新增 Emby 入库查询、搜索、统计功能
- v1.1.1: 初始版本，支持 MoviePilot 订阅和下载查看
