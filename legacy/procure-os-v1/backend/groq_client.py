from groq import Groq
from config import GROQ_API_KEY, MODEL_NAME

_client = Groq(api_key=GROQ_API_KEY)


def chat_completion(messages, tools=None, stream=False, temperature=0.4):
    kwargs = dict(model=MODEL_NAME, messages=messages, temperature=temperature, stream=stream)
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return _client.chat.completions.create(**kwargs)
