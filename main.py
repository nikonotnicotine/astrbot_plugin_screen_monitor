import base64
import aiohttp
import tempfile
import os
import asyncio
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import llm_tool, MessageChain, Image
from astrbot.api import logger


@register("screenspy", "nikonotnicotine", "查岗电脑屏幕插件", "1.1.4")
class ScreenSpy(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.auto_fallback = self.config.get("auto_fallback_to_phone", False)
        self.fallback_prompt_template = self.config.get(
            "fallback_prompt_template",
            "当前电脑查岗因为{{error}}无法查看，系统建议查看手机屏幕，请调用手机查岗工具。"
        )

    @llm_tool("check_computer_screen")
    async def check_computer_screen(self, event: AstrMessageEvent):
        """
        当你（AI）想查看用户当前电脑屏幕界面时调用此工具。它会截取用户电脑真实屏幕，并返回详细的画面描述供你分析。
        """
        host = self.config.get("server_host", "127.0.0.1")
        port = self.config.get("server_port", 6878)
        api_url = self.config.get("vision_api_url", "")
        api_key = self.config.get("vision_api_key", "")
        model_name = self.config.get("vision_model_name", "gpt-4o")
        send_to_user = self.config.get("send_screenshot_to_user", False)

        # 获取超时设置
        screenshot_timeout = self.config.get("screenshot_timeout", 30)
        vision_api_timeout = self.config.get("vision_api_timeout", 60)

        # 读取自定义提示词，如果没有配置则使用默认值
        default_prompt = "你是一个查岗助手，请直接回复，详细描述这张电脑屏幕截图的内容。务必告诉我用户现在大概在干什么（比如在写代码、看B站视频、在和谁聊天、在打什么游戏等）。"
        vision_prompt = self.config.get("vision_prompt", default_prompt)

        if not api_url or not api_key:
            error_msg = "视觉模型未配置"
            if self.auto_fallback:
                return self.fallback_prompt_template.replace("{{error}}", error_msg)
            return f"查岗失败：{error_msg}，请提醒用户配置。"

        try:
            logger.info(
                f"📸 大模型发起了查岗请求，正在获取屏幕截图 (超时设定: {screenshot_timeout}秒)..."
            )
            # 1. 获取截图
            screenshot_url = f"http://{host}:{port}/screenshot"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    screenshot_url, timeout=screenshot_timeout
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"截图获取失败，状态码 {resp.status}。")
                        error_msg = "截图服务返回异常"
                        if self.auto_fallback:
                            return self.fallback_prompt_template.replace("{{error}}", error_msg)
                        return f"查岗失败：{error_msg}，可能服务出错。"
                    img_data = await resp.read()

            logger.info(
                f"📤 截图获取成功，正在请求视觉大模型解析 (超时设定: {vision_api_timeout}秒)..."
            )

            if send_to_user:
                try:
                    # 使用临时文件保存图片以发送，发送完清理
                    fd, temp_path = tempfile.mkstemp(suffix=".jpg")
                    with os.fdopen(fd, "wb") as f:
                        f.write(img_data)

                    # 兼容不同版本的 AstrBot 构建 Image 格式
                    try:
                        image_component = Image.fromFileSystem(temp_path)
                    except AttributeError:
                        image_component = Image(path=temp_path)

                    # 直接发送图片，不带提示文字
                    mc = MessageChain()
                    mc.chain.append(image_component)

                    await event.send(mc)
                    logger.info("图片已同步发送给用户。")

                    # 启动后台任务延迟 10 秒删除临时文件
                    async def cleanup_temp_file(path):
                        await asyncio.sleep(10)
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                        except Exception as e:
                            logger.error(f"清理临时文件失败: {e}")

                    asyncio.create_task(cleanup_temp_file(temp_path))
                except Exception as e:
                    logger.error(f"发送截图给用户失败: {e}")
            # ============================================================

            # 2. 转换为 base64
            base64_img = base64.b64encode(img_data).decode("utf-8")

            # 3. 调用视觉模型 API (兼容 OpenAI 格式)
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": vision_prompt,
                            },  # 使用读取到的自定义提示词
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_img}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 1000,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url, headers=headers, json=payload, timeout=vision_api_timeout
                ) as resp:
                    if resp.status != 200:
                        error_info = await resp.text()
                        logger.error(f"视觉模型请求出错 {error_info}")
                        error_msg = "视觉解析服务异常"
                        if self.auto_fallback:
                            return self.fallback_prompt_template.replace("{{error}}", error_msg)
                        return f"查岗失败：{error_msg}。"
                    result = await resp.json()

            content = result["choices"][0]["message"]["content"]
            logger.info(f"✅ 视觉模型解析完成：{content}")

            # 4. 返回给大模型
            return f"查岗成功，用户当前的电脑屏幕内容描述如下：\n{content}\n\n请根据上述内容，以你的角色人设对用户发送回复。"

        except asyncio.TimeoutError:
            logger.error("❌ 查岗请求超时！可能是网络太慢或截图客户端未响应。")
            error_msg = "请求超时，用户电脑网络可能较差"
            if self.auto_fallback:
                return self.fallback_prompt_template.replace("{{error}}", error_msg)
            return f"查岗失败：{error_msg}。"
        except Exception as e:
            logger.error(f"❌ 查岗功能发生异常：{type(e).__name__} - {e}")
            # 捕获包括请求不通等所有异常
            error_msg = "用户电脑未响应，大概率是没开截图服务或网络断了"
            if self.auto_fallback:
                return self.fallback_prompt_template.replace("{{error}}", error_msg)
            return f"查岗失败：{error_msg}。"
