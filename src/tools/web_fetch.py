import ipaddress
import re
import socket
from html.parser import HTMLParser
from logging import getLogger
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

logger = getLogger(__name__)

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 1_000_000
MAX_TEXT_CHARS = 6_000
REQUEST_TIMEOUT_SECONDS = 15
ALLOWED_PORTS = {None, 80, 443}
TEXT_CONTENT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml")
USER_AGENT = "meowgent/0.1 (+https://github.com/xsota/meowgent)"


class UnsafeURLError(ValueError):
  pass


def _is_public_ip(address: str) -> bool:
  try:
    return ipaddress.ip_address(address.split("%", 1)[0]).is_global
  except ValueError:
    return False


def validate_public_url(url: str) -> str:
  """Reject anything but plain http(s) URLs on default ports to non-literal-private hosts."""
  if not isinstance(url, str):
    raise UnsafeURLError("url must be a string.")
  parts = urlsplit(url.strip())
  if parts.scheme not in {"http", "https"}:
    raise UnsafeURLError("Only http and https URLs can be fetched.")
  if not parts.hostname or parts.username or parts.password:
    raise UnsafeURLError("The URL must contain a plain host name.")
  try:
    port = parts.port
  except ValueError as error:
    raise UnsafeURLError("The URL port is invalid.") from error
  if port not in ALLOWED_PORTS:
    raise UnsafeURLError("Only the default http and https ports can be fetched.")

  # aiohttp skips the resolver for IP literals, so they are checked here.
  try:
    ipaddress.ip_address(parts.hostname)
  except ValueError:
    return parts.geturl()
  if not _is_public_ip(parts.hostname):
    raise UnsafeURLError("The URL does not point to a public address.")
  return parts.geturl()


class PublicOnlyResolver(AbstractResolver):
  """Resolver that refuses hosts resolving to private, loopback or link-local addresses.

  Checking at connection time (rather than before the request) also covers
  redirects and DNS rebinding.
  """

  def __init__(self):
    self._resolver = DefaultResolver()

  async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET):
    results = await self._resolver.resolve(host, port, family)
    for result in results:
      if not _is_public_ip(result["host"]):
        raise UnsafeURLError("The URL does not point to a public address.")
    return results

  async def close(self) -> None:
    await self._resolver.close()


class _TextExtractor(HTMLParser):
  SKIPPED_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
  BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "ul", "ol",
  }

  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.parts: list[str] = []
    self.title_parts: list[str] = []
    self._skip_depth = 0
    self._in_title = False

  def handle_starttag(self, tag, attrs):
    if tag in self.SKIPPED_TAGS:
      self._skip_depth += 1
    elif tag == "title":
      self._in_title = True
    elif tag in self.BLOCK_TAGS:
      self.parts.append("\n")

  def handle_endtag(self, tag):
    if tag in self.SKIPPED_TAGS:
      self._skip_depth = max(0, self._skip_depth - 1)
    elif tag == "title":
      self._in_title = False
    elif tag in self.BLOCK_TAGS:
      self.parts.append("\n")

  def handle_data(self, data):
    if self._skip_depth:
      return
    if self._in_title:
      self.title_parts.append(data)
    else:
      self.parts.append(data)


def _collapse_whitespace(text: str) -> str:
  lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
  return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str | None, str]:
  extractor = _TextExtractor()
  extractor.feed(html)
  extractor.close()
  title = _collapse_whitespace("".join(extractor.title_parts)).replace("\n", " ") or None
  return title, _collapse_whitespace("".join(extractor.parts))


async def _read_limited(response: aiohttp.ClientResponse) -> tuple[bytes, bool]:
  body = bytearray()
  async for chunk in response.content.iter_chunked(64 * 1024):
    body.extend(chunk)
    if len(body) > MAX_RESPONSE_BYTES:
      return bytes(body[:MAX_RESPONSE_BYTES]), True
  return bytes(body), False


async def fetch_url(url: str, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
  """Fetch a public web page and return its readable text."""
  if not isinstance(max_chars, int) or isinstance(max_chars, bool):
    max_chars = MAX_TEXT_CHARS
  max_chars = min(max(max_chars, 200), MAX_TEXT_CHARS)

  try:
    current_url = validate_public_url(url)
    connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver(), use_dns_cache=False)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(
      connector=connector,
      timeout=timeout,
      headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1"},
    ) as session:
      for _ in range(MAX_REDIRECTS + 1):
        async with session.get(current_url, allow_redirects=False) as response:
          if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
              return {"error": "The server redirected without a destination.", "url": current_url}
            current_url = validate_public_url(urljoin(current_url, location))
            continue

          content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
          if content_type and not content_type.startswith(TEXT_CONTENT_TYPES):
            return {
              "error": f"Unsupported content type: {content_type}",
              "url": current_url,
              "status": response.status,
            }

          body, body_truncated = await _read_limited(response)
          text = body.decode(response.charset or "utf-8", errors="replace")
          title = None
          if "html" in content_type or (not content_type and "<html" in text[:2000].lower()):
            title, text = html_to_text(text)
          else:
            text = text.strip()

          return {
            "url": current_url,
            "status": response.status,
            "title": title,
            "text": text[:max_chars],
            "truncated": body_truncated or len(text) > max_chars,
          }
      return {"error": "Too many redirects.", "url": current_url}
  except UnsafeURLError as error:
    return {"error": str(error)}
  except LookupError:
    return {"error": "The page uses an unknown text encoding."}
  except (aiohttp.ClientError, TimeoutError) as error:
    cause = error.__cause__ or getattr(error, "os_error", None)
    if isinstance(cause, UnsafeURLError):
      return {"error": str(cause)}
    logger.warning("fetch_url failed: %s", type(error).__name__)
    return {"error": f"Could not fetch the URL ({type(error).__name__})."}
