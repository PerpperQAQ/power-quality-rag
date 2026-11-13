"""Mainland Tongyi HTTP APIs with LangChain chat/embedding adapters.

Only observed upstream usage is reported. There is no estimated token fallback,
credential logging, mock response, or automatic retry of billable requests.

API contracts verified against Alibaba Cloud's official documentation:
https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api
https://help.aliyun.com/en/model-studio/rerank
https://help.aliyun.com/en/model-studio/qwen-vl-ocr-api-reference
"""

from __future__ import annotations

import asyncio
import base64
import math
import re
import json
import weakref
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langsmith import tracing_context
from pydantic import ConfigDict, Field
from .model_catalog import MODEL_INFO


_CLOUD_SEMAPHORES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _cloud_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _CLOUD_SEMAPHORES:
        _CLOUD_SEMAPHORES[loop] = asyncio.Semaphore(2)
    return _CLOUD_SEMAPHORES[loop]


class ProviderError(Exception):
    """An intentionally sanitized, user-facing upstream failure."""

    def __init__(self, code: str, message: str, status_code: int | None = None,
                 usage: list[dict] | None = None, partial_data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.usage = list(usage or [])
        self.partial_data = partial_data


@dataclass
class ProviderResult:
    data: Any
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    model: str = ""
    kind: str = ""
    request_id: str | None = None
    attempts: list[dict] = field(default_factory=list)
    cached_tokens: int | None = None

    @property
    def usage(self) -> dict:
        return {
            "model": self.model,
            "kind": self.kind,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "request_id": self.request_id,
            "cached_tokens": self.cached_tokens,
        }


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(payload: dict, model: str, kind: str, data: Any) -> ProviderResult:
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        raise ProviderError("invalid_response", "通义返回的用量格式无效。")
    details=usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached=_count(details.get("cached_tokens")) if isinstance(details,dict) else None
    input_tokens=_count(usage.get("input_tokens", usage.get("prompt_tokens")))
    if input_tokens is not None and cached is not None and cached>input_tokens:cached=None
    result = ProviderResult(
        data=data,
        input_tokens=input_tokens,
        output_tokens=_count(usage.get("output_tokens", usage.get("completion_tokens"))),
        total_tokens=_count(usage.get("total_tokens")),
        model=model,
        kind=kind,
        request_id=str(payload.get("request_id") or payload.get("id") or "")[:180] or None,
        cached_tokens=cached,
    )
    result.attempts = [result.usage]
    return result


def _http_failure(response, model):
    """Expose status and known error codes, never echo request bodies or keys."""
    messages = {
        400: "通义拒绝了参数，请检查模型能力、图片格式与输出长度。",
        401: "通义 API Key 无效或已失效。",
        403: "此业务空间未开通模型，或 API Key 没有调用权限。",
        404: "当前模型在此业务空间不存在或不可访问。视觉模型可改选 qwen3.6-flash 或 qwen3-vl-plus；请核对北京地域与业务空间。",
        413: "请求过大，请减小文档分块或图片尺寸。",
        429: "达到限流或额度限制，请稍后重试并检查额度。",
    }
    body = {}
    try:
        value = response.json()
        if isinstance(value, dict): body = value
    except ValueError:
        pass
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    code = error.get("code")
    known = {
        "ModelNotFound", "model_not_found", "InvalidParameter", "InvalidParameter.ModelNotFound",
        "InvalidApiKey", "invalid_api_key", "AccessDenied", "AccessDenied.Unpurchased",
        "DataInspectionFailed", "Throttling", "AllocationQuota.FreeTierOnly",
        "InvalidParameter.UnsupportedModel", "BadRequest", "insufficient_quota",
    }
    suffix = f"，{code}" if isinstance(code, str) and code in known else ""
    message = f"{messages.get(response.status_code, '通义服务暂时不可用，请稍后重试。')}（HTTP {response.status_code}{suffix}）"
    attempt = {"model": model, "kind": "upstream_error", "input_tokens": None,
               "output_tokens": None, "total_tokens": None,
               "request_id": response.headers.get("x-request-id"), "status_code": response.status_code}
    try:
        attempt.update(_usage(body, model, "upstream_error", None).usage)
        attempt["request_id"] = attempt["request_id"] or response.headers.get("x-request-id")
    except ProviderError:
        pass
    return ProviderError("upstream_http_error", message, response.status_code, [attempt])


def normalize_endpoint(config: dict) -> tuple[str, str]:
    """Return compatible and native roots; prevent secret-bearing SSRF requests."""
    value = str(config.get("baseUrl") or config.get("base_url") or config.get("endpoint")
                or "https://dashscope.aliyuncs.com").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ProviderError("invalid_endpoint", "服务地址格式无效。") from None
    host = (parsed.hostname or "").lower()
    allowed = host == "dashscope.aliyuncs.com" or bool(
        re.fullmatch(r"[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", host)
    )
    if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
            or parsed.query or parsed.fragment or port not in (None, 443)):
        raise ProviderError("invalid_endpoint", "仅支持阿里云中国内地 HTTPS 地址（北京地域）。")
    if parsed.path.rstrip("/") not in ("", "/compatible-mode/v1", "/api/v1"):
        raise ProviderError("invalid_endpoint", "地址请填写服务域名或 /compatible-mode/v1 基础地址。")
    root = f"https://{host}"
    return f"{root}/compatible-mode/v1", f"{root}/api/v1"


def _run_sync(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    awaitable.close()
    raise RuntimeError("在异步流程中请使用 aembed_documents / aembed_query / ainvoke。")


class TongyiProvider:
    def __init__(self, config: dict, api_key: str, *, client: httpx.AsyncClient | None = None):
        self.base_url, self.native_url = normalize_endpoint(config)
        self.api_key = api_key.strip()
        self.config = dict(config)
        self._client = client
        self._active_stream_clients: set[httpx.AsyncClient] = set()

    async def aclose(self) -> None:
        # Non-stream clients are request-scoped. Explicitly closing live streams
        # also releases a canceled worker's upstream connection promptly.
        clients = list(self._active_stream_clients)
        self._active_stream_clients.clear()
        if clients:
            await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)

    async def _post(self, url: str, payload: dict) -> dict:
        if not self.api_key:
            raise ProviderError("missing_api_key", "请先为通义供应商配置 API Key。")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
                    response = await client.post(url, json=payload, headers=headers)
            if not 200 <= response.status_code < 300:
                raise _http_failure(response, payload.get("model", ""))
            body = response.json()
            if not isinstance(body, dict) or body.get("code") and not body.get("output"):
                raise ProviderError("invalid_response", "通义返回了无法识别的响应。")
            return body
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise ProviderError("upstream_timeout", "通义请求超时，任务未完成，请稍后重试。", usage=[{
                "model": payload.get("model", ""), "kind": "upstream_error", "input_tokens": None,
                "output_tokens": None, "total_tokens": None, "request_id": None,
            }]) from None
        except httpx.HTTPError:
            raise ProviderError("upstream_network_error", "无法连接通义服务，请检查网络与服务地址。", usage=[{
                "model": payload.get("model", ""), "kind": "upstream_error", "input_tokens": None,
                "output_tokens": None, "total_tokens": None, "request_id": None,
            }]) from None
        except (ValueError, TypeError):
            raise ProviderError("invalid_response", "通义返回了无法识别的响应。") from None

    async def embed(self, texts: list[str], model: str) -> ProviderResult:
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ProviderError("invalid_input", "Embedding 输入不能为空。")
        vectors: list[list[float]] = []
        usages: list[ProviderResult] = []
        # v3/v4 allow 10 rows; using 10 is conservative for newer embedding models.
        for offset in range(0, len(texts), 10):
            batch = texts[offset:offset + 10]
            try:
                body = await self._post(f"{self.base_url}/embeddings", {
                    "model": model, "input": batch, "encoding_format": "float",
                })
            except ProviderError as error:
                error.usage = [item.usage for item in usages] + error.usage
                error.partial_data = list(vectors)
                raise
            observed = _usage(body, model, "embedding", None)
            usages.append(observed)
            rows = body.get("data")
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ProviderError("invalid_response", "Embedding 返回条数与输入不一致。", usage=[item.usage for item in usages])
            try:
                ordered = sorted(rows, key=lambda item: int(item["index"]))
                if [item["index"] for item in ordered] != list(range(len(batch))):
                    raise ValueError()
                for row in ordered:
                    vector = row["embedding"]
                    if not isinstance(vector, list) or not vector or any(
                        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in vector
                    ):
                        raise ValueError()
                    vectors.append([float(value) for value in vector])
            except (KeyError, TypeError, ValueError):
                raise ProviderError("invalid_response", "Embedding 返回的向量格式无效。", usage=[item.usage for item in usages]) from None
        if len({len(vector) for vector in vectors}) != 1:
            raise ProviderError("invalid_response", "Embedding 返回的向量维度不一致。", usage=[item.usage for item in usages])
        def total(field):
            values = [getattr(item, field) for item in usages]
            return sum(values) if all(value is not None for value in values) else None
        return ProviderResult(vectors, total("input_tokens"), total("output_tokens"), total("total_tokens"), model,
                              "embedding", usages[-1].request_id, [item.usage for item in usages])

    async def rerank(self, query: str, documents: list[str], model: str) -> ProviderResult:
        if not documents:
            return ProviderResult([], model=model, kind="rerank")
        if not query.strip() or len(documents) > 500:
            raise ProviderError("invalid_input", "Rerank 需要非空问题，且候选片段不能超过 500 个。")
        body = await self._post(f"{self.native_url}/services/rerank/text-rerank/text-rerank", {
            "model": model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": len(documents), "return_documents": False},
        })
        observed = _usage(body, model, "rerank", None)
        rows = (body.get("output") or {}).get("results")
        if not isinstance(rows, list) or len(rows) != len(documents):
            raise ProviderError("invalid_response", "Rerank 返回条数与候选片段不一致。", usage=[observed.usage])
        results, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError("invalid_response", "Rerank 返回的排序结果无效。", usage=[observed.usage])
            index, score = row.get("index"), row.get("relevance_score")
            if (not isinstance(index, int) or isinstance(index, bool) or index in seen
                    or not 0 <= index < len(documents) or isinstance(score, bool)
                    or not isinstance(score, (int, float)) or not math.isfinite(score)):
                raise ProviderError("invalid_response", "Rerank 返回的排序结果无效。", usage=[observed.usage])
            seen.add(index)
            results.append({"index": index, "score": float(score)})
        return _usage(body, model, "rerank", sorted(results, key=lambda row: row["score"], reverse=True))

    async def _chat(self, messages: list[dict], model: str, generation: dict | None = None) -> ProviderResult:
        if MODEL_INFO.get(model, {}).get("streamOnly"):
            # OCR, connection checks and metadata use ainvoke; stream-only models
            # still receive SSE upstream and return their final answer + usage.
            parts, observed = [], None
            async for chunk in self._stream_chat(messages, model, generation):
                if chunk.data: parts.append(chunk.data)
                if chunk.attempts: observed = chunk
            if observed is None:
                raise ProviderError("incomplete_stream", "模型没有返回完整的流式结果。")
            observed.data = "".join(parts)
            return observed
        payload = self._chat_payload(messages, model, generation)
        body = await self._post(f"{self.base_url}/chat/completions", payload)
        observed = _usage(body, model, "llm", None)
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                raise ProviderError("truncated_response", "模型输出达到长度上限，请提高最大输出 Token 后重试。",
                                    usage=[observed.usage], partial_data=content)
            if isinstance(content, list):
                content = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            return _usage(body, model, "llm", content.strip())
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("empty_response", "通义没有返回可用文本，请检查模型能力与内容限制。", usage=[observed.usage]) from None

    @staticmethod
    def _chat_payload(messages: list[dict], model: str, generation: dict | None = None) -> dict:
        params = generation or {}
        info = MODEL_INFO.get(model, {})
        if info.get("disabledReason"):
            raise ProviderError("unsupported_protocol", info["disabledReason"])
        aliases = {"temperature": "temperature", "topP": "top_p", "top_p": "top_p",
                   "maxTokens": "max_tokens", "max_tokens": "max_tokens",
                   "presencePenalty": "presence_penalty", "presence_penalty": "presence_penalty",
                   "frequencyPenalty": "frequency_penalty", "frequency_penalty": "frequency_penalty"}
        payload = {"model": model, "messages": messages, "stream": False}
        for key, target in aliases.items():
            if key in params and params[key] is not None:
                payload[target] = params[key]
        # Direct answers keep bounded OCR/metadata calls usable on hybrid models.
        # Thinking-only models must never receive enable_thinking=False.
        if info.get("thinking") == "hybrid":
            payload["enable_thinking"] = params["enable_thinking"] if isinstance(params.get("enable_thinking"), bool) else False
        elif info.get("thinking") != "always" and isinstance(params.get("enable_thinking"), bool):
            payload["enable_thinking"] = params["enable_thinking"]
        if info.get("outputLimit") and payload.get("max_tokens", 0) > info["outputLimit"]:
            payload["max_tokens"] = info["outputLimit"]
        if info.get("textOnly"):
            payload["modalities"] = ["text"]
        # Metadata extraction opts into the provider's bounded JSON-object mode;
        # the application still validates the resulting schema and quotations.
        if params.get("response_format") == {"type": "json_object"} and info.get("thinking") != "always" and info.get("jsonMode", True):
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _stream_chat(self, messages: list[dict], model: str, generation: dict | None = None):
        if not self.api_key:
            raise ProviderError("missing_api_key", "请先为通义供应商配置 API Key。")
        payload = self._chat_payload(messages, model, generation)
        payload.update({"stream": True, "stream_options": {"include_usage": True}})
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        observed = ProviderResult("", model=model, kind="llm")
        partial, finish_reason = "", None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=False)
        if self._client is None:
            self._active_stream_clients.add(client)
        try:
            async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as response:
                if not 200 <= response.status_code < 300:
                    await response.aread()
                    raise _http_failure(response, model)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = line[5:].strip()
                    if event == "[DONE]":
                        break
                    body = json.loads(event)
                    if not isinstance(body, dict) or body.get("error"):
                        raise ProviderError("invalid_response", "通义返回了无效的流式响应。")
                    if body.get("usage"):
                        observed = _usage(body, model, "llm", "")
                    elif body.get("id"):
                        observed.request_id = str(body["id"])[:180]
                    for choice in body.get("choices") or []:
                        finish_reason = choice.get("finish_reason") or finish_reason
                        content = (choice.get("delta") or {}).get("content")
                        if content:
                            if not isinstance(content, str):
                                raise ProviderError("invalid_response", "通义返回了无效的流式文本。")
                            partial += content
                            yield ProviderResult(content, model=model, kind="llm", request_id=observed.request_id)
            if finish_reason == "length":
                raise ProviderError("truncated_response", "模型输出达到长度上限，请提高最大输出 Token 后重试。",
                                    usage=[observed.usage], partial_data=partial)
            if finish_reason not in {"stop", "tool_calls"}:
                raise ProviderError("incomplete_stream", "通义流式响应提前中断，请重试。", usage=[observed.usage], partial_data=partial)
            if not partial.strip():
                raise ProviderError("empty_response", "通义没有返回可用文本。", usage=[observed.usage])
            observed.attempts = [observed.usage]
            yield observed
        except ProviderError as error:
            if not error.usage:
                error.usage = [observed.usage]
            if error.partial_data is None:
                error.partial_data = partial
            raise
        except asyncio.CancelledError as error:
            error.usage = [observed.usage]
            error.partial_data = partial
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise ProviderError("upstream_stream_error", "通义流式连接中断或响应无效，请重试。",
                                usage=[observed.usage], partial_data=partial) from None
        finally:
            if self._client is None:
                self._active_stream_clients.discard(client)
                await client.aclose()

    async def agent_step(self, messages: list[dict], model: str, tools: list[dict], generation: dict | None = None) -> ProviderResult:
        """One tool-capable model call; callers validate and execute approved tools."""
        if MODEL_INFO.get(model,{}).get("streamOnly"):
            raise ProviderError("agent_model_stream_only", "该模型当前仅支持流式调用，请为 Agent 选择支持非流式工具调用的模型。")
        payload=self._chat_payload(messages,model,generation)
        payload["tools"]=tools
        body=await self._post(f"{self.base_url}/chat/completions",payload)
        observed=_usage(body,model,"llm",None)
        try:
            choice=body["choices"][0];message=choice["message"]
            content=message.get("content") or "";calls=message.get("tool_calls") or []
            if not isinstance(content,str) or not isinstance(calls,list) or len(calls)>8 or choice.get("finish_reason") not in {"stop","tool_calls"} or (not content.strip() and not calls):raise ValueError()
            sanitized=[]
            for call in calls:
                function=call.get("function",{})
                identifier=call.get("id");name=function.get("name");arguments=function.get("arguments")
                if call.get("type")!="function" or not isinstance(identifier,str) or not 1<=len(identifier)<=200 or not isinstance(name,str) or not 1<=len(name)<=100 or not isinstance(arguments,str) or len(arguments)>10000:raise ValueError()
                sanitized.append({"id":identifier,"type":"function","function":{"name":name,"arguments":arguments}})
            observed.data={"role":"assistant","content":content,"tool_calls":sanitized}
            return observed
        except (KeyError,IndexError,TypeError,ValueError,AttributeError):
            raise ProviderError("invalid_agent_response","模型返回的工具调用格式无效。",usage=[observed.usage]) from None

    async def chat(self, messages: list[dict], model: str, generation: dict | None = None) -> ProviderResult:
        """All conversational calls pass through the actual LangChain chat model."""
        # Enterprise document fragments must not be sent to an unrelated tracing
        # service because of a developer's shell-wide LangSmith environment.
        with tracing_context(enabled=False):
            message = await self.as_chat_model(model, generation).ainvoke(messages)
        return ProviderResult(
            message.content, model=model, kind="llm",
            input_tokens=message.response_metadata.get("observed_usage", {}).get("input_tokens"),
            output_tokens=message.response_metadata.get("observed_usage", {}).get("output_tokens"),
            total_tokens=message.response_metadata.get("observed_usage", {}).get("total_tokens"),
            request_id=message.response_metadata.get("request_id"),
            cached_tokens=message.response_metadata.get("observed_usage", {}).get("cached_tokens"),
            attempts=message.response_metadata.get("attempts", []),
        )

    async def stream_chat(self, messages: list[dict], model: str, generation: dict | None = None):
        """Yield text deltas, then an empty terminal result carrying actual usage."""
        with tracing_context(enabled=False):
            async for chunk in self.as_chat_model(model, generation).astream(messages):
                usage = chunk.response_metadata.get("observed_usage") or {}
                yield ProviderResult(
                    chunk.content, model=model, kind="llm" if chunk.response_metadata.get("attempts") else "llm_delta", input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"), total_tokens=usage.get("total_tokens"),
                    request_id=chunk.response_metadata.get("request_id"),
                    cached_tokens=usage.get("cached_tokens"),
                    attempts=chunk.response_metadata.get("attempts") or [],
                )

    async def recognize(self, image: bytes, mime: str, model: str, vision: bool = False) -> ProviderResult:
        if mime not in {"image/jpeg", "image/png", "image/webp"} or not image or len(image) >= 7 * 1024 * 1024:
            raise ProviderError("invalid_image", "云端识别支持小于 7 MB 的 PNG、JPEG 或 WEBP 图片。")
        info = MODEL_INFO.get(model, {})
        if info and not set(info["capabilities"]) & {"vision", "ocr"}:
            raise ProviderError("invalid_model_capability", "此模型不支持图片输入，请选择视觉理解或 OCR 模型。")
        prompt = ("请识别图片中的全部文字，并理解图表、流程图、结构图。按阅读顺序输出 Markdown；"
                  "表格保留表头与行列，流程图说明节点及连线方向。区分原文和图形解读，模糊处标记[无法识别]，不得补造。"
                  if vision else
                  "请按阅读顺序完整提取图片中的文字，输出 Markdown。表格保留表头与行列；模糊处标记[无法识别]，不得补造内容。")
        try:
            async with _cloud_semaphore():
                result = await self.chat([{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"}},
                    {"type": "text", "text": prompt},
                ]}], model, {"maxTokens": 8192})
        except ProviderError as error:
            for attempt in error.usage:
                attempt["kind"] = "vision" if vision else "ocr"
            raise
        result.kind = "vision" if vision else "ocr"
        for attempt in result.attempts:
            attempt["kind"] = result.kind
        return result

    def as_chat_model(self, model: str, generation: dict | None = None) -> TongyiChatModel:
        return TongyiChatModel(provider=self, model_id=model, generation=dict(generation or {}))

    def as_embeddings(self, model: str) -> TongyiEmbeddings:
        return TongyiEmbeddings(self, model)


class TongyiChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    provider: Any = Field(exclude=True, repr=False)
    model_id: str
    generation: dict = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "tongyi-mainland"

    @property
    def _identifying_params(self) -> dict:
        return {"model": self.model_id}

    async def _agenerate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        result = await self.provider._chat(convert_to_openai_messages(messages), self.model_id, self.generation)
        usage_metadata = None
        if all(value is not None for value in (result.input_tokens, result.output_tokens, result.total_tokens)):
            usage_metadata = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "total_tokens": result.total_tokens}
        message = AIMessage(content=result.data, usage_metadata=usage_metadata,
                            response_metadata={"observed_usage": result.usage, "request_id": result.request_id,
                                               "attempts": result.attempts})
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        return _run_sync(self._agenerate(messages, stop, run_manager, **kwargs))

    async def _astream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        async for result in self.provider._stream_chat(convert_to_openai_messages(messages), self.model_id, self.generation):
            metadata = {"request_id": result.request_id}
            usage_metadata = None
            if result.attempts:
                metadata.update({"observed_usage": result.usage, "attempts": result.attempts})
                if all(value is not None for value in (result.input_tokens, result.output_tokens, result.total_tokens)):
                    usage_metadata = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                                      "total_tokens": result.total_tokens}
            yield ChatGenerationChunk(message=AIMessageChunk(content=result.data, response_metadata=metadata,
                                                            usage_metadata=usage_metadata))


class TongyiEmbeddings(Embeddings):
    def __init__(self, provider: TongyiProvider, model: str):
        self.provider = provider
        self.model = model

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return (await self.provider.embed(texts, self.model)).data

    async def aembed_query(self, text: str) -> list[float]:
        return (await self.aembed_documents([text]))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _run_sync(self.aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return _run_sync(self.aembed_query(text))
