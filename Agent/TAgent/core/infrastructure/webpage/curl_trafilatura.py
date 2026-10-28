# -*- coding: utf-8 -*-
"""网页抓取适配器：用本地 `curl` 拉取 HTML，再用 `trafilatura` 提取正文。

设计要点（遵循项目分层规范）：

    1. **第三方依赖只出现在本模块**：`subprocess`（curl）与 `trafilatura`
       都不外泄到服务层；核心服务面对的是 `WebPageFetcher` 抽象。
    2. **参数列表方式调用 curl，绝不 `shell=True`**：避免 shell 注入，
       也让路径含空格 / 特殊字符时行为可预测。
    3. **所有外部失败统一翻译成 `WebFetchError` 子类**：
       curl 不存在 / 启动失败 / 超时 / 非 2xx / 输出解码失败 /
       trafilatura 抛异常 / 提取结果为空，各有明确语义，模型据此答复用户。
"""

from __future__ import annotations

import shutil
import subprocess
import time

from ...domain.webpage import WebDocument
from ...errors import (
    WebFetchConnectionError,
    WebFetchEmptyError,
    WebFetchHTTPError,
    WebFetchParseError,
    WebFetchTimeoutError,
)
from ...ports.webpage import WebPageFetcher

#: curl 默认超时（秒），覆盖「连接 + 整体传输」两阶段
_CURL_TIMEOUT = 25
#: curl 连接超时（秒）
_CURL_CONNECT_TIMEOUT = 10
#: 单次抓取最大 HTML 字节数（防止异常巨大的页面拖垮解析），约 8 MiB
_MAX_HTML_BYTES = 8 * 1024 * 1024
#: 抓取失败时保留错误输出的最大长度（便于定位问题）
_MAX_ERROR_OUTPUT = 500
#: 正文必须达到的最小长度，否则视为「没提取出正文」
_MIN_BODY_CHARS = 40
#: 级联提取的「足够长」阈值：拿到这个长度就不必再放宽策略
_GOOD_ENOUGH_CHARS = 1000


class CurlTrafilaturaFetcher(WebPageFetcher):
    """`curl`（抓取）+ `trafilatura`（提取）的网页抓取器。

    Args:
        curl_path: curl 可执行文件路径；默认从 `PATH` 解析（Windows 自带
                   `C:\\Windows\\System32\\curl.exe`）。
        timeout: 抓取超时（秒）。
        max_html_bytes: 抓取 HTML 的最大字节数。
        min_body_chars: 正文最低字符数，低于此值视为提取失败。
    """

    def __init__(self, curl_path: str | None = None,
                 timeout: float = _CURL_TIMEOUT,
                 max_html_bytes: int = _MAX_HTML_BYTES,
                 min_body_chars: int = _MIN_BODY_CHARS) -> None:
        self._curl = curl_path or self._resolve_curl()
        self._timeout = timeout
        self._max_html_bytes = max_html_bytes
        self._min_body_chars = min_body_chars

    # ------------------------------------------------------------------
    def fetch(self, url: str) -> WebDocument:
        started = time.perf_counter()
        html, status, final_url = self._download(url)
        document = self._extract(html, url, final_url, status)
        # 在不可变对象之外补上耗时（frozen dataclass 用 replace 生成新实例）
        return _with_elapsed(document, started)

    # ------------------------------------------------------------------
    def _download(self, url: str) -> tuple[bytes, int, str]:
        """用 curl 拉取 HTML，返回 (原始字节, HTTP 状态码, 最终 URL)。"""
        cmd = [
            self._curl,
            "--silent",                # 不打印进度
            "--show-error",            # 但出错时把原因打到 stderr
            "--location",              # 跟随重定向
            "--max-redirs", "5",
            "--connect-timeout", str(_CURL_CONNECT_TIMEOUT),
            "--max-time", str(int(self._timeout)),
            "--max-filesize", str(self._max_html_bytes),
            "--user-agent", (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "--write-out", "\n%{http_code}\t%{url_effective}",
            url,
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self._timeout + 5,
            )
        except FileNotFoundError:
            raise WebFetchConnectionError(
                "找不到 curl 程序：%s。请确认已安装 curl 并加入 PATH。" % self._curl)
        except subprocess.TimeoutExpired:
            raise WebFetchTimeoutError(self._timeout, url)

        # curl 自身失败（DNS / 连接被拒 / 证书错误等）→ 非零退出码
        if proc.returncode != 0:
            detail = self._decode(proc.stderr).strip()
            raise WebFetchConnectionError(
                "curl 抓取失败（退出码 %d）%s"
                % (proc.returncode, "：" + detail[: _MAX_ERROR_OUTPUT] if detail else ""))

        raw = proc.stdout
        if raw.endswith(b"\n"):
            raw = raw[:-1]  # 去掉 write-out 前缀的换行（见下）
        html, status, final_url = self._split_trailer(raw)
        return html, status, final_url

    def _split_trailer(self, raw: bytes) -> tuple[bytes, int, str]:
        """把「HTML + 状态码 + 最终 URL」的合并输出拆开。

        `--write-out` 的输出以换行开头，因此以**最后一个换行**为界：
        前面是 HTML（HTML 内部即使含换行也不影响，因为取的是最后一段）。
        """
        marker = raw.rfind(b"\n")
        if marker < 0:
            # 没有 trailer（理论上不会发生），按纯 HTML 处理
            return raw, 0, ""
        html = raw[:marker]
        trailer = raw[marker + 1:].decode("utf-8", "replace").strip()
        status = 0
        final_url = ""
        if "\t" in trailer:
            status_part, final_url = trailer.split("\t", 1)
            try:
                status = int(status_part)
            except ValueError:
                status = 0
        return html, status, final_url

    def _extract(self, html: bytes, url: str, final_url: str, status: int) -> WebDocument:
        """把 HTML 提取成正文 Markdown，并对各类失败给出明确异常。"""
        if status >= 400:
            raise WebFetchHTTPError(status, final_url or url)

        text = self._decode(html)
        if not text.strip():
            raise WebFetchEmptyError(url, "返回内容为空")

        try:
            import trafilatura  # 延迟导入：没有抓取任务时不让启动变慢
        except ImportError:
            raise WebFetchParseError(
                "缺少 trafilatura 依赖，请安装：pip install trafilatura")

        title = self._extract_title(trafilatura, text)
        markdown = self._extract_markdown(trafilatura, text)
        if not markdown:
            raise WebFetchEmptyError(url, "解析后无正文")
        if len(markdown) < self._min_body_chars:
            raise WebFetchEmptyError(url, "正文过短（%d 字符）" % len(markdown))

        return WebDocument(
            url=url,
            final_url=final_url or url,
            status_code=status,
            title=title,
            markdown=markdown,
            html_bytes=len(html),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_title(trafilatura, text: str) -> str:
        """提取页面标题；失败不影响正文提取。"""
        try:
            from trafilatura.metadata import extract_metadata

            metadata = extract_metadata(text)
            return (getattr(metadata, "title", "") or "").strip() if metadata else ""
        except Exception:  # 标题只是锦上添花
            return ""

    def _extract_markdown(self, trafilatura, text: str) -> str:
        """多策略级联提取，取**最长**的结果。

        为什么不能只用默认参数：trafilatura 默认偏「精度优先」，
        在国内高校 / 政企 CMS 的 **table 布局 + 图片正文** 页面上
        （实测 nsu.edu.cn 的新闻页）会把整篇正文判成 None；
        而 `favor_recall=True` 能完整提取。反过来，在标准新闻/博客页上
        默认参数更干净。因此从精确到宽松逐级尝试，谁提取得多用谁。

        单次策略抛异常只跳过该策略；全部失败才上抛 `WebFetchParseError`。
        """
        base = dict(
            output_format="markdown",
            include_tables=True,      # 保留表格
            include_links=True,       # 保留链接（转成 Markdown 链接）
            include_formatting=True,  # 保留加粗 / 斜体
            deduplicate=True,         # 去除重复段落
        )
        # 从「精度优先」到「召回优先」逐级放宽
        strategies: tuple[dict, ...] = (
            {},
            {"favor_recall": True},
            {"favor_recall": True, "include_comments": True},
        )

        best = ""
        errors: list[str] = []
        for extra in strategies:
            try:
                result = trafilatura.extract(text, **base, **extra) or ""
            except Exception as exc:  # 单个策略失败不拖垮整体
                errors.append("%s: %s" % (extra or "default", exc))
                continue
            result = result.strip()
            if len(result) > len(best):
                best = result
            if len(best) >= _GOOD_ENOUGH_CHARS:
                break  # 已经拿到足够长的正文，没必要再放宽

        if not best and errors:
            raise WebFetchParseError("网页 HTML 解析失败：%s" % errors[0])
        return best

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_curl() -> str:
        resolved = shutil.which("curl")
        if resolved:
            return resolved
        # Windows 常见默认路径兜底（which 在部分环境对系统目录不生效）
        import os
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                 "System32", "curl.exe")
        return candidate if os.path.isfile(candidate) else "curl"

    @staticmethod
    def _decode(data: bytes) -> str:
        """把 HTML 字节按 utf-8 → gb18030 → latin-1 顺序解码。"""
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, ValueError):
                continue
        return data.decode("utf-8", "replace")  # pragma: no cover - 兜底


def _with_elapsed(document: WebDocument, started: float) -> WebDocument:
    """生成带耗时字段的新文档实例（保持 frozen dataclass 的不可变语义）。"""
    from dataclasses import replace

    return replace(document, elapsed_ms=int((time.perf_counter() - started) * 1000))


__all__ = ["CurlTrafilaturaFetcher"]
