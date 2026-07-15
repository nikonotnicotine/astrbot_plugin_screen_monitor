import base64
import aiohttp
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import llm_tool
from astrbot.api import logger

@register("screenspy", "nikonotnicotine", "查岗电脑屏幕插件", "1.1.0")
class ScreenSpy(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

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
        
        # 读取自定义提示词，如果没有配置则使用默认值
        default_prompt = "你是一个查岗助手，请直接回复，详细描述这张电脑屏幕截图的内容。务必告诉我用户现在大概在干什么（比如在写代码、看B站视频、在和谁聊天、在打什么游戏等）。"
        vision_prompt = self.config.get("vision_prompt", default_prompt)

        if not api_url or not api_key:
            return "查岗失败：视觉模型未配置，请提醒用户配置。"

        try:
            logger.info("📸 大模型发起了查岗请求，正在获取屏幕截图...")
            # 1. 获取截图
            screenshot_url = f"http://{host}:{port}/screenshot"
            async with aiohttp.ClientSession() as session:
                async with session.get(screenshot_url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.error(f"截图获取失败，状态码 {resp.status}。")
                        return "查岗失败：截图服务返回异常，可能服务出错。"
                    img_data = await resp.read()
            
            logger.info("📤 截图获取成功，正在请求视觉大模型解析...")
            # 2. 转换为 base64
            base64_img = base64.b64encode(img_data).decode('utf-8')

            # 3. 调用视觉模型 API (兼容 OpenAI 格式)
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},  # 使用读取到的自定义提示词
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                        ]
                    }
                ],
                "max_tokens": 1000
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, headers=headers, json=payload, timeout=45) as resp:
                    if resp.status != 200:
                        error_info = await resp.text()
                        logger.error(f"视觉模型请求出错 {error_info}")
                        return "查岗失败：视觉解析服务异常。"
                    result = await resp.json()
                    
            content = result["choices"][0]["message"]["content"]
            logger.info(f"✅ 视觉模型解析完成：{content}")
            
            # 4. 返回给大模型
            return f"查岗成功，用户当前的电脑屏幕内容描述如下：\n{content}\n\n请根据上述内容，以你的角色人设对用户发送回复。"

        except Exception as e:
            logger.error(f"❌ 查岗功能发生异常：{e}")
            # 捕获包括请求不通、超时等所有异常，返回给 LLM 简短的口语化提示
            return "查岗失败：用户电脑未响应，大概率是没开截图服务或网络断了。"