import os
import json
import random
import asyncio
from pydub import AudioSegment
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
    SessionFilter,
)
from collections import defaultdict

@register("astrbot_plugin_jiahao", "vmoranv", "艾路迪克都去导管室!", "1.0.0")
class JhdjPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # JHDJ 功能配置
        volume_config = self.config.get("volume_config", {})
        self.min_volume = volume_config.get("min_volume", 20)
        self.max_volume = volume_config.get("max_volume", 100)
        self.min_speed_ms = volume_config.get("min_speed_ms", 100)
        self.max_speed_ms = volume_config.get("max_speed_ms", 500)

        # 确保音量和速度范围有效
        if self.min_volume >= self.max_volume:
            self.min_volume, self.max_volume = 20, 100
            logger.warning("音量配置无效，已重置为默认值 (20-100)。")
        if self.min_speed_ms >= self.max_speed_ms:
            self.min_speed_ms, self.max_speed_ms = 100, 500
            logger.warning("速度配置无效，已重置为默认值 (100-500ms)。")

        # 开鹿游戏配置
        self.kailu_duration_minutes = self.config.get("kailu_game_duration", 30)
        self.kailu_sessions = {} # 用于存储每个群的游戏状态
        self.luguan_messages = {}

        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        # 加载鹿管消息
        luguan_json_path = os.path.join(self.data_dir, 'luguan.json')
        try:
            with open(luguan_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.luguan_messages = {item['count']: item['text'] for item in data.get('messages', [])}
            logger.info(f"成功加载 luguan.json 中的 {len(self.luguan_messages)} 条消息。")
        except FileNotFoundError:
            logger.warning("data/luguan.json 未找到，将不发送里程碑消息。")
        except json.JSONDecodeError:
            logger.error("data/luguan.json 文件格式错误，请检查。")
        except Exception as e:
            logger.error(f"加载 luguan.json 时发生未知错误: {e}")

        logger.info(f"JHDJ 插件已加载。音量范围: {self.min_volume}-{self.max_volume}, 速度范围: {self.min_speed_ms}-{self.max_speed_ms}ms, 开鹿时长: {self.kailu_duration_minutes}分钟")

    def process_audio_sync(self, input_filepath: str, output_filepath: str):
        """
        同步函数，用于处理音频，实现平滑的音量变化。
        这个函数会阻塞，所以应该在单独的线程中运行。
        """
        logger.info(f"开始使用平滑模式处理音频文件: {input_filepath}")
        audio = AudioSegment.from_mp3(input_filepath)
        processed_audio = AudioSegment.empty()

        chunk_size_ms = 50
        
        start_volume = 100.0
        target_volume = float(random.randint(self.min_volume, self.max_volume))
        transition_duration_ms = random.randint(self.min_speed_ms, self.max_speed_ms)
        elapsed_in_transition = 0

        for i in range(0, len(audio), chunk_size_ms):
            if elapsed_in_transition >= transition_duration_ms:
                start_volume = target_volume
                while True:
                    new_target = float(random.randint(self.min_volume, self.max_volume))
                    if abs(new_target - start_volume) >= 30:
                        target_volume = new_target
                        break
                transition_duration_ms = random.randint(self.min_speed_ms, self.max_speed_ms)
                elapsed_in_transition = 0

            progress = elapsed_in_transition / transition_duration_ms
            current_volume = start_volume + (target_volume - start_volume) * progress

            gain = -120 if current_volume <= 0 else (current_volume - 100) * 0.6
            
            chunk = audio[i:i + chunk_size_ms]
            processed_chunk = chunk.apply_gain(gain)
            processed_audio += processed_chunk
            
            elapsed_in_transition += chunk_size_ms

        logger.info(f"音频处理完成，正在导出到: {output_filepath}")
        processed_audio.export(output_filepath, format="mp3")
        logger.info("音频文件已导出。")

    @filter.command("jhdj")
    async def jhdj_handler(self, event: AstrMessageEvent):
        """处理 data 文件夹下的随机 mp3 文件，每秒钟的音量都是[0,100]随机数,处理完了发回去"""
        
        audio_files = [f for f in os.listdir(self.data_dir) if f.lower().endswith('.mp3')]
        if not audio_files:
            yield event.plain_result("错误：data 目录中没有找到任何 .mp3 文件。")
            return

        input_filename = random.choice(audio_files)
        input_filepath = os.path.join(self.data_dir, input_filename)
        
        yield event.plain_result(f"收到请求，正在处理音频 '{input_filename}'，这可能需要一些时间，请稍候...")

        output_filename = f"processed_{random.randint(1000,9999)}_{input_filename}"
        output_filepath = os.path.join(self.data_dir, output_filename)

        try:
            await asyncio.to_thread(self.process_audio_sync, input_filepath, output_filepath)
            
            logger.info(f"准备以语音形式发送处理后的文件: {output_filepath}")
            
            yield event.chain_result([Comp.Record(file=output_filepath)])
            
        except Exception as e:
            logger.error(f"处理音频时出错: {e}", exc_info=True)
            yield event.plain_result("处理音频时发生严重错误，请检查后台日志。")
        finally:
            if os.path.exists(output_filepath):
                try:
                    os.remove(output_filepath)
                    logger.info(f"已清理临时文件: {output_filepath}")
                except OSError as e:
                    logger.error(f"清理临时文件失败: {e}")

    @filter.command("开鹿")
    async def kailu_handler(self, event: AstrMessageEvent):
        """开始“开鹿”游戏，发送趣图并监听特定关键词。"""
        
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("不要一个人偷偷鹿。")
            return

        if group_id in self.kailu_sessions and self.kailu_sessions[group_id].get('is_running', False):
            yield event.plain_result("机长正在执行航班计划！")
            return

        image_path = os.path.join(self.data_dir, 'luguanluguanshijiandao.jpg')
        if not os.path.exists(image_path):
            yield event.plain_result("错误：开鹿图片 'luguanluguanshijiandao.jpg' 不存在于 data 目录中。")
            return

        # 初始化游戏会话
        self.kailu_sessions[group_id] = {
            'is_running': True,
            'records': defaultdict(int)
        }

        yield event.chain_result([
            Comp.Image.fromFileSystem(image_path),
            Comp.Plain(f"导管室开放！持续时间 {self.kailu_duration_minutes} 分钟。\n发送“不鹿了”可以提前迫降。")
        ])

        class CustomFilter(SessionFilter):
            def filter(self, event: AstrMessageEvent) -> str:
                return event.get_group_id() if event.get_group_id() else event.unified_msg_origin

        @session_waiter(timeout=self.kailu_duration_minutes * 60, record_history_chains=False)
        async def kailu_waiter(controller: SessionController, event: AstrMessageEvent):
            msg = event.message_str.lower()
            sender_id = event.get_sender_id()
            
            if "不鹿了" in msg:
                await event.send(event.plain_result("本次航班已紧急迫降。"))
                controller.stop()
                return

            keywords = ["鹿", "撸管", "🦌"]
            if any(keyword in msg for keyword in keywords):
                records = self.kailu_sessions[group_id]['records']
                records[sender_id] += 1
                current_count = records[sender_id]
                logger.info(f"群 {group_id} 中几把 {sender_id} 鹿了 {current_count}")

                # 检查是否达到里程碑
                if self.luguan_messages and current_count in self.luguan_messages:
                    message_to_send = self.luguan_messages[current_count]
                    await event.send(event.plain_result(message_to_send))

        try:
            await kailu_waiter(event, session_filter=CustomFilter())
        except TimeoutError:
            await event.send(event.plain_result(f"{self.kailu_duration_minutes} 分钟时间到，航班降落！"))
        except Exception as e:
            logger.error(f"开鹿会话出错: {e}", exc_info=True)
            await event.send(event.plain_result("导管室出现未知错误，已强制关闭。"))
        finally:
            records = self.kailu_sessions.get(group_id, {}).get('records', {})
            if records:
                result_text = "本次航班已结束:\n"
                sorted_records = sorted(records.items(), key=lambda item: item[1], reverse=True)
                for user_id, count in sorted_records:
                    result_text += f"几把 {user_id}:鹿了 {count} 次\n"
            else:
                result_text = "导管室空无一人,十年前的仇难道不报了吗!"
            
            # 清理会话
            if group_id in self.kailu_sessions:
                del self.kailu_sessions[group_id]

            await event.send(event.plain_result(result_text))
            event.stop_event()

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        pass
