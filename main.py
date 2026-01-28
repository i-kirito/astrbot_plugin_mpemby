from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
import time
import asyncio
import io
import base64
import tempfile
import os
from datetime import datetime
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)
from .api import MoviepilotApi, EmbyApi

# 尝试导入 Pillow
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logger.warning("Pillow 未安装，推送将使用纯文本模式。可通过 pip install Pillow 安装")

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("apscheduler not found, daily report function disabled.")

@register("MoviepilotSubscribe", "ikirito", "MoviePilot订阅 & Emby入库查询插件", "1.2.7", "https://github.com/i-kirito/astrbot_plugin_mpemby")
class MyPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.api = MoviepilotApi(config)  # MoviePilot API
        self.emby_api = EmbyApi(config)  # Emby API
        self.state = {}  # 初始化状态管理字典

        # 定时任务调度器
        self.scheduler = None
        if HAS_APSCHEDULER and self.config.get("enable_daily_report", False):
            self.setup_scheduler()

        logger.info(f"插件初始化完成，Emby配置状态: {'已配置' if self.emby_api.is_configured() else '未配置'}")

    def setup_scheduler(self):
        """配置定时任务"""
        try:
            report_time = self.config.get("report_time", "20:00")
            hour, minute = report_time.split(":")

            self.scheduler = AsyncIOScheduler()
            self.scheduler.add_job(
                self.send_daily_report,
                CronTrigger(hour=int(hour), minute=int(minute)),
                id="daily_report"
            )
            self.scheduler.start()
            logger.info(f"已启动每日入库推送任务，时间: {report_time}")
        except Exception as e:
            logger.error(f"启动定时任务失败: {e}")

    def render_text_to_image(self, text: str) -> bytes:
        """将文本渲染为图片，返回 PNG 字节数据"""
        if not HAS_PILLOW:
            return None

        # 配置参数
        padding = 40
        line_spacing = 8
        font_size = 28
        bg_color = (30, 30, 35)  # 深色背景
        text_color = (230, 230, 230)  # 浅色文字
        accent_color = (100, 180, 255)  # 强调色
        border_color = (60, 60, 70)

        # 尝试加载字体
        font = None
        title_font = None
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",  # macOS
            "/System/Library/Fonts/STHeiti Light.ttc",  # macOS 备选
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux 备选
            "C:\\Windows\\Fonts\\msyh.ttc",  # Windows 微软雅黑
            "C:\\Windows\\Fonts\\simhei.ttf",  # Windows 黑体
        ]

        for path in font_paths:
            try:
                if os.path.exists(path):
                    font = ImageFont.truetype(path, font_size)
                    title_font = ImageFont.truetype(path, font_size + 6)
                    break
            except Exception:
                continue

        if not font:
            font = ImageFont.load_default()
            title_font = font

        # 计算图片尺寸
        lines = text.split('\n')

        # 创建临时图片计算文字宽度
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        max_width = 0
        for line in lines:
            bbox = temp_draw.textbbox((0, 0), line, font=font)
            line_width = bbox[2] - bbox[0]
            max_width = max(max_width, line_width)

        img_width = max_width + padding * 2
        img_height = len(lines) * (font_size + line_spacing) + padding * 2

        # 确保最小宽度
        img_width = max(img_width, 500)

        # 创建图片
        img = Image.new('RGB', (img_width, img_height), bg_color)
        draw = ImageDraw.Draw(img)

        # 绘制边框
        draw.rectangle([2, 2, img_width - 3, img_height - 3], outline=border_color, width=2)

        # 绘制顶部装饰线
        draw.rectangle([0, 0, img_width, 4], fill=accent_color)

        # 逐行绘制文本
        y = padding
        for i, line in enumerate(lines):
            # 标题行使用强调色
            if i == 0 or '━' in line:
                color = accent_color
                current_font = title_font if i == 0 else font
            else:
                color = text_color
                current_font = font

            draw.text((padding, y), line, font=current_font, fill=color)
            y += font_size + line_spacing

        # 转换为字节
        buffer = io.BytesIO()
        img.save(buffer, format='PNG', optimize=True)
        return buffer.getvalue()

    async def send_daily_report(self, manual_trigger: bool = False, event: AstrMessageEvent = None):
        """发送每日入库简报

        Args:
            manual_trigger: 是否为手动触发
            event: 触发事件对象 (仅手动触发时存在)
        """
        # 如果是手动触发且有 event，优先使用 event 发送，这样最稳
        if manual_trigger and event:
            logger.info("使用当前会话直接发送日报")
        else:
            target_id = self.config.get("report_target_id")
            if not target_id:
                msg = "⚠️ 未配置推送目标ID (report_target_id)，请使用 /emby推送配置 target <id> 进行设置"
                logger.warning(msg)
                if manual_trigger and event:
                   # 修复：移除 yield，改用 await event.send
                   await event.send(event.plain_result(msg))
                return

        logger.info(f"开始执行每日入库统计推送 (手动触发: {manual_trigger})...")
        data = await self.emby_api.get_today_additions_stats()

        stats = data.get("stats", {})
        items = data.get("items", [])
        total = stats.get("Total", 0)

        if total == 0:
            logger.info("今日无新入库")
            if manual_trigger:
                msg = f"📅 {datetime.now().strftime('%Y-%m-%d')}\n今日暂无新入库内容。"
                if event:
                    await event.send(event.plain_result(msg))
                elif target_id:
                    await self._send_to_target(target_id, msg)
            return

        # 构建消息内容
        date_str = datetime.now().strftime('%Y-%m-%d')
        msg = f"📢 Emby 今日入库日报 ({date_str})\n"
        msg += "━━━━━━━━━━━━\n"

        # 1. 统计摘要
        if stats.get("Movie", 0) > 0:
            msg += f"🎬 电影新增：{stats['Movie']} 部\n"
        if stats.get("Series", 0) > 0:
            msg += f"📺 剧集新增：{stats['Series']} 部\n"
        if stats.get("Episode", 0) > 0:
            msg += f"🎞️ 单集新增：{stats['Episode']} 集\n"
        msg += "━━━━━━━━━━━━\n"

        # 2. 详情列表
        if items:
            msg += "📚 最近入库详情：\n"
            for i, item_str in enumerate(items, 1):
                msg += f"{i}. {item_str}\n"

            if total > len(items):
                msg += f"...等共 {total} 条记录"

        msg = msg.strip()

        # 尝试渲染为图片发送
        if HAS_PILLOW:
            try:
                img_bytes = self.render_text_to_image(msg)
                if img_bytes:
                    # 保存到临时文件
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                        f.write(img_bytes)
                        tmp_path = f.name

                    if manual_trigger and event:
                        message_result = event.make_result()
                        message_result.chain = [Comp.Image.fromFileSystem(tmp_path)]
                        await event.send(message_result)
                    else:
                        await self._send_image_to_target(target_id, tmp_path)

                    # 清理临时文件
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                    return
            except Exception as e:
                logger.warning(f"图片渲染失败，回退到文本模式: {e}")

        # 回退到纯文本模式
        if manual_trigger and event:
            await event.send(event.plain_result(msg))
        else:
            await self._send_to_target(target_id, msg)

    async def _send_image_to_target(self, target_id: str, image_path: str):
        """发送图片到指定目标"""
        sent = False
        platform_name = None
        user_id = target_id

        if ":" in target_id:
            platform_name, user_id = target_id.split(":", 1)

        logger.info(f"准备推送图片，目标: {target_id}")

        try:
            platforms = []
            if hasattr(self.context, 'platform_manager'):
                pm = self.context.platform_manager
                if hasattr(pm, 'get_insts'):
                    platforms = pm.get_insts()
                elif hasattr(pm, 'platforms'):
                    platforms = pm.platforms
                elif hasattr(pm, 'adapters'):
                    platforms = pm.adapters

            if not platforms:
                logger.error("未找到任何平台实例")
                return False

            for platform in platforms:
                curr_platform_name = getattr(platform, "platform_name", str(platform))
                if platform_name and curr_platform_name != platform_name:
                    continue

                bot_client = None
                if hasattr(platform, 'get_client'):
                    bot_client = platform.get_client()
                elif hasattr(platform, 'client'):
                    bot_client = platform.client
                elif hasattr(platform, 'bot'):
                    bot_client = platform.bot

                try:
                    uid_int = int(user_id)
                except ValueError:
                    uid_int = None

                # 读取图片并编码
                with open(image_path, 'rb') as f:
                    img_data = f.read()
                img_base64 = base64.b64encode(img_data).decode()

                call_action = None
                if bot_client:
                    if hasattr(bot_client, 'call_action'):
                        call_action = bot_client.call_action
                    elif hasattr(bot_client, 'api') and hasattr(bot_client.api, 'call_action'):
                        call_action = bot_client.api.call_action

                if call_action and uid_int:
                    message_payload = [{"type": "image", "data": {"file": f"base64://{img_base64}"}}]

                    try:
                        await call_action("send_private_msg", user_id=uid_int, message=message_payload)
                        logger.info(f"✅ 图片私聊推送成功")
                        sent = True
                        break
                    except Exception:
                        pass

                    try:
                        await call_action("send_group_msg", group_id=uid_int, message=message_payload)
                        logger.info(f"✅ 图片群聊推送成功")
                        sent = True
                        break
                    except Exception:
                        pass

                if not sent and hasattr(platform, "send_msg"):
                    chain = [Comp.Image.fromFileSystem(image_path)]
                    try:
                        await platform.send_msg(uid_int if uid_int else user_id, chain)
                        logger.info("✅ 标准接口图片推送成功")
                        sent = True
                        break
                    except Exception as e:
                        logger.warning(f"标准接口发送失败: {e}")

            return sent

        except Exception as e:
            logger.error(f"图片推送错误: {e}")
            return False

    async def _send_to_target(self, target_id: str, msg: str):
        """发送消息到指定目标 (增强版)"""
        sent = False
        platform_name = None
        user_id = target_id

        # 解析 platform:user_id 格式
        if ":" in target_id:
            platform_name, user_id = target_id.split(":", 1)

        logger.info(f"准备推送消息，目标: {target_id} (平台: {platform_name})")

        try:
            # 获取所有平台实例 - 修复 API 调用
            platforms = []
            if hasattr(self.context, 'platform_manager'):
                # 尝试获取平台实例列表，兼容不同版本 API
                pm = self.context.platform_manager
                if hasattr(pm, 'get_insts'):
                    platforms = pm.get_insts()
                elif hasattr(pm, 'platforms'):
                    platforms = pm.platforms
                elif hasattr(pm, 'adapters'):
                    platforms = pm.adapters
                else:
                    # 尝试直接遍历属性查找列表
                    for attr in dir(pm):
                        if not attr.startswith('_'):
                            val = getattr(pm, attr)
                            if isinstance(val, list) and len(val) > 0 and hasattr(val[0], 'platform_name'):
                                platforms = val
                                break

            if not platforms:
                logger.error(f"未找到任何平台实例 (PlatformManager 属性: {dir(self.context.platform_manager)})")
                return False

            for platform in platforms:
                # 筛选指定平台
                curr_platform_name = getattr(platform, "platform_name", str(platform))
                if platform_name and curr_platform_name != platform_name:
                    continue

                # 尝试获取底层的 bot 客户端
                bot_client = None
                if hasattr(platform, 'get_client'):
                    bot_client = platform.get_client()
                elif hasattr(platform, 'client'):
                    bot_client = platform.client
                elif hasattr(platform, 'bot'):
                    bot_client = platform.bot

                # 尝试转换 ID 为整数 (QQ 需要)
                try:
                    uid_int = int(user_id)
                except ValueError:
                    uid_int = None

                # 策略 1: 使用底层 call_action (OneBot/Lagrange)
                call_action = None
                if bot_client:
                    if hasattr(bot_client, 'call_action'):
                        call_action = bot_client.call_action
                    elif hasattr(bot_client, 'api') and hasattr(bot_client.api, 'call_action'):
                        call_action = bot_client.api.call_action

                if call_action and uid_int:
                    logger.info(f"尝试使用底层 API (call_action) 通过 {curr_platform_name} 发送...")
                    message_payload = [{"type": "text", "data": {"text": msg}}]

                    # 尝试 1.1: 发送私聊
                    try:
                        await call_action("send_private_msg", user_id=uid_int, message=message_payload)
                        logger.info(f"✅ 私聊推送成功 (user_id={uid_int})")
                        sent = True
                        break
                    except Exception:
                        pass

                    # 尝试 1.2: 发送群聊
                    try:
                        await call_action("send_group_msg", group_id=uid_int, message=message_payload)
                        logger.info(f"✅ 群聊推送成功 (group_id={uid_int})")
                        sent = True
                        break
                    except Exception:
                        pass

                # 策略 2: 使用 AstrBot 标准接口 (platform.send_msg)
                if not sent and hasattr(platform, "send_msg"):
                    logger.info(f"尝试使用标准接口 platform.send_msg 通过 {curr_platform_name} 发送...")
                    chain = [Comp.Plain(msg)]
                    try:
                        await platform.send_msg(uid_int if uid_int else user_id, chain)
                        logger.info("✅ 标准接口推送成功")
                        sent = True
                        break
                    except Exception as e:
                        logger.warning(f"标准接口发送失败: {e}")

            if sent:
                return True
            else:
                logger.error(f"❌ 所有尝试均失败，无法推送到目标: {target_id}")
                return False

        except Exception as e:
            logger.error(f"执行推送逻辑致命错误: {e}")
            return False

    async def terminate(self):
        """插件卸载时清理"""
        if self.scheduler:
            self.scheduler.shutdown()
            logger.info("已停止定时任务")

    @filter.command("mp订阅")
    async def sub(self, event: AstrMessageEvent, message: str):
        '''订阅影片'''
        movies = await self.api.search_media_info(message)  # 使用 self.api 访问实例属性
        if movies:
            movie_list = "\n".join([f"{i + 1}. {movie['title']} ({movie['year']})" for i, movie in enumerate(movies)])
            print(movie_list)
            media_list = "\n查询到的影片如下\n请直接回复序号进行订阅（回复0退出选择）：\n" + movie_list
            yield event.plain_result(media_list)

            # 使用会话控制器等待用户回复
            @session_waiter(timeout=60, record_history_chains=False)
            async def movie_selection_waiter(controller: SessionController, event: AstrMessageEvent):
                try:
                    user_input = event.message_str.strip()

                    # 处理电影选择
                    try:
                        index = int(user_input) - 1

                        if index == -1:  # 用户输入0
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("操作已取消。")]
                            await event.send(message_result)
                            controller.stop()
                            return

                        if 0 <= index < len(movies):
                            selected_movie = movies[index]
                            if selected_movie['type'] == "电视剧":
                                # 如果是电视剧，直接订阅所有季
                                seasons = await self.api.list_all_seasons(selected_movie['tmdb_id'])
                                if seasons:
                                    message_result = event.make_result()
                                    message_result.chain = [Comp.Plain(f"正在订阅 {selected_movie['title']} 的所有季...")]
                                    await event.send(message_result)

                                    # 订阅所有季
                                    result = await self.api.subscribe_all_seasons(selected_movie, seasons)

                                    message_result = event.make_result()
                                    if result["success"] > 0:
                                        msg = f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n✅ 成功订阅 {result['success']} 季"
                                        if result["failed"] > 0:
                                            msg += f"，{result['failed']} 季订阅失败（可能已订阅）"
                                        message_result.chain = [Comp.Plain(msg)]
                                    else:
                                        message_result.chain = [Comp.Plain("订阅失败，可能已全部订阅。")]
                                    await event.send(message_result)
                                    controller.stop()
                                else:
                                    message_result = event.make_result()
                                    message_result.chain = [Comp.Plain("没有找到可用的季数。")]
                                    await event.send(message_result)
                                    controller.stop()
                            else:
                                # 如果是电影，直接订阅
                                success = await self.api.subscribe_movie(selected_movie)
                                message_result = event.make_result()
                                if success:
                                    message_result.chain = [Comp.Plain(f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n订阅成功！")]
                                else:
                                    message_result.chain = [Comp.Plain("订阅失败。")]
                                await event.send(message_result)
                                controller.stop()
                        else:
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("无效的序号，请重新输入。")]
                            await event.send(message_result)
                            controller.keep(timeout=60, reset_timeout=True)
                    except ValueError:
                        message_result = event.make_result()
                        message_result.chain = [Comp.Plain("请输入一个数字。")]
                        await event.send(message_result)
                        controller.keep(timeout=60, reset_timeout=True)
                except Exception as e:
                    logger.error(f"处理用户输入时出错: {e}")
                    message_result = event.make_result()
                    message_result.chain = [Comp.Plain(f"处理输入时出错: {str(e)}")]
                    await event.send(message_result)
                    controller.stop()

            try:
                await movie_selection_waiter(event)
            except Exception as e:
                logger.error(f"Movie selection error: {e}")
                yield event.plain_result(f"发生错误：{str(e)}")
            finally:
                event.stop_event()
        else:
            yield event.plain_result("没有查询到影片，请检查名字。")

    @filter.command("mp当前订阅")
    async def current_subscribes(self, event: AstrMessageEvent):
        '''查看当前订阅列表（仅显示订阅中的）'''
        subscribes = await self.api.get_subscribes()
        if subscribes is None:
            yield event.plain_result("获取订阅列表失败，请检查 MoviePilot 配置。")
            return

        if len(subscribes) == 0:
            yield event.plain_result("当前没有订阅。")
            return

        # 分类整理订阅（只保留订阅中的，过滤已完成的）
        movies = []
        series = []

        for sub in subscribes:
            state = sub.get('state', '')
            # 只显示订阅中的，跳过已完成的
            if state == '已完成' or state == 'completed':
                continue

            sub_type = sub.get('type', '')
            name = sub.get('name', '未知')
            year = sub.get('year', '')
            sub_id = sub.get('id', '')

            if sub_type == '电影':
                movies.append({
                    'name': name,
                    'year': year,
                    'id': sub_id,
                    'state': state
                })
            else:
                season = sub.get('season', 1)
                total_episode = sub.get('total_episode', 0)
                lack_episode = sub.get('lack_episode', 0)
                series.append({
                    'name': name,
                    'year': year,
                    'season': season,
                    'total_episode': total_episode,
                    'lack_episode': lack_episode,
                    'id': sub_id,
                    'state': state
                })

        # 格式化输出
        result_lines = ["📋 当前订阅列表\n"]
        result_lines.append("━━━━━━━━━━━━━━━━━━━━")

        if movies:
            result_lines.append("\n🎬 电影订阅：")
            for i, m in enumerate(movies, 1):
                year_str = f" ({m['year']})" if m['year'] else ""
                state_str = f" [{m['state']}]" if m['state'] else ""
                result_lines.append(f"  {i}. {m['name']}{year_str}{state_str}")

        if series:
            result_lines.append("\n📺 剧集订阅：")
            for i, s in enumerate(series, 1):
                year_str = f" ({s['year']})" if s['year'] else ""
                season_str = f" 第{s['season']}季" if s['season'] else ""

                # 计算进度
                total = s['total_episode']
                lack = s['lack_episode']
                if total > 0:
                    downloaded = total - lack
                    progress = f" [{downloaded}/{total}集]"
                else:
                    progress = ""

                state_str = f" - {s['state']}" if s['state'] else ""
                result_lines.append(f"  {i}. {s['name']}{year_str}{season_str}{progress}{state_str}")

        result_lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        result_lines.append(f"共 {len(movies)} 部电影，{len(series)} 部剧集")

        yield event.plain_result("\n".join(result_lines))

    @filter.command("mp下载")
    async def progress(self, event: AstrMessageEvent):
        '''查看下载'''
        progress_data = await self.api.get_download_progress()
        if progress_data is not None:  # 如果成功获取到数据
            if len(progress_data) == 0:  # 如果没有正在下载的任务
                yield event.plain_result("当前没有正在下载的任务。")
                return

            # 格式化下载进度信息
            progress_list = []
            for task in progress_data:
                media = task.get('media', {})
                title = media.get('title', task.get('title', '未知'))
                season = media.get('season', '')
                episode = media.get('episode', '')
                progress = round(task.get('progress', 0), 2)  # 保留两位小数

                # 按照要求格式化：title season episode：progress
                formatted_info = f"{title} {season} {episode}：{progress}%"
                progress_list.append(formatted_info)

            result = "\n".join(progress_list)
            yield event.plain_result(result)
        else:
            yield event.plain_result("获取下载进度失败，请稍后重试。")

    @filter.command("emby")
    async def emby_latest(self, event: AstrMessageEvent, media_type: str = "all"):
        '''查看Emby最新入库

        参数:
            media_type: 可选 "movie"(电影), "series"(电视剧), "all"(全部，默认)
        '''
        if not self.emby_api.is_configured():
            yield event.plain_result("Emby 未配置，请先在插件配置中填写 Emby 服务器信息。")
            return

        # 处理类型参数
        type_map = {
            "movie": "movie",
            "电影": "movie",
            "series": "series",
            "电视剧": "series",
            "剧集": "series",
            "all": "all",
            "全部": "all",
        }
        media_type = type_map.get(media_type.lower(), "all")

        type_name = {"movie": "电影", "series": "电视剧", "all": "全部"}

        yield event.plain_result(f"正在查询 Emby 最新入库（{type_name.get(media_type, '全部')}）...")

        media_list = await self.emby_api.get_latest_media(media_type)

        if not media_list:
            yield event.plain_result("暂无入库记录或查询失败。")
            return

        # 格式化输出
        result_lines = [f"📺 Emby 最新入库 ({type_name.get(media_type, '全部')}) 📺\n"]
        for i, media in enumerate(media_list, 1):
            name = media.get('name', '未知')
            year = media.get('year', '')
            m_type = media.get('type', '')
            date_created = media.get('date_created', '')

            year_str = f" ({year})" if year else ""
            result_lines.append(f"{i}. 《{name}》{year_str} [{m_type}]")
            result_lines.append(f"   入库时间: {date_created}")

        yield event.plain_result("\n".join(result_lines))

    @filter.command("emby搜索")
    async def emby_search(self, event: AstrMessageEvent, keyword: str):
        '''在Emby媒体库中搜索'''
        if not self.emby_api.is_configured():
            yield event.plain_result("Emby 未配置，请先在插件配置中填写 Emby 服务器信息。")
            return

        if not keyword.strip():
            yield event.plain_result("请输入搜索关键词，例如: /emby搜索 复仇者联盟")
            return

        yield event.plain_result(f"正在搜索: {keyword}...")

        media_list = await self.emby_api.search_media(keyword)

        if not media_list:
            yield event.plain_result(f"未找到与 \"{keyword}\" 相关的内容。")
            return

        # 格式化输出
        result_lines = [f"🔍 Emby 搜索结果: {keyword}\n"]
        for i, media in enumerate(media_list, 1):
            name = media.get('name', '未知')
            original_title = media.get('original_title', '')
            year = media.get('year', '')
            m_type = media.get('type', '')

            year_str = f" ({year})" if year else ""
            original_str = f" / {original_title}" if original_title and original_title != name else ""
            result_lines.append(f"{i}. 《{name}》{original_str}{year_str} [{m_type}]")

        yield event.plain_result("\n".join(result_lines))

    @filter.command("emby统计")
    async def emby_stats(self, event: AstrMessageEvent):
        '''查看Emby媒体库统计'''
        if not self.emby_api.is_configured():
            yield event.plain_result("Emby 未配置，请先在插件配置中填写 Emby 服务器信息。")
            return

        stats = await self.emby_api.get_library_stats()

        if not stats:
            yield event.plain_result("获取统计信息失败。")
            return

        result = f"""📊 Emby 媒体库统计 📊
━━━━━━━━━━━━━━━━━━━━
🎬 电影: {stats.get('movies', 0)} 部
📺 电视剧: {stats.get('series', 0)} 部
🎞️ 剧集: {stats.get('episodes', 0)} 集
━━━━━━━━━━━━━━━━━━━━"""

        yield event.plain_result(result)

    @filter.command("emby推送")
    async def manual_daily_report(self, event: AstrMessageEvent):
        '''手动发送一次今日入库日报'''
        # 鉴权：仅管理员可用
        is_admin = False
        try:
            if hasattr(event, "is_admin"):
                if callable(event.is_admin):
                    is_admin = event.is_admin()
                else:
                    is_admin = bool(event.is_admin)

            if not is_admin:
                role = getattr(event, "role", None)
                if isinstance(role, str) and role.lower() == "admin":
                    is_admin = True

            if not is_admin:
                sender_id = str(event.get_sender_id())
                astrbot_config = self.context.get_config()
                for key in ("admins", "admin_ids", "admin_list", "superusers"):
                    ids = astrbot_config.get(key, [])
                    if isinstance(ids, (list, tuple, set)) and sender_id in {str(i) for i in ids}:
                        is_admin = True
                        break
        except:
            pass

        if not is_admin:
            yield event.plain_result("🚫 仅管理员可执行此操作")
            return

        yield event.plain_result("⏳ 正在触发日报推送...")

        # 强制执行推送，并开启手动触发标志
        await self.send_daily_report(manual_trigger=True, event=event)

    @filter.command("emby推送配置")
    async def config_daily_report(self, event: AstrMessageEvent, action: str = "", value: str = ""):
        '''配置每日入库推送

        参数:
            action: 操作指令 (on/off/time/target)
            value: 参数值
        '''
        # 鉴权：仅管理员可用
        is_admin = False
        try:
            # 尝试多种方式判断管理员
            if hasattr(event, "is_admin"):
                if callable(event.is_admin):
                    is_admin = event.is_admin()
                else:
                    is_admin = bool(event.is_admin)

            if not is_admin:
                role = getattr(event, "role", None)
                if isinstance(role, str) and role.lower() == "admin":
                    is_admin = True

            # 兜底：检查是否在配置的管理员列表中
            if not is_admin:
                sender_id = str(event.get_sender_id())
                astrbot_config = self.context.get_config()
                for key in ("admins", "admin_ids", "admin_list", "superusers"):
                    ids = astrbot_config.get(key, [])
                    if isinstance(ids, (list, tuple, set)) and sender_id in {str(i) for i in ids}:
                        is_admin = True
                        break
        except:
            pass

        if not is_admin:
            yield event.plain_result("🚫 仅管理员可执行此操作")
            return

        if not action:
            # 显示当前配置
            status = "✅ 开启" if self.config.get("enable_daily_report") else "❌ 关闭"
            time_val = self.config.get("report_time", "20:00")
            target = self.config.get("report_target_id", "未设置")

            msg = f"""⚙️ 每日入库推送配置
━━━━━━━━━━━━
状态：{status}
时间：{time_val}
目标：{target}
━━━━━━━━━━━━
指令说明：
/emby推送配置 on        - 开启推送
/emby推送配置 off       - 关闭推送
/emby推送配置 time 20:00 - 设置时间
/emby推送配置 target 123 - 设置目标ID
"""
            yield event.plain_result(msg)
            return

        action = action.lower()

        try:
            if action == "on":
                self.config["enable_daily_report"] = True
                if HAS_APSCHEDULER:
                    self.setup_scheduler() # 重新设置调度器
                yield event.plain_result("✅ 已开启每日入库推送")

            elif action == "off":
                self.config["enable_daily_report"] = False
                if self.scheduler:
                    self.scheduler.shutdown()
                    self.scheduler = None
                yield event.plain_result("✅ 已关闭每日入库推送")

            elif action == "time":
                if not value:
                    yield event.plain_result("❌ 请输入时间，格式 HH:MM，例如: /emby推送配置 time 20:00")
                    return
                # 简单验证格式
                try:
                    datetime.strptime(value, "%H:%M")
                    self.config["report_time"] = value
                    if self.config.get("enable_daily_report"):
                        self.setup_scheduler() # 重启任务以应用新时间
                    yield event.plain_result(f"✅ 推送时间已设置为: {value}")
                except ValueError:
                    yield event.plain_result("❌ 时间格式错误，请使用 HH:MM 格式")

            elif action == "target":
                if not value:
                    yield event.plain_result("❌ 请输入目标ID (群号或QQ号)")
                    return
                self.config["report_target_id"] = value
                yield event.plain_result(f"✅ 推送目标已设置为: {value}")
            else:
                yield event.plain_result(f"❌ 未知指令: {action}")
                return

            # 尝试保存配置 (如果在 AstrBot 中支持)
            # 注意：这里修改的是内存中的 config，重启后可能会失效，除非框架自动保存
            # AstrBot v3+ 通常可以通过 context.save_config() 保存
            if hasattr(self.context, "save_config"):
                try:
                    # save_config 通常需要传入 plugin_name 或实例
                    # 具体参数视版本而定，这里尝试无参调用或传自身
                    # 或者提示用户手动去后台保存
                    pass
                except:
                    pass

        except Exception as e:
            logger.error(f"修改配置失败: {e}")
            yield event.plain_result(f"❌ 配置修改失败: {str(e)}")

    @filter.command("订阅帮助")
    async def show_help(self, event: AstrMessageEvent):
        '''显示帮助信息'''
        help_text = """📖 MoviePilot & Emby 插件帮助 📖
━━━━━━━━━━━━━━━━━━━━━━━━━━━
【MoviePilot 功能】
  /mp订阅 [片名]      - 搜索并订阅影片
  /mp当前订阅         - 查看当前订阅列表
  /mp下载             - 查看下载进度

【Emby 功能】
  /emby [类型]     - 查看最新入库
                     类型: movie/电影, series/电视剧, all/全部
  /emby搜索 [关键词] - 搜索媒体库
  /emby统计      - 查看媒体库统计

【推送管理】(管理员)
  /emby推送配置    - 查看/修改推送设置
  /emby推送        - 手动触发一次推送

【其他】
  /订阅帮助            - 显示此帮助
━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
        yield event.plain_result(help_text)
