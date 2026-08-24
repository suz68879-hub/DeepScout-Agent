import logging
import os

import httpx
from config import settings
from services.utils import Signer

logger = logging.getLogger(__name__)



class RagService:
    def __init__(self):
        # 1. 加载基础鉴权配置
        self.ak = settings.VOLC_AK
        self.sk = settings.VOLC_SK
        
        # 2. 知识库特有配置
        # 建议后续将这些变量加入 .env 和 config.py 中，这里暂时使用 os.getenv 读取
        # 如果 .env 中未配置，将使用默认值
        self.collection_name = os.getenv("KB_COLLECTION_NAME", "dw_ai")  # 知识库集合名称
        self.project_name = os.getenv("KB_PROJECT_NAME", "default")      # 项目名称
        self.account_id = os.getenv("VOLC_ACCOUNT_ID")               # 火山引擎主账号ID (必填)
        
        # 知识库服务的固定配置
        self.host = "api-knowledgebase.mlp.cn-beijing.volces.com"
        self.region = "cn-north-1" # 示例代码中使用的是 cn-north-1
        self.service = "air"       # 知识库服务的 Service 名通常为 air

    async def retrieve(self, query: str) -> str:
        """
        根据用户问题检索知识库
        :param query: 用户查询语句
        :return: 整合后的上下文文本
        """
        # 基础校验
        if not self.ak or not self.sk or not self.account_id:
            logger.warning("RAG retrieval disabled because required credentials are missing")
            return ""

        path = "/api/knowledge/collection/search_knowledge"
        
        # 3. 构造请求体 (参考官方示例)
        body = {
            "project": self.project_name,
            "name": self.collection_name,
            "query": query,
            "limit": 1, # 获取相关度最高的前3条
            "pre_processing": {
                "need_instruction": True,
                "return_token_usage": True,
                "messages": [{"role": "user", "content": query}]
            },
            "post_processing": {
                "get_attachment_link": True
            }
        }

        # 4. 构造 Header
        # 注意：V-Account-Id 是知识库接口必须的
        headers = {
            "Host": self.host,
            "Content-Type": "application/json",
            "V-Account-Id": self.account_id 
        }

        # 构造待签名的请求数据
        request_data = {
            "method": "POST",
            "path": path,
            "headers": headers,
            "body": body,
            "params": {}
        }



        try:
            # 5. 计算签名 (复用 utils.py 中的 Signer)
            # 知识库使用的是 air / cn-north-1
            signer = Signer(request_data, service=self.service, region=self.region)
            signer.add_authorization({
                "accessKeyId": self.ak,
                "secretKey": self.sk
            })
            
            # 6. 发送异步请求
            url = f"https://{self.host}{path}"
            
            async with httpx.AsyncClient() as client:
                # request_data['headers'] 已经被 signer 修改，包含了 Authorization 字段
                resp = await client.post(
                    url, 
                    headers=request_data["headers"], 
                    json=body,
                    timeout=10.0
                )

            # 6. 解析响应内容
            if resp.status_code != 200:
                logger.warning("RAG provider request failed status=%s", resp.status_code)
                return ""

            data = resp.json()
            
            # --- 核心提取逻辑 ---
            # 1. 按照层级定位到 result_list
            # 使用 .get() 级联获取，防止中间某个 Key 缺失导致报错
            result_list = data.get("data", {}).get("result_list", [])
            
            # 2. 提取所有 item 中的 content
            # 兼容多条数据：遍历列表，只取 content 字段不为空的部分
            contents = [item.get("content", "") for item in result_list if item.get("content")]
            
            
            if not contents:
                logger.info("RAG retrieval returned no matching content")
                return ""

            # 3. 将多条 content 拼接成一个完整的字符串返回
            # 使用双换行符分隔不同的知识块，方便 LLM 区分
            context_text = "\n\n".join(contents)
            
            logger.info("RAG retrieval returned %s content blocks", len(contents))
            return context_text


        except Exception as exc:
            logger.error("RAG retrieval failed error_type=%s", type(exc).__name__)
            return ""

# 实例化单例
rag_service = RagService()
