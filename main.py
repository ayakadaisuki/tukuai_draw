import os, time, base64, yaml, aiohttp, asyncio, json, re
from pathlib import Path
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.message_components import Plain

@register("tukuai_draw", "土块AI画图", "1.0.0", "Custom")
class TukuaiDrawPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = Path(__file__).parent
        self.config_path = self.data_dir / "config.yaml"
        self.temp_dir = self.data_dir / "temp"
        self.temp_dir.mkdir(exist_ok=True)
        self.config = self.load_config()
        self.cooldowns = {}
        self.quality_prefix = "masterpiece, best quality, "  # 去掉了 1girl，让你完全控制主体

    def load_config(self):
        default = {
            "api_key": "",
            "master_qq": "",
            "api_base": "http://datukuai.top:1450",
            "txt2img_path": "/ht2.php",
            "cooldown_sec": 30,
            "default_prompt": "1girl, beautiful, detailed eyes",
            "negative_prompt": "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry",
            "width": 512,
            "height": 768,
            "steps": 28,
            "cfg_scale": 7.5,
            "sampler": "Euler a"
        }
        if not self.config_path.exists():
            self.config_path.write_text(yaml.dump(default, allow_unicode=True))
            return default
        with open(self.config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            return cfg if cfg else default

    def save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, allow_unicode=True)

    async def check_quota(self) -> int:
        master_qq = self.config.get("master_qq")
        if not master_qq:
            logger.error("❌ 未配置 master_qq")
            return -1
        url = f"{self.config['api_base']}/qx2.php"
        params = {"tk": self.config["api_key"], "qq": str(master_qq)}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    text = await resp.text()
                    data = json.loads(text)
                    return data.get("recs", 0) if data.get("code") == 1 else -1
        except:
            return -1

    async def translate_prompt(self, text: str) -> str:
        # 简单的翻译逻辑，实际使用建议尽量用英文
        if not re.search(r'[\u4e00-\u9fff]', text):
            return text
        try:
            url = f"https://api.52vmy.cn/api/query/fanyi/youdao?msg={text}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    return data.get("data", {}).get("target", text)
        except:
            return text

    async def process_prompt(self, raw_prompt: str) -> str:
        if not raw_prompt:
            return self.quality_prefix + self.config.get("default_prompt", "1girl")
        
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', raw_prompt))
        
        if has_chinese:
            logger.info(f"🔤 检测到中文，正在翻译...")
            prompt = await self.translate_prompt(raw_prompt)
        else:
            logger.info(f"🔤 英文提示词，优化处理中...")
            # 1. 替换中文逗号，保留空格（部分标签需要空格分隔权重）
            prompt = raw_prompt.replace('，', ',')
            # 2. 清理多余空格，但保留单词间的必要空格（不要强制把空格转下划线，以免破坏 (tag:1.2) 权重语法）
            prompt = re.sub(r'\s+', ' ', prompt).strip()
            
        final_prompt = self.quality_prefix + prompt
        logger.info(f"📤 最终 Prompt (长度:{len(final_prompt)}): {final_prompt[:50]}...")
        return final_prompt

    @filter.command("土块画图")
    async def cmd_draw(self, event: AstrMessageEvent, prompt: str = ""):
        qq_id = event.get_sender_id()

        # 手动从完整消息中提取 Prompt，防止框架截断长提示词
        full_msg = event.get_message_str()
        match = re.match(r"^(?:/|#)?土块画图\s*", full_msg)
        if match:
            raw_prompt = full_msg[match.end():].strip()
        else:
            raw_prompt = prompt

        logger.info(f"🔥 [土块画图] 接收到完整提示词 (长度:{len(raw_prompt)})")
        
        if not self.config.get("api_key"):
            yield event.plain_result("⚠️ 未配置秘钥，请让管理员使用 /设置土块秘钥 <你的秘钥>")
            event.stop_event()
            return

        now = time.time()
        cd = self.config.get("cooldown_sec", 30)
        last = self.cooldowns.get(qq_id, 0)
        if now - last < cd:
            yield event.plain_result(f"⏳ 冷却中，请等待 {cd - int(now - last)} 秒")
            event.stop_event()
            return

        rem = await self.check_quota()
        if rem == -1:
            yield event.plain_result("❌ 额度查询失败，请检查配置")
            event.stop_event()
            return
        if rem <= 0:
            yield event.plain_result("❌ 次数不足，请续费")
            event.stop_event()
            return

        processed_prompt = await self.process_prompt(raw_prompt)
        yield event.plain_result("🎨 正在生成图片，请稍候...")

        payload = {
            "prompt": processed_prompt,
            "negative_prompt": self.config.get("negative_prompt", ""),
            "steps": self.config.get("steps", 28),
            "cfg_scale": self.config.get("cfg_scale", 7.5),
            "width": self.config.get("width", 512),
            "height": self.config.get("height", 768),
            "sampler_name": self.config.get("sampler", "Euler a"),
            "seed": -1,
            "enable_hr": False,
            "my": self.config["api_key"]
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['api_key']}",
            "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36"
        }

        try:
            draw_url = f"{self.config['api_base']}{self.config.get('txt2img_path', '/ht2.php')}"
            async with aiohttp.ClientSession() as session:
                async with session.post(draw_url, json=payload, headers=headers, timeout=180) as resp:
                    raw_text = await resp.text()
                    data = json.loads(raw_text)

            if "images" not in data or not data["images"]:
                yield event.plain_result(f"❌ 生成失败: {data.get('info', '未知错误')}")
                event.stop_event()
                return

            img_b64 = data["images"][0]
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            img_bytes = base64.b64decode(img_b64)

            img_path = self.temp_dir / f"{qq_id}_{int(time.time())}.png"
            img_path.write_bytes(img_bytes)
            self.cooldowns[qq_id] = time.time()
            
            logger.info(f"✅ 图片已保存: {img_path}")
            yield event.image_result(str(img_path.resolve()))
            event.stop_event()
            return

        except asyncio.TimeoutError:
            yield event.plain_result("⏱️ 请求超时，请稍后重试")
            event.stop_event()
            return
        except Exception as e:
            logger.error(f"❌ 异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 绘图失败: {str(e)}")
            event.stop_event()
            return

    @filter.command("设置土块秘钥")
    async def cmd_set_key(self, event: AstrMessageEvent, key: str):
        if not event.is_admin_or_owner():
            yield event.plain_result("⚠️ 仅管理员可设置")
            event.stop_event()
            return
        self.config["api_key"] = key.strip()
        self.config["master_qq"] = event.get_sender_id()
        self.save_config()
        yield event.plain_result("✅ 秘钥已保存")
        event.stop_event()
        return

    @filter.command("查询土块额度")
    async def cmd_check_quota(self, event: AstrMessageEvent):
        rem = await self.check_quota()
        if rem == -1:
            yield event.plain_result("❌ 查询失败")
        else:
            yield event.plain_result(f"📊 剩余次数: {rem}")
        event.stop_event()
        return
