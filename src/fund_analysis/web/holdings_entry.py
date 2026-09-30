from __future__ import annotations

import base64
import io
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageOps, UnidentifiedImageError

from ..fund_data import FundSearchHit


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_COMPRESSED_BYTES = 2 * 1024 * 1024
MAX_IMAGE_COUNT = 5
IMAGE_LONG_EDGE = 1600
MONEY_QUANT = Decimal("0.01")
SHARES_QUANT = Decimal("0.0001")

ENTRY_OPTIONAL_FIELDS = (
    "shares",
    "cost",
    "holding_profit_rate",
    "daily_profit",
    "cumulative_profit",
    "amount_nav_date",
)
BLOCKING_ISSUES = {
    "code_missing",
    "name_missing",
    "amount_missing",
    "amount_conflict",
    "share_class_mismatch",
    "share_class_ambiguous",
    "skipped_non_fund",
}
_SHARE_CLASS_RE = re.compile(r"[（(]?\s*([ABCEH])\s*[）)]?$", re.IGNORECASE)


class HoldingsRecognitionError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class StructuredOcrConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 180.0
    max_tokens: int = 4096

    @classmethod
    def from_environ(cls) -> StructuredOcrConfig | None:
        base_url = os.environ.get("FUND_ANALYSIS_OCR_STRUCTURED_BASE_URL", "").strip()
        api_key = os.environ.get("FUND_ANALYSIS_OCR_STRUCTURED_API_KEY", "").strip()
        model = os.environ.get("FUND_ANALYSIS_OCR_STRUCTURED_MODEL", "").strip()
        if not all((base_url, api_key, model)):
            return None
        try:
            timeout = float(os.environ.get("FUND_ANALYSIS_OCR_STRUCTURED_TIMEOUT", "180"))
            max_tokens = int(os.environ.get("FUND_ANALYSIS_OCR_STRUCTURED_MAX_TOKENS", "4096"))
        except ValueError as exc:
            raise ValueError("持仓识图服务配置无效") from exc
        if timeout <= 0 or max_tokens <= 0:
            raise ValueError("持仓识图服务配置无效")
        return cls(base_url, api_key, model, timeout, max_tokens)


class StructuredHoldingsRecognizer:
    provider = "structured_vlm"

    def __init__(
        self,
        config: StructuredOcrConfig | None = None,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.config = config if config is not None else StructuredOcrConfig.from_environ()
        self.model = self.config.model if self.config else ""
        self._opener = opener

    @property
    def configured(self) -> bool:
        return self.config is not None

    def recognize_image(self, image: bytes, image_index: int) -> list[Mapping[str, Any]]:
        if self.config is None:
            raise HoldingsRecognitionError(503, "未配置持仓识图服务")
        endpoint = self.config.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        body = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "提取截图中的中国公募基金持仓。只返回 JSON，格式为 "
                                '{"funds":[{"code":null,"name":null,"amount":null,'
                                '"shares":null,"cost":null,"holding_profit_rate":null,'
                                '"daily_profit":null,"cumulative_profit":null}]}。'
                                "金额和份额使用十进制字符串；看不清的字段返回 null，不推算。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64,"
                                + base64.b64encode(image).decode("ascii")
                            },
                        },
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.config.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            raise HoldingsRecognitionError(504, "持仓识图超时") from exc
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            raise HoldingsRecognitionError(502, "持仓识图服务失败") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HoldingsRecognitionError(502, "持仓识图结果无法解析") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = _parse_model_content(content)
            funds = parsed.get("funds") if isinstance(parsed, Mapping) else parsed
            if not isinstance(funds, list):
                raise TypeError("funds is not a list")
            return [item for item in funds if isinstance(item, Mapping)]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise HoldingsRecognitionError(502, "持仓识图结果无法解析") from exc


def _parse_model_content(content: Any) -> Any:
    if isinstance(content, list):
        text = "".join(
            str(item.get("text") or "") for item in content if isinstance(item, Mapping)
        )
    else:
        text = str(content or "")
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    return json.loads(text)


def sniff_image_type(data: bytes) -> str:
    if len(data) >= 12 and data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and any(
        brand in data[8:32] for brand in (b"heic", b"heix", b"hevc", b"hevx", b"heif", b"mif1")
    ):
        raise HoldingsRecognitionError(415, "当前不支持 HEIC，请使用 JPG 或 PNG")
    raise HoldingsRecognitionError(415, "仅支持 JPG、PNG 或 WebP 截图")


def compress_image(data: bytes) -> bytes:
    sniff_image_type(data)
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((IMAGE_LONG_EDGE, IMAGE_LONG_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=80, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HoldingsRecognitionError(415, "仅支持 JPG、PNG 或 WebP 截图") from exc
    compressed = output.getvalue()
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise HoldingsRecognitionError(413, "截图压缩后仍然过大，请裁剪后重试")
    return compressed


def recognize_candidates(
    images: Sequence[bytes],
    recognizer: Any,
    fund_searcher: Callable[..., Sequence[Any]],
) -> dict[str, Any]:
    if not getattr(recognizer, "configured", True):
        raise HoldingsRecognitionError(503, "未配置持仓识图服务")
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[HoldingsRecognitionError] = []
    success_count = 0
    for index, image in enumerate(images):
        try:
            raw_candidates = recognizer.recognize_image(image, index)
            if not isinstance(raw_candidates, list):
                raise HoldingsRecognitionError(502, "持仓识图结果无法解析")
            success_count += 1
            candidates.extend(
                _normalize_candidate(raw, index, fund_searcher)
                for raw in raw_candidates
                if isinstance(raw, Mapping)
            )
        except HoldingsRecognitionError as exc:
            failures.append(exc)
            warnings.append(f"第 {index + 1} 张截图识别失败：{exc.detail}")
        except Exception:
            failures.append(HoldingsRecognitionError(502, "持仓识图服务失败"))
            warnings.append(f"第 {index + 1} 张截图识别失败：持仓识图服务失败")
    if success_count == 0:
        if failures and all(failure.status_code == 504 for failure in failures):
            raise HoldingsRecognitionError(504, "持仓识图超时")
        detail = (
            "持仓识图结果无法解析"
            if failures and all(failure.detail == "持仓识图结果无法解析" for failure in failures)
            else "持仓识图服务失败"
        )
        raise HoldingsRecognitionError(502, detail)
    merged, merge_warnings = _merge_candidates(candidates)
    warnings.extend(merge_warnings)
    if not merged:
        raise HoldingsRecognitionError(422, "未识别到基金持仓")
    return {
        "schema_version": "holdings_entry_recognize.v1",
        "provider": str(getattr(recognizer, "provider", "structured_vlm")),
        "model": str(getattr(recognizer, "model", "")),
        "image_count": len(images),
        "candidates": merged,
        "warnings": warnings,
    }


def _normalize_candidate(
    raw: Mapping[str, Any],
    image_index: int,
    fund_searcher: Callable[..., Sequence[Any]],
) -> dict[str, Any]:
    issues: list[str] = []
    raw_code = _text_or_none(raw.get("code"))
    digits = re.sub(r"\D", "", raw_code or "")
    code = digits if len(digits) == 6 and digits != "000000" else None
    ocr_name = _text_or_none(raw.get("name"))
    name = ocr_name
    amount = _candidate_decimal(raw.get("amount"), MONEY_QUANT)
    shares = _candidate_decimal(raw.get("shares"), SHARES_QUANT, trim=True)
    if raw_code and code is None:
        issues.append("skipped_non_fund")
    if name is None:
        issues.append("name_missing")
    if amount is None:
        issues.append("amount_missing")
    if raw.get("shares") not in (None, "") and shares is None:
        issues.append("shares_unclear")

    name_matches: list[dict[str, str]] = []
    verification = "unverified"
    inferred = False
    if code:
        try:
            hits = _search_hits(fund_searcher(code, limit=5))
            match = next((hit for hit in hits if hit["code"] == code), None)
            if match:
                official_name = match["name"]
                ocr_class = _share_class(ocr_name)
                official_class = _share_class(official_name)
                if ocr_class and official_class and ocr_class != official_class:
                    issues.append("share_class_mismatch")
                elif ocr_name and _normalized_name(ocr_name) != _normalized_name(official_name):
                    issues.append("name_mismatch")
                name = official_name
                verification = "matched"
            else:
                issues.append("not_found")
                verification = "not_found"
        except Exception:
            issues.append("unverified")
            verification = "unverified"
    elif name:
        try:
            name_matches = _search_hits(fund_searcher(name, limit=5))
            if len(name_matches) == 1:
                code = name_matches[0]["code"]
                name = name_matches[0]["name"]
                verification = "inferred"
                inferred = True
            elif len(name_matches) > 1:
                issues.append("share_class_ambiguous")
                verification = "unverified"
            else:
                issues.append("code_missing")
        except Exception:
            issues.extend(("code_missing", "unverified"))
    elif "skipped_non_fund" not in issues:
        issues.append("code_missing")

    issues = list(dict.fromkeys(issues))
    return {
        "source_image_index": image_index,
        "code": code,
        "ocr_name": ocr_name,
        "name": name,
        "share_class": _share_class(name),
        "amount": amount,
        "shares": shares,
        "cost": _candidate_decimal(raw.get("cost"), MONEY_QUANT),
        "holding_profit_rate": _candidate_decimal(
            raw.get("holding_profit_rate"), MONEY_QUANT, allow_percent=True
        ),
        "daily_profit": _candidate_decimal(raw.get("daily_profit"), MONEY_QUANT),
        "cumulative_profit": _candidate_decimal(
            raw.get("cumulative_profit"), MONEY_QUANT
        ),
        "issues": issues,
        "verification": verification,
        "confidence": "low" if issues or inferred else "high",
        "name_matches": name_matches,
    }


def _search_hits(raw_hits: Sequence[Any]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for hit in raw_hits:
        data: Mapping[str, Any]
        if isinstance(hit, FundSearchHit):
            data = hit.to_dict()
        elif isinstance(hit, Mapping):
            data = hit
        else:
            continue
        code = str(data.get("code") or "").strip()
        name = str(data.get("name") or "").strip()
        if re.fullmatch(r"\d{6}", code) and code != "000000" and name:
            hits.append({"code": code, "name": name})
    return hits


def _merge_candidates(
    candidates: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    merged: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for candidate in candidates:
        code = candidate.get("code")
        if not code or code not in by_code:
            merged.append(candidate)
            if code:
                by_code[code] = candidate
            continue
        current = by_code[code]
        if current.get("amount") != candidate.get("amount"):
            current["amount"] = None
            current["issues"] = list(
                dict.fromkeys([*current["issues"], "amount_conflict"])
            )
            warnings.append(f"基金代码 {code} 在多张截图中的金额不一致")
        if current.get("shares") != candidate.get("shares"):
            current["shares"] = None
            current["issues"] = list(
                dict.fromkeys([*current["issues"], "shares_conflict"])
            )
            warnings.append(f"基金代码 {code} 在多张截图中的份额不一致")
        current["confidence"] = "low" if current["issues"] else current["confidence"]
    return merged, list(dict.fromkeys(warnings))


def validate_entry_request(payload: Mapping[str, Any]) -> tuple[str, list[dict[str, str]]]:
    mode = str(payload.get("mode") or "").strip()
    if mode not in {"merge", "replace"}:
        raise ValueError("mode 只接受 merge 或 replace")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("请至少确认 1 条持仓")
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("确认列表格式无效")
        code = str(raw.get("code") or "").strip()
        if not re.fullmatch(r"\d{6}", code) or code == "000000":
            raise ValueError(f"基金代码 {code} 不是 6 位数字")
        normalized = code.casefold()
        if normalized in seen:
            raise ValueError(f"基金代码 {code} 在确认列表中重复")
        seen.add(normalized)
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"基金代码 {code} 缺少名称")
        amount = _strict_decimal(raw.get("amount"), MONEY_QUANT)
        if amount is None:
            raise ValueError(f"基金代码 {code} 缺少有效金额")
        if Decimal(amount) < 0:
            raise ValueError(f"基金代码 {code} 的金额不能为负")
        item = {"code": code, "name": name, "amount": amount}
        for field in ENTRY_OPTIONAL_FIELDS:
            value = raw.get(field)
            if value in (None, ""):
                continue
            if field == "amount_nav_date":
                date_text = str(value).strip()[:10]
                try:
                    item[field] = date_cls.fromisoformat(date_text).isoformat()
                except ValueError as exc:
                    raise ValueError(f"基金代码 {code} 的金额基准日期无效") from exc
                continue
            quant = SHARES_QUANT if field == "shares" else MONEY_QUANT
            normalized_value = _strict_decimal(
                value,
                quant,
                trim=field == "shares",
            )
            if normalized_value is None or (
                field in {"shares", "cost"} and Decimal(normalized_value) < 0
            ):
                label = "份额" if field == "shares" else "金额字段"
                raise ValueError(f"基金代码 {code} 的{label}无效")
            item[field] = normalized_value
        items.append(item)
    return mode, items


def build_entry_preview(
    items: Sequence[Mapping[str, str]],
    existing_holdings: Sequence[Mapping[str, Any]],
    mode: str,
) -> dict[str, Any]:
    existing = {
        str(row.get("code") or "").strip().casefold(): row for row in existing_holdings
    }
    rows: list[dict[str, Any]] = []
    counts = {"add": 0, "update": 0, "keep": 0, "remove": 0}
    confirmed_codes = {str(item["code"]).casefold() for item in items}
    for item in items:
        current = existing.get(str(item["code"]).casefold())
        action = "add" if current is None else ("update" if entry_differs(current, item) else "keep")
        counts[action] += 1
        rows.append({**{field: item.get(field) for field in ("code", "name", "amount", *ENTRY_OPTIONAL_FIELDS)}, "action": action, "error": None})
    if mode == "replace":
        for current in existing_holdings:
            code = str(current.get("code") or "").strip()
            if code.casefold() in confirmed_codes:
                continue
            counts["remove"] += 1
            rows.append(
                {
                    "code": code,
                    "name": str(current.get("name") or ""),
                    "amount": _existing_decimal(current.get("amount"), MONEY_QUANT),
                    "shares": _existing_decimal(current.get("shares"), SHARES_QUANT, trim=True),
                    "cost": _existing_decimal(current.get("cost"), MONEY_QUANT),
                    "holding_profit_rate": _existing_decimal(current.get("holding_profit_rate"), MONEY_QUANT),
                    "daily_profit": _existing_decimal(current.get("daily_profit"), MONEY_QUANT),
                    "cumulative_profit": _existing_decimal(current.get("cumulative_profit"), MONEY_QUANT),
                    "amount_nav_date": current.get("amount_nav_date"),
                    "action": "remove",
                    "error": None,
                }
            )
    return {
        "schema_version": "holdings_entry_preview.v1",
        "mode": mode,
        "rows": rows,
        "warnings": [],
        "valid_count": len(items),
        "error_count": 0,
        "add_count": counts["add"],
        "update_count": counts["update"],
        "keep_count": counts["keep"],
        "remove_count": counts["remove"],
    }


def entry_differs(existing: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    if str(existing.get("name") or "").strip() != str(item.get("name") or "").strip():
        return True
    if _existing_decimal(existing.get("amount"), MONEY_QUANT) != item.get("amount"):
        return True
    for field in ENTRY_OPTIONAL_FIELDS:
        if field not in item:
            continue
        if field == "amount_nav_date":
            existing_date = str(existing.get("amount_nav_date") or "").strip()[:10] or None
            if existing_date != item.get(field):
                return True
            continue
        quant = SHARES_QUANT if field == "shares" else MONEY_QUANT
        if _existing_decimal(existing.get(field), quant, trim=field == "shares") != item.get(field):
            return True
    return False


def _candidate_decimal(
    value: Any,
    quant: Decimal,
    *,
    trim: bool = False,
    allow_percent: bool = False,
) -> str | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    if allow_percent:
        text = text.removesuffix("%").strip()
    return _strict_decimal(text, quant, trim=trim)


def _strict_decimal(value: Any, quant: Decimal, *, trim: bool = False) -> str | None:
    if isinstance(value, (bool, float)) or value is None or value == "":
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    normalized = format(number.quantize(quant, rounding=ROUND_HALF_UP), "f")
    return normalized.rstrip("0").rstrip(".") if trim else normalized


def _existing_decimal(
    value: Any,
    quant: Decimal,
    *,
    trim: bool = False,
) -> str | None:
    if isinstance(value, float):
        value = str(value)
    return _strict_decimal(value, quant, trim=trim)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _share_class(name: str | None) -> str | None:
    match = _SHARE_CLASS_RE.search(name or "")
    return match.group(1).upper() if match else None


def _normalized_name(name: str) -> str:
    return re.sub(r"\s+", "", name).replace("（", "(").replace("）", ")").casefold()
