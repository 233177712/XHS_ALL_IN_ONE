from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import requests

from backend.app.models import ModelConfig

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MEDIA_API_PREFIX = "/api/files/media/"
CODEX_CLI_PROVIDER = "codex-cli"


class TextAiClient(Protocol):
    def rewrite_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        instruction: str,
    ) -> str:
        ...

    def rewrite_note_with_images(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        instruction: str,
        image_urls: list[str],
    ) -> str:
        ...

    def generate_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        topic: str,
        reference: str,
        instruction: str,
    ) -> dict[str, str]:
        ...

    def generate_titles(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
    ) -> list[str]:
        ...

    def generate_tags(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
    ) -> list[str]:
        ...

    def polish_text(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        text: str,
        instruction: str,
    ) -> str:
        ...


class ImageAiClient(Protocol):
    def generate_cover(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
    ) -> dict[str, Any]:
        ...

    def generate_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        ...

    def describe_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        image_url: str,
        instruction: str,
    ) -> str:
        ...


def _candidate_response_encodings(response: requests.Response) -> list[str]:
    encodings: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", response.apparent_encoding, response.encoding):
        normalized = (encoding or "").strip()
        if normalized and normalized.lower() not in {item.lower() for item in encodings}:
            encodings.append(normalized)
    return encodings


def _load_json_response(response: requests.Response) -> Any:
    raw = response.content
    last_error: Exception | None = None
    for encoding in _candidate_response_encodings(response):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc

    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        last_error = exc

    raise ValueError("AI response is not valid JSON") from last_error


def _media_dir() -> Path:
    from backend.app.core.config import get_settings

    return Path(get_settings().storage_dir) / "media"


def _local_media_path_from_api_url(url: str) -> Path | None:
    if not url.startswith(MEDIA_API_PREFIX):
        return None
    file_name = url.removeprefix(MEDIA_API_PREFIX)
    local = _media_dir() / file_name
    return local if local.is_file() else None


def _api_media_url(file_name: str) -> str:
    return f"{MEDIA_API_PREFIX}{file_name}"


def _guess_download_suffix(url: str, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return suffix
    lowered = content_type.lower()
    if "jpeg" in lowered:
        return ".jpg"
    if "gif" in lowered:
        return ".gif"
    if "webp" in lowered:
        return ".webp"
    return ".png"


def _parse_size(value: str) -> tuple[int, int] | None:
    parts = value.lower().split("x")
    if len(parts) != 2:
        return None
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _resolve_image_ref(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    local = _local_media_path_from_api_url(url)
    if local is not None:
        import base64

        raw = local.read_bytes()
        ext = local.suffix.lower().lstrip(".")
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "image/png")
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    return url


class OpenAICompatibleTextClient:
    def _complete(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
    ) -> str:
        if not model_config.base_url:
            raise ValueError("Text model base_url is required")
        if not model_config.model_name:
            raise ValueError("Text model model_name is required")
        if not api_key:
            raise ValueError("Text model api_key is required")

        endpoint = f"{model_config.base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model_config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = _load_json_response(response)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI response missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI response content is empty")
        return content.strip()

    def _complete_with_images(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str],
        temperature: float = 0.7,
    ) -> str:
        if not model_config.base_url:
            raise ValueError("Text model base_url is required")
        if not model_config.model_name:
            raise ValueError("Text model model_name is required")
        if not api_key:
            raise ValueError("Text model api_key is required")

        resolved = [_resolve_image_ref(url) for url in image_urls if url]
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for ref in resolved:
            content_parts.append({"type": "image_url", "image_url": {"url": ref}})

        endpoint = f"{model_config.base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model_config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content_parts},
                ],
                "temperature": temperature,
                "max_tokens": 4096,
            },
            timeout=180,
        )
        response.raise_for_status()
        payload = _load_json_response(response)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI response missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI response content is empty")
        return content.strip()

    def rewrite_note_with_images(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        instruction: str,
        image_urls: list[str],
    ) -> str:
        return self._complete_with_images(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书内容运营编辑，负责在保留事实的前提下改写成自然、可发布的种草笔记。"
            "你会先阅读所有图片中的可见文字和关键信息，再结合正文进行改写。",
            user_prompt=(
                f"改写要求：{instruction or '提升表达、增强小红书语感'}\n\n"
                f"标题：{title}\n\n正文：\n{body}"
            ),
            image_urls=image_urls,
        )

    def rewrite_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        instruction: str,
    ) -> str:
        return self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书内容运营编辑，负责在保留事实的前提下改写成自然、可发布的种草笔记。",
            user_prompt=(
                f"改写要求：{instruction or '提升表达、增强小红书语感'}\n\n"
                f"标题：{title}\n\n正文：\n{body}"
            ),
        )

    def generate_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        topic: str,
        reference: str,
        instruction: str,
    ) -> dict[str, str]:
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书内容策划，输出可发布的标题和正文。",
            user_prompt=(
                "请生成一篇小红书笔记，格式必须是：\n标题：...\n正文：...\n\n"
                f"选题：{topic}\n参考材料：{reference or '无'}\n要求：{instruction or '自然、有信息密度'}"
            ),
        )
        title = topic
        body = content
        for line in content.splitlines():
            if line.startswith("标题："):
                title = line.replace("标题：", "", 1).strip() or title
                break
        if "正文：" in content:
            body = content.split("正文：", 1)[1].strip()
        return {"title": title, "body": body}

    def generate_titles(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
    ) -> list[str]:
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书标题优化专家。",
            user_prompt=f"请给出 {count} 个小红书标题，每行一个。\n原标题：{title}\n正文：{body}",
        )
        return [line.strip(" -0123456789.、") for line in content.splitlines() if line.strip()][:count]

    def generate_tags(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
    ) -> list[str]:
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书 SEO 和话题标签专家。",
            user_prompt=f"请给出 {count} 个小红书话题标签，只输出标签，用逗号或换行分隔。\n标题：{title}\n正文：{body}",
        )
        separators = content.replace("，", ",").replace("\n", ",").split(",")
        return [item.strip().lstrip("#") for item in separators if item.strip()][:count]

    def polish_text(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        text: str,
        instruction: str,
    ) -> str:
        return self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书正文润色编辑。",
            user_prompt=f"润色要求：{instruction or '更自然、清晰、有种草感'}\n\n原文：\n{text}",
        )


class OpenAICompatibleImageClient:
    def _validate(self, *, model_config: ModelConfig, api_key: str) -> None:
        if not model_config.base_url:
            raise ValueError("Image model base_url is required")
        if not model_config.model_name:
            raise ValueError("Image model model_name is required")
        if not api_key:
            raise ValueError("Image model api_key is required")

    def generate_cover(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
    ) -> dict[str, Any]:
        return self.generate_image(
            model_config=model_config,
            api_key=api_key,
            prompt=f"{prompt}\nStyle: {style or 'clean XHS cover'}\nSize: {size}",
        )

    def generate_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        self._validate(model_config=model_config, api_key=api_key)
        endpoint = f"{model_config.base_url.rstrip('/')}/images/generations"
        body: dict[str, Any] = {
            "model": model_config.model_name,
            "prompt": prompt,
            "response_format": "url",
        }
        if reference_images:
            resolved = [self._resolve_image_ref(url) for url in reference_images]
            if len(resolved) == 1:
                body["image"] = resolved[0]
            else:
                body["image"] = resolved
                body["sequential_image_generation"] = "disabled"
            body["watermark"] = False
        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                error_payload = _load_json_response(exc.response) if exc.response else {}
                detail = error_payload.get("error", {}).get("message", "") if isinstance(error_payload, dict) else ""
            except Exception:
                pass
            raise ValueError(f"图片生成失败: {detail or exc}") from exc
        payload = _load_json_response(response)
        try:
            item = payload["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Image response missing data[0]") from exc
        image_ref = item.get("url") or item.get("b64_json")
        if not isinstance(image_ref, str) or not image_ref:
            raise ValueError("Image response missing url or b64_json")
        return {"url": image_ref, "raw": payload}

    @staticmethod
    def _resolve_image_ref(url: str) -> str:
        return _resolve_image_ref(url)

    def describe_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        image_url: str,
        instruction: str,
    ) -> str:
        self._validate(model_config=model_config, api_key=api_key)
        endpoint = f"{model_config.base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model_config.model_name,
                "messages": [
                    {"role": "system", "content": "你是小红书图片分析助手。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction or "描述这张图片适合的小红书卖点。"},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = _load_json_response(response)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI response missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI image description is empty")
        return content.strip()


class CodexCliImageClient:
    def generate_cover(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
    ) -> dict[str, Any]:
        result = self.generate_image(
            model_config=model_config,
            api_key=api_key,
            prompt=f"{prompt}\nStyle: {style or 'clean XHS cover'}\nTarget size: {size}",
            reference_images=None,
        )
        dimensions = _parse_size(size)
        output_path = _local_media_path_from_api_url(str(result.get("url") or ""))
        if dimensions and output_path is not None:
            from backend.app.services.image_util import resize_image_file

            resized_path = output_path.with_name(f"{output_path.stem}-resized{output_path.suffix}")
            resize_image_file(
                source_path=output_path,
                output_path=resized_path,
                width=dimensions[0],
                height=dimensions[1],
                mode="cover",
                image_format="png",
                quality=90,
            )
            resized_path.replace(output_path)
        return result

    def generate_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        output_file_name = f"xhs-image-u{model_config.user_id}-{uuid4().hex}.png"
        output_path = _media_dir() / output_file_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            image_paths = [self._resolve_input_image(url, stack) for url in reference_images or []]
            command_prompt = self._image_generation_prompt(prompt, output_path, bool(image_paths))
            command_result = self._run_codex_exec(prompt=command_prompt, image_paths=image_paths)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ValueError("Codex CLI did not create the expected image file")
        return {
            "url": _api_media_url(output_file_name),
            "raw": {
                "provider": CODEX_CLI_PROVIDER,
                "stdout": command_result["stdout"],
                "stderr": command_result["stderr"],
                "reference_images_count": len(reference_images or []),
            },
        }

    def describe_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        image_url: str,
        instruction: str,
    ) -> str:
        with ExitStack() as stack:
            image_path = self._resolve_input_image(image_url, stack)
            with tempfile.NamedTemporaryFile(prefix="codex-image-desc-", suffix=".txt", delete=False) as handle:
                output_file = Path(handle.name)
            stack.callback(output_file.unlink, missing_ok=True)
            command_prompt = (
                "Read the attached image and respond in Chinese. "
                f"Task: {instruction or '描述这张图片适合的小红书卖点。'}\n"
                "Focus on the visible text, layout, tone, and standout visual details."
            )
            command_result = self._run_codex_exec(prompt=command_prompt, image_paths=[image_path], output_last_message=output_file)
            content = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
        final_text = content or command_result["stdout"]
        if not final_text.strip():
            raise ValueError("Codex CLI image description is empty")
        return final_text.strip()

    @staticmethod
    def _image_generation_prompt(prompt: str, output_path: Path, has_references: bool) -> str:
        relative_output = output_path.relative_to(PROJECT_ROOT).as_posix()
        lines = [
            f'$imagegen {prompt.strip()}',
            f'Save the final image exactly to "{relative_output}".',
            "Do not write any other files.",
        ]
        if has_references:
            lines.insert(1, "Use the attached image(s) as reference images for the result.")
        return "\n".join(lines)

    @staticmethod
    def _resolve_input_image(image_ref: str, stack: ExitStack) -> Path:
        local_media = _local_media_path_from_api_url(image_ref)
        if local_media is not None:
            return local_media
        direct_path = Path(image_ref)
        if direct_path.is_file():
            return direct_path
        if image_ref.startswith("http://") or image_ref.startswith("https://"):
            response = requests.get(image_ref, timeout=60)
            response.raise_for_status()
            suffix = _guess_download_suffix(image_ref, response.headers.get("Content-Type", ""))
            with tempfile.NamedTemporaryFile(prefix="codex-ref-", suffix=suffix, delete=False) as handle:
                temp_path = Path(handle.name)
            temp_path.write_bytes(response.content)
            stack.callback(temp_path.unlink, missing_ok=True)
            return temp_path
        raise ValueError(f"Unsupported reference image: {image_ref}")

    @staticmethod
    def _run_codex_exec(
        *,
        prompt: str,
        image_paths: list[Path],
        output_last_message: Path | None = None,
    ) -> dict[str, str]:
        executable = shutil.which("codex")
        if executable is None:
            raise ValueError("Codex CLI is not installed")
        command = [
            executable,
            "exec",
            "-C",
            str(PROJECT_ROOT),
            "-s",
            "workspace-write",
            "-a",
            "never",
        ]
        for image_path in image_paths:
            command.extend(["-i", str(image_path)])
        if output_last_message is not None:
            command.extend(["-o", str(output_last_message)])
        command.append(prompt)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ValueError(f"Codex CLI command failed: {detail[-400:] or result.returncode}")
        return {"stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


class ProviderAwareImageClient:
    def __init__(
        self,
        *,
        openai_client: ImageAiClient | None = None,
        codex_client: ImageAiClient | None = None,
    ) -> None:
        self._openai_client = openai_client or OpenAICompatibleImageClient()
        self._codex_client = codex_client or CodexCliImageClient()

    def _delegate(self, model_config: ModelConfig) -> ImageAiClient:
        provider = (model_config.provider or "openai-compatible").strip() or "openai-compatible"
        if provider == CODEX_CLI_PROVIDER:
            return self._codex_client
        if provider == "openai-compatible":
            return self._openai_client
        raise ValueError(f"Unsupported image model provider: {provider}")

    def generate_cover(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
    ) -> dict[str, Any]:
        return self._delegate(model_config).generate_cover(
            model_config=model_config,
            api_key=api_key,
            prompt=prompt,
            size=size,
            style=style,
        )

    def generate_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._delegate(model_config).generate_image(
            model_config=model_config,
            api_key=api_key,
            prompt=prompt,
            reference_images=reference_images,
        )

    def describe_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        image_url: str,
        instruction: str,
    ) -> str:
        return self._delegate(model_config).describe_image(
            model_config=model_config,
            api_key=api_key,
            image_url=image_url,
            instruction=instruction,
        )
