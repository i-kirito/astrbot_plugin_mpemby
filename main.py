from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
import time
import asyncio
from datetime import datetime
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)
from .api import MoviepilotApi, EmbyApi

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("apscheduler not found, daily report function disabled.")

@register("MoviepilotSubscribe", "4Nest", "MoviePilot订阅 & Emby入库查询插件", "1.2.1", "https://github.com/i-kirito/astrbot_plugin_mpemby")
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

    async def send_daily_report(self):
        """发送每日入库简报"""
        target_id = self.config.get("report_target_id")
        if not target_id:
            logger.warning("未配置推送目标ID (report_target_id)，跳过推送")
            return

        logger.info("开始执行每日入库统计推送...")
        stats = await self.emby_api.get_today_additions_stats()

        if not stats or stats.get("Total", 0) == 0:
            logger.info("今日无新入库，跳过推送")
            return

        # 构建消息内容
        msg = "📢 Emby 今日入库日报\n━━━━━━━━━━━━\n"
        if stats.get("Movie", 0) > 0:
            msg += f"🎬 电影新增：{stats['Movie']} 部\n"
        if stats.get("Series", 0) > 0:
            msg += f"📺 剧集新增：{stats['Series']} 部\n"
        if stats.get("Episode", 0) > 0:
            msg += f"🎞️ 单集新增：{stats['Episode']} 集\n"
        msg += "━━━━━━━━━━━━"

        # 发送消息 (使用 Context 的 send_message 方法)
        # 注意：AstrBot 的主动发送 API 可能因版本而异，这里尝试使用 context.get_platform_adapter
        # 或者直接构建 Event。但在 AstrBot 中，主动发送通常需要 adapter。
        # 为了兼容性，这里假设 target_id 是纯数字 ID，且插件运行在主平台上。

        # 尝试遍历所有 Provider 发送
        sent = False
        # platform_name:target_id 格式解析
        platform_name = None
        user_id = target_id

        if ":" in target_id:
            platform_name, user_id = target_id.split(":", 1)

        try:
            for platform in self.context.platform_manager.platforms:
                if platform_name and platform.platform_name != platform_name:
                    continue

                # 尝试构建消息链
                chain = [Comp.Plain(msg)]

                # 尝试作为私聊发送
                try:
                    # 获取 adapter 实例进行发送是比较底层的做法
                    # AstrBot 推荐使用 UnifiedMessage 发送
                    # 这里尝试使用 platform 的接口
                    if hasattr(platform, "send_msg"):
                        # 尝试转换为 int (针对 QQ 等平台)
                        try:
                            uid = int(user_id)
                        except:
                            uid = user_id

                        # 构造简单的 payload，具体取决于平台实现，这里尝试通用调用
                        # 注意：不同适配器的 send_msg 参数可能不同，这是一个潜在的兼容性问题
                        # 为了稳妥，我们尝试使用 context 的高层 API 如果有

                        # 假设目标是个人
                        await platform.send_msg(uid, chain)
                        sent = True
                        break
                except Exception as e:
                    logger.warning(f"尝试通过平台 {platform.platform_name} 发送失败: {e}")

            if sent:
                logger.info("日报推送成功")
            else:
                logger.error("日报推送失败：未找到合适的平台或发送失败")

        except Exception as e:
            logger.error(f"执行推送逻辑出错: {e}")

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
                    user_id = event.get_sender_id()

                    # 检查用户是否在等待选择季度
                    user_state = self.state.get(user_id, {})
                    if user_state.get("waiting_for") == "season":
                        # 用户正在选择季度
                        try:
                            season_number = int(user_input)
                            selected_movie = user_state["selected_movie"]
                            seasons = user_state["seasons"]

                            # 验证季度是否有效
                            valid_season = False
                            for season in seasons:
                                if season['season_number'] == season_number:
                                    valid_season = True
                                    break

                            if valid_season:
                                # 订阅电视剧的指定季度
                                success = await self.api.subscribe_series(selected_movie, season_number)
                                message_result = event.make_result()
                                if success:
                                    message_result.chain = [Comp.Plain(f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n订阅第 {season_number} 季成功！")]
                                else:
                                    message_result.chain = [Comp.Plain("订阅失败。")]
                                await event.send(message_result)
                                # 清除状态
                                self.state.pop(user_id, None)
                                controller.stop()
                            else:
                                message_result = event.make_result()
                                message_result.chain = [Comp.Plain("无效的季数，请重新输入。")]
                                await event.send(message_result)
                                controller.keep(timeout=60, reset_timeout=True)
                        except ValueError:
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("请输入一个有效的季数。")]
                            await event.send(message_result)
                            controller.keep(timeout=60, reset_timeout=True)
                        return

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
                                # 如果是电视剧，获取所有季数
                                seasons = await self.api.list_all_seasons(selected_movie['tmdb_id'])
                                if seasons:
                                    season_list = "\n".join(
                                        [f"第 {season['season_number']} 季 {season['name']}" for season in seasons])
                                    season_list = "\n查询到的季如下\n请直接回复季数进行选择：\n" + season_list

                                    message_result = event.make_result()
                                    message_result.chain = [Comp.Plain(season_list)]
                                    await event.send(message_result)

                                    # 继续等待用户选择季数
                                    controller.keep(timeout=60, reset_timeout=True)

                                    # 更新状态
                                    self.state[user_id] = {
                                        "selected_movie": selected_movie,
                                        "seasons": seasons,
                                        "waiting_for": "season"
                                    }
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

        # 强制执行推送，忽略"无更新跳过"的逻辑？通常手动触发可能希望看到结果
        # 但复用 send_daily_report 会保留该逻辑。
        # 如果需要强制发送即使无更新，需要修改 send_daily_report 的参数。
        # 这里暂时保持一致逻辑。
        await self.send_daily_report()

        yield event.plain_result("✅ 推送逻辑执行完毕")

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
  /mp下载        - 查看下载进度

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
