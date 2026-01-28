import traceback
from typing import List, Optional
from datetime import datetime
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import httpx

class MoviepilotApi:
    def __init__(self, config: dict):
        self.base_url = config.get('mp_url')
        self.mp_username = config.get('mp_username')
        self.mp_password = config.get('mp_password')
        print(self.mp_username)        

    async def _get_mp_token(self) -> str | None:
        _api_path = "/api/v1/login/access-token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "accept": "application/json"
        }
        # 构建表单数据
        form_data = {
            "username": self.mp_username,
            "password": self.mp_password,
        }

        if self.mp_password is None:
            logger.error("moviepilot的密码不能为空")
            return ""
        else:
            # 发送 POST 请求并传递表单数据
            data = await self._request(
                url=self.base_url + _api_path,
                method="POST-DATA",
                headers=headers,
                data=form_data
            )
            return data.get("access_token", None) if data else None

    async def _get_headers(self) -> dict[str, str] | None:
        _token = await self._get_mp_token()
        if _token:
            return {
                "Authorization": f"Bearer {_token}",
                'User-Agent': "nonebot2/0.0.1"
            }
        else:
            logger.error("访问MoviePilot失败，请确认密码或者是否开启了两步验证")
            return

    async def search_media_info(self, media_name: str) -> dict | None:
        _api_path = f"/api/v1/media/search?title={media_name}"
        try:
            return await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=await self._get_headers()
            )
        except Exception as e:
            logger.error(f"Error searching movies: {e}\n{traceback.format_exc()}")
            return None

    async def list_all_seasons(self, tmdbid: str) -> dict | None:
        _api_path = f"/api/v1/tmdb/seasons/{tmdbid}"
        try:
            return await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=await self._get_headers()
            )
        except Exception as e:
            logger.error(f"Error listing seasons: {e}")
            return None

    async def subscribe_movie(self, movie: dict) -> bool:
        _api_path = "/api/v1/subscribe/"
        body = {
            "name": movie['title'],
            "tmdbid": movie['tmdb_id'],
            "type": "电影"
        }
        try:
            response = await self._request(
                url=self.base_url + _api_path,
                method="POST-JSON",
                headers=await self._get_headers(),
                data=body
            )
            logger.info(response)
            return response.get("success", False) if response else False
        except Exception as e:
            logger.error(f"Error subscribing to movie: {e}")
            return False

    async def subscribe_series(self, movie: dict, season: int) -> bool:
        _api_path = "/api/v1/subscribe/"
        body = {
            "name": movie['title'],
            "tmdbid": movie['tmdb_id'],
            "season": season
        }
        try:
            response = await self._request(
                url=self.base_url + _api_path,
                method="POST-JSON",
                headers=await self._get_headers(),
                data=body
            )
            return response.get("success", False) if response else False
        except Exception as e:
            logger.error(f"Error subscribing to series: {e}")
            return False

    async def subscribe_all_seasons(self, movie: dict, seasons: list) -> dict:
        """订阅电视剧的所有季

        Args:
            movie: 电视剧信息
            seasons: 季度列表

        Returns:
            dict: {"success": int, "failed": int, "total": int}
        """
        result = {"success": 0, "failed": 0, "total": len(seasons)}

        for season in seasons:
            season_num = season.get('season_number', 0)
            if season_num <= 0:  # 跳过特辑等（season 0）
                result["total"] -= 1
                continue

            success = await self.subscribe_series(movie, season_num)
            if success:
                result["success"] += 1
            else:
                result["failed"] += 1

        return result

    async def _request(
            self,
            url,
            method="GET",
            headers=None,
            data=None
    ) -> List | None:

        if headers is None:
            headers = {'user-agent': 'nonebot2/0.0.1'}
        timeout = httpx.Timeout(120.0, read=120.0)

        logger.info(f"""
                url: {url}
                method = {method}
                headers = {headers}
                data = {data}
                """)

        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                r = await client.get(url, headers=headers)
            elif method == "POST-JSON":
                r = await client.post(url, headers=headers, json=data)
            elif method == "POST-DATA":
                r = await client.post(url, headers=headers, data=data)
            else:
                return

            if r.status_code != 200:
                logger.error(f"{r.status_code} 请求错误\n{r}")
            else:
                return r.json()

    async def get_subscribes(self) -> List[dict] | None:
        """获取当前订阅列表
        Returns:
            List[dict] | None: 返回订阅列表，每个订阅包含以下字段：
            - id: int 订阅ID
            - name: str 名称
            - type: str 类型（电影/电视剧）
            - year: str 年份
            - season: int 季数（电视剧）
            - total_episode: int 总集数
            - lack_episode: int 缺失集数
            - state: str 状态
        """
        _api_path = "/api/v1/subscribe/"
        try:
            headers = await self._get_headers()
            if not headers:
                logger.error("获取认证头失败")
                return None

            data = await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=headers
            )

            if not data:
                logger.info("当前没有订阅")
                return []

            return data

        except Exception as e:
            logger.error(f"获取订阅列表失败: {e}")
            return None

    async def get_download_progress(self) -> List[dict] | None:
        """获取下载进度
        Returns:
            List[dict] | None: 返回下载任务列表，每个任务包含以下字段：
            - media: dict 媒体信息
                - title: str 中文标题
                - type: str 类型（电影/电视剧）
            - progress: float 下载进度（百分比）
            - state: str 下载状态
        """
        _api_path = "/api/v1/download/"
        try:
            headers = await self._get_headers()
            if not headers:
                logger.error("获取认证头失败")
                return None

            data = await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=headers
            )

            if not data:
                logger.info("当前没有正在下载的任务")
                return []

            return data

        except Exception as e:
            logger.error(f"获取下载进度失败: {e}")
            return None


class EmbyApi:
    """Emby 服务器 API 封装"""

    def __init__(self, config: dict):
        self.base_url = config.get('emby_url', '').rstrip('/')
        self.api_key = config.get('emby_api_key', '')
        self.user_id = config.get('emby_user_id', '')
        self.max_results = config.get('emby_max_results', 10)

    def _get_headers(self) -> dict:
        """获取 Emby API 请求头"""
        return {
            'X-Emby-Token': self.api_key,
            'Accept': 'application/json'
        }

    async def _request(self, url: str, method: str = "GET") -> Optional[dict]:
        """发送 HTTP 请求"""
        try:
            timeout = httpx.Timeout(30.0, read=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    r = await client.get(url, headers=self._get_headers())
                else:
                    return None

                if r.status_code != 200:
                    logger.error(f"Emby API 请求失败: {r.status_code}")
                    return None
                return r.json()
        except Exception as e:
            logger.error(f"Emby API 请求异常: {e}")
            return None

    def is_configured(self) -> bool:
        """检查 Emby 是否已配置"""
        return bool(self.base_url and self.api_key)

    async def get_latest_media(self, media_type: str = "all") -> List[dict]:
        """获取最新入库的媒体

        Args:
            media_type: 媒体类型，可选 "movie"(电影), "series"(电视剧), "all"(全部)

        Returns:
            List[dict]: 媒体列表，包含标题、年份、类型、入库时间等信息
        """
        if not self.is_configured():
            logger.error("Emby 未配置")
            return []

        # 根据类型设置过滤条件
        include_types = ""
        if media_type == "movie":
            include_types = "Movie"
        elif media_type == "series":
            include_types = "Series"
        else:
            include_types = "Movie,Series"

        # 构建 API 请求
        params = f"?SortBy=DateCreated&SortOrder=Descending&IncludeItemTypes={include_types}&Recursive=true&Limit={self.max_results}"

        if self.user_id:
            url = f"{self.base_url}/Users/{self.user_id}/Items{params}"
        else:
            url = f"{self.base_url}/Items{params}"

        try:
            data = await self._request(url)
            if not data or 'Items' not in data:
                return []

            results = []
            for item in data['Items']:
                media_info = {
                    'id': item.get('Id', ''),
                    'name': item.get('Name', '未知'),
                    'original_title': item.get('OriginalTitle', ''),
                    'year': item.get('ProductionYear', ''),
                    'type': '电影' if item.get('Type') == 'Movie' else '电视剧',
                    'date_created': self._format_date(item.get('DateCreated', '')),
                    'overview': item.get('Overview', '')[:100] + '...' if item.get('Overview') and len(item.get('Overview', '')) > 100 else item.get('Overview', ''),
                    'community_rating': item.get('CommunityRating', 0),
                }
                results.append(media_info)

            return results

        except Exception as e:
            logger.error(f"获取 Emby 最新入库失败: {e}\n{traceback.format_exc()}")
            return []

    async def search_media(self, keyword: str) -> List[dict]:
        """在 Emby 媒体库中搜索

        Args:
            keyword: 搜索关键词

        Returns:
            List[dict]: 搜索结果列表
        """
        if not self.is_configured():
            logger.error("Emby 未配置")
            return []

        params = f"?SearchTerm={keyword}&IncludeItemTypes=Movie,Series&Recursive=true&Limit={self.max_results}"

        if self.user_id:
            url = f"{self.base_url}/Users/{self.user_id}/Items{params}"
        else:
            url = f"{self.base_url}/Items{params}"

        try:
            data = await self._request(url)
            if not data or 'Items' not in data:
                return []

            results = []
            for item in data['Items']:
                media_info = {
                    'id': item.get('Id', ''),
                    'name': item.get('Name', '未知'),
                    'original_title': item.get('OriginalTitle', ''),
                    'year': item.get('ProductionYear', ''),
                    'type': '电影' if item.get('Type') == 'Movie' else '电视剧',
                    'date_created': self._format_date(item.get('DateCreated', '')),
                }
                results.append(media_info)

            return results

        except Exception as e:
            logger.error(f"Emby 搜索失败: {e}\n{traceback.format_exc()}")
            return []

    async def get_library_stats(self) -> dict:
        """获取媒体库统计信息

        Returns:
            dict: 包含电影数量、电视剧数量等统计信息
        """
        if not self.is_configured():
            logger.error("Emby 未配置")
            return {}

        stats = {'movies': 0, 'series': 0, 'episodes': 0}

        try:
            # 获取电影数量
            movie_url = f"{self.base_url}/Items/Counts"
            data = await self._request(movie_url)

            if data:
                stats['movies'] = data.get('MovieCount', 0)
                stats['series'] = data.get('SeriesCount', 0)
                stats['episodes'] = data.get('EpisodeCount', 0)

            return stats

        except Exception as e:
            logger.error(f"获取 Emby 统计信息失败: {e}")
            return stats

    async def get_today_additions_stats(self) -> dict:
        """获取今日入库统计及详情 (从今日0点开始)，同一剧集的集数会合并显示"""
        if not self.is_configured():
            return {}

        try:
            now = datetime.now()
            start_of_day = now.strftime("%Y-%m-%dT00:00:00Z")

            # 查询电影、剧集、单集
            params = f"?Recursive=true&IncludeItemTypes=Movie,Series,Episode&MinDateCreated={start_of_day}&SortBy=DateCreated&SortOrder=Descending"

            if self.user_id:
                url = f"{self.base_url}/Users/{self.user_id}/Items{params}"
            else:
                url = f"{self.base_url}/Items{params}"

            data = await self._request(url)

            result = {
                "stats": {"Movie": 0, "Series": 0, "Episode": 0, "Total": 0},
                "items": []
            }

            if data and 'Items' in data:
                items = data['Items']
                result["stats"]["Total"] = len(items)

                # 统计数量
                for item in items:
                    itype = item.get('Type')
                    if itype in result["stats"]:
                        result["stats"][itype] += 1

                # 分类处理：电影、剧集、单集
                movies = []
                series_map = {}  # 用于合并同一剧集的不同集数
                new_series = []  # 新入库的剧集本身

                for item in items:
                    itype = item.get('Type')
                    name = item.get('Name', '未知')
                    year = item.get('ProductionYear', '')

                    if itype == "Movie":
                        movies.append(f"[电影] {name} ({year})" if year else f"[电影] {name}")

                    elif itype == "Series":
                        new_series.append(f"[剧集] {name} ({year})" if year else f"[剧集] {name}")

                    elif itype == "Episode":
                        series_name = item.get('SeriesName', '未知剧集')
                        series_id = item.get('SeriesId', series_name)
                        season_num = item.get('ParentIndexNumber', 0)
                        ep_num = item.get('IndexNumber', 0)

                        # 按 series_id + season 分组
                        key = f"{series_id}_S{season_num}"
                        if key not in series_map:
                            series_map[key] = {
                                'name': series_name,
                                'season': season_num,
                                'episodes': []
                            }
                        if ep_num:
                            series_map[key]['episodes'].append(ep_num)

                # 构建合并后的剧集列表
                merged_series = []
                for key, info in series_map.items():
                    eps = sorted(info['episodes'])
                    if eps:
                        # 合并连续集数，如 [1,2,3,5,6] -> "E1-E3, E5-E6"
                        ep_ranges = self._merge_episode_ranges(eps)
                        season_str = f"S{info['season']}" if info['season'] else ""
                        merged_series.append(f"[剧集] {info['name']} {season_str} {ep_ranges}")
                    else:
                        merged_series.append(f"[剧集] {info['name']}")

                # 组合最终列表：电影 -> 新剧集 -> 合并后的单集
                result["items"] = movies + new_series + merged_series

            return result

        except Exception as e:
            logger.error(f"获取今日入库统计失败: {e}")
            return {}

    def _merge_episode_ranges(self, episodes: list) -> str:
        """将集数列表合并为范围字符串，如 [1,2,3,5,6] -> 'E1-E3, E5-E6'"""
        if not episodes:
            return ""

        episodes = sorted(set(episodes))
        ranges = []
        start = end = episodes[0]

        for ep in episodes[1:]:
            if ep == end + 1:
                end = ep
            else:
                if start == end:
                    ranges.append(f"E{start}")
                else:
                    ranges.append(f"E{start}-E{end}")
                start = end = ep

        # 添加最后一个范围
        if start == end:
            ranges.append(f"E{start}")
        else:
            ranges.append(f"E{start}-E{end}")

        return ", ".join(ranges)

    def _format_date(self, date_str: str) -> str:
        """格式化日期字符串"""
        if not date_str:
            return ''
        try:
            # Emby 返回的日期格式类似: 2024-01-15T10:30:00.0000000Z
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return date_str[:16] if len(date_str) > 16 else date_str




