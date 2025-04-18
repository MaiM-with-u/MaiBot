from openai import OpenAI


class LLMMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content

    def to_dict(self):
        return {"role": self.role, "content": self.content}


class LLMClient:
    """LLM客户端，对应一个API服务商"""

    def __init__(self, url, api_key):
        self.client = OpenAI(
            base_url=url,
            api_key=api_key,
        )

    def send_chat_request(self, model, messages):
        """发送对话请求，等待返回结果"""
        response = self.client.chat.completions.create(model=model, messages=messages, stream=False)
        if hasattr(response.choices[0].message, "reasoning_content"):
            # 有单独的推理内容块
            reasoning_content = response.choices[0].message.reasoning_content
            content = response.choices[0].message.content
        else:
            # 无单独的推理内容块
            response = response.choices[0].message.content.split("<think>")[-1].split("</think>")
            # 如果有推理内容，则分割推理内容和内容
            if len(response) == 2:
                reasoning_content = response[0]
                content = response[1]
            else:
                reasoning_content = None
                content = response[0]

        return reasoning_content, content

    def send_embedding_request(self, model, text):
        """发送嵌入请求，等待返回结果"""
        text = text.replace("\n", " ")
        return self.client.embeddings.create(input=[text], model=model).data[0].embedding
