import httpx
import time
import logging
from pkg.plugin.context import register, handler, BasePlugin, EventContext
from pkg.plugin.events import PersonNormalMessageReceived

# --- 1. 配置信息 ---
# 把这些信息填上你自己的
OPEN_KFID = "wkWXzbIQAAm6b8YG1bHAuGxAXyJtCG6A"  # 你的企业微信客服账号ID
SERVICER_USERID = "LiHaiLong"  # 指定接待的客服人员ID，也可以是一个列表轮流分配
WECOM_CORP_ID = "ww3e43e43b6854c776" # 你的企业ID
WECOM_SECRET = "sI-a1zAI7hB1rZVy-IJnubba01eXL9fmVFKY3SwbY4s" # 你的客服应用Secret

# --- 2. Access Token 管理 ---
# 企业微信的 access_token 有效期为2小时，需要全局缓存
# 这里是一个简单的缓存实现
access_token_cache = {
    "token": None,
    "expires_at": 0
}

async def get_access_token():
    """获取并缓存企业微信的 access_token"""
    now = int(time.time())
    # 如果缓存中的token有效，直接返回
    if access_token_cache["token"] and access_token_cache["expires_at"] > now:
        logging.info("Using cached access_token.")
        return access_token_cache["token"]

    # 否则，重新获取
    logging.info("Fetching new access_token...")
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WECOM_CORP_ID}&corpsecret={WECOM_SECRET}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        if data.get("errcode") == 0:
            token = data["access_token"]
            # 缓存token，并设置7000秒的有效期（比官方的7200秒稍短以防万一）
            access_token_cache["token"] = token
            access_token_cache["expires_at"] = now + 7000
            logging.info("Successfully fetched new access_token.")
            return token
        else:
            logging.error(f"Failed to get access_token: {data}")
            return None
    except Exception as e:
        logging.error(f"Exception while getting access_token: {e}")
        return None


# --- 3. 插件主体 ---
@register(name="TransferToAgentFinal", description="处理转人工逻辑并调用企微API", version="1.0", author="YourName")
class TransferToAgentPlugin(BasePlugin):

    @handler(PersonNormalMessageReceived)
    async def handle_transfer_request(self, ctx: EventContext):
        msg = ctx.event.text_message

        if "转人工" in msg or "找客服" in msg:
            self.ap.logger.info("检测到转人工请求，开始调用企微API...")

            # --- 步骤 1: 获取 access_token ---
            token = await get_access_token()
            if not token:
                self.ap.logger.error("无法获取 access_token，转人工失败。")
                ctx.add_return("reply", ["抱歉，系统繁忙，请稍后再试。"])
                ctx.prevent_default()
                return

            #--- 步骤 2: 提取并转换 external_userid  ---
            try:
                original_user_id = ctx.event.sender_id
                self.ap.logger.info(f"接收到的原始用户ID: {original_user_id}")

                # 查找 "wm" 的起始位置
                wm_start_index = original_user_id.find("wm")

                if wm_start_index != -1:
                    # 从 "wm" 的位置开始，截取到字符串末尾
                    # 这样就保证了ID是以 "wm" 开头的
                    formatted_user_id = original_user_id[wm_start_index:]

                    # 为保险起见，我们仍然清理一下末尾可能存在的 "!" 符号
                    # 因为企微API通常不需要这个符号
                    if formatted_user_id.endswith('!'):
                        formatted_user_id = formatted_user_id[:-1]
                    
                    self.ap.logger.info(f"成功转换为企微格式ID: {formatted_user_id}")
                else:
                    # 如果连 "wm" 都没找到，说明ID格式可能发生了根本变化，必须记录错误并中断
                    self.ap.logger.error(f"处理失败：无法在用户ID '{original_user_id}' 中找到 'wm' 标志。请检查数据源！")
                    ctx.add_return("reply", ["抱歉，无法识别您的用户身份，转接人工失败。"])
                    ctx.prevent_default()
                    return

            except AttributeError:
                self.ap.logger.error("无法从 ctx.event 获取 sender_id，请用 print(dir(ctx.event)) 检查！")
                return
            # --- 步骤 3: 调用企微API变更会话状态 ---
            api_url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans?access_token={token}"
            payload = {
                "open_kfid": OPEN_KFID,
                "external_userid": formatted_user_id,
                "service_state": 3,
                "servicer_userid": SERVICER_USERID
            }

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(api_url, json=payload)
                    response.raise_for_status()
                    result = response.json()

                # --- 步骤 4: 处理API返回结果 ---
                if result.get("errcode") == 0:
                    self.ap.logger.info("成功将会话状态变更为'由人工接待'！")
                    ctx.add_return("reply", ["好的，已为您转接人工客服，请在对话框里直接提问。"])
                else:
                    self.ap.logger.error(f"调用企微转人工API失败: {result}")
                    error_msg = result.get('errmsg', '未知错误')
                    ctx.add_return("reply", [f"抱歉，转接失败：{error_msg}。请稍后再试。"])

            except Exception as e:
                self.ap.logger.error(f"请求企微转人工API时发生异常: {e}")
                ctx.add_return("reply", ["抱歉，转接服务出现网络问题，请稍后再试。"])

            # 无论成功失败，都阻止机器人继续回复
            ctx.prevent_default()