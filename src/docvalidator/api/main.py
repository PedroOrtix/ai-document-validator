"""HTTP API for document validation."""

import base64
import binascii
import os
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from starlette.datastructures import UploadFile as StarletteUploadFile

from docvalidator.api.logging_setup import configure_logging
from docvalidator.domain.models import DocumentExtraction, ValidationConfig, Verdict
from docvalidator.extraction import DocumentInput, ExtractionError, OfflineExtractor
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.llm import (
    LLMConfigurationError,
    LLMParsingError,
    LLMRequestError,
    LLMTimeoutError,
)
from docvalidator.extraction.ocr import OcrExtractor
from docvalidator.rules_engine import RulesEngine

logger = configure_logging()


class ValidateResponse(Verdict):
    """Validation response enriched with the request identifier."""

    request_id: str


class JsonValidateRequest(BaseModel):
    """JSON request body accepted by the validation and extraction endpoints."""

    model_config = ConfigDict(extra="forbid")

    content_b64: str | None = None
    text: str | None = None
    filename: str | None = None
    config: ValidationConfig = ValidationConfig()
    extraction_backend: Literal["auto", "offline", "llm", "vlm", "ocr"] | None = None

    @model_validator(mode="after")
    def validate_exactly_one_content_source(self) -> "JsonValidateRequest":
        provided = [self.content_b64 is not None, self.text is not None]
        if not any(provided):
            raise ValueError("provide content_b64 or text")
        if all(provided):
            raise ValueError("provide only one of content_b64 or text")
        return self


class APIError(Exception):
    """An error that is safe to expose to API clients."""

    def __init__(
        self, code: str, message: str, details: Any | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ParsedRequest:
    """Normalized data from either supported request representation."""

    def __init__(
        self,
        document: DocumentInput,
        config: ValidationConfig,
        extraction_backend: str | None,
    ) -> None:
        self.document = document
        self.config = config
        self.extraction_backend = extraction_backend


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Any | None = None,
) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "request_id": request_id},
    )


def _default_backend() -> str:
    return "auto" if os.environ.get("OPENROUTER_API_KEY") else "offline"


def _validation_error(
    exc: Exception, request_id: str
) -> JSONResponse:
    if isinstance(exc, ValidationError):
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return _error_response(
            422, "validation_error", "request validation failed", request_id, details
        )
    if isinstance(exc, APIError):
        return _error_response(422, exc.code, exc.message, request_id)
    return _error_response(422, "invalid_document", str(exc), request_id)


app = FastAPI(title="Document Validator API", version="0.1.0")


@app.middleware("http")
async def request_logging_middleware(
    request: Request, call_next
) -> JSONResponse:
    """Assign a request ID, echo it in headers, and log one structured line."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.backend = None
    request.state.verdict_status = None
    started_at = time.perf_counter()

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    latency_ms = (time.perf_counter() - started_at) * 1000

    logger.info(
        "request completed",
        extra={
            "log_data": {
                "request_id": request_id,
                "route": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "latency_ms": round(latency_ms, 2),
                "backend": request.state.backend,
                "verdict_status": request.state.verdict_status,
            }
        },
    )
    return response


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Convert known API errors to structured responses."""
    if exc.code == "unsupported_backend":
        return _error_response(
            501, exc.code, exc.message, request.state.request_id
        )
    return _error_response(
        422, exc.code, exc.message, request.state.request_id, exc.details
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert FastAPI request validation failures to structured responses."""
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return _error_response(
        422, "validation_error", "request validation failed", request.state.request_id, details
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_error_handler(
    request: Request, exc: ValidationError
) -> JSONResponse:
    """Convert nested Pydantic validation failures to structured responses."""
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return _error_response(
        422, "validation_error", "request validation failed", request.state.request_id, details
    )


@app.exception_handler(ExtractionError)
async def extraction_error_handler(
    request: Request, exc: ExtractionError
) -> JSONResponse:
    """Convert extraction failures to backend-specific structured responses."""
    if isinstance(exc, LLMConfigurationError):
        return _error_response(
            503,
            "llm_configuration_error",
            str(exc),
            request.state.request_id,
            {"hint": "configure OPENROUTER_API_KEY or use the offline backend"},
        )
    if isinstance(exc, (LLMParsingError, LLMRequestError)):
        return _error_response(502, "llm_response_error", str(exc), request.state.request_id)
    if isinstance(exc, LLMTimeoutError):
        return _error_response(504, "llm_timeout", str(exc), request.state.request_id)
    return _validation_error(exc, request.state.request_id)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert unexpected errors to a generic 500 response."""
    logger.exception("unexpected error", extra={"request_id": request.state.request_id})
    return _error_response(
        500, "internal_error", "unexpected internal error", request.state.request_id
    )


async def _parse_request(request: Request) -> ParsedRequest:
    """Normalize JSON or multipart input into a document and configuration."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            raw_body = await request.json()
        except Exception as exc:
            raise APIError("invalid_json", "request body is not valid JSON") from exc
        body = JsonValidateRequest.model_validate(raw_body)
        document = _document_from_json(body)
        return ParsedRequest(document, body.config, body.extraction_backend)

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        config_value = form.get("config")
        is_upload = isinstance(upload, (UploadFile, StarletteUploadFile)) or hasattr(
            upload, "read"
        )
        if not is_upload:
            raise APIError("invalid_request", "file is required")
        document = _document_from_multipart(upload.filename or "", await upload.read())
        if config_value is None:
            config = ValidationConfig()
        elif isinstance(config_value, str):
            config = ValidationConfig.model_validate_json(config_value)
        else:
            raise APIError("invalid_request", "config must be a JSON string")
        return ParsedRequest(document, config, None)

    raise APIError("invalid_request", "unsupported content type")


@app.get("/health")
async def health() -> dict[str, str]:
    """Return the service liveness status."""
    return {"status": "ok"}


def _document_from_json(body: JsonValidateRequest) -> DocumentInput:
    if body.content_b64 is not None:
        try:
            decoded = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise APIError("invalid_base64", "content_b64 is not valid base64") from exc
        if body.filename and body.filename.lower().endswith(".txt"):
            try:
                return DocumentInput(text=decoded.decode("utf-8"), filename=body.filename)
            except UnicodeDecodeError as exc:
                raise ExtractionError("unable to decode text document") from exc
        return DocumentInput(pdf_bytes=decoded, filename=body.filename)
    return DocumentInput(text=body.text, filename=body.filename)


def _document_from_multipart(
    filename: str, content: bytes
) -> DocumentInput:
    lower_filename = filename.lower()
    if lower_filename.endswith(".pdf"):
        return DocumentInput(pdf_bytes=content, filename=filename)
    if lower_filename.endswith(".txt"):
        try:
            return DocumentInput(text=content.decode("utf-8"), filename=filename)
        except UnicodeDecodeError as exc:
            raise ExtractionError("unable to decode text document") from exc
    raise APIError("unsupported_file_type", "file must be a PDF or .txt document")


def _run_pipeline(
    document: DocumentInput,
    config: ValidationConfig,
    backend: str,
) -> Verdict:
    extraction = _extract(document, backend)
    verdict = RulesEngine().evaluate(extraction, config)
    return verdict


def _select_backend(requested: str | None) -> str:
    backend = requested or _default_backend()
    if backend not in {"auto", "offline", "llm", "vlm", "ocr"}:
        raise APIError("unsupported_backend", f"unknown extraction backend: {backend}")
    return backend


def _llm_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def _build_extractor(backend: str) -> Extractor:
    if backend == "auto":
        from docvalidator.extraction.routing import AutoExtractor
        from docvalidator.settings import LLMSettings

        return AutoExtractor(LLMSettings(openrouter_api_key=_llm_api_key()))
    if backend == "llm":
        from docvalidator.extraction.llm import LLMExtractor
        from docvalidator.settings import LLMSettings

        return LLMExtractor(LLMSettings(openrouter_api_key=_llm_api_key()))
    if backend == "vlm":
        from docvalidator.extraction.vision import VisionExtractor
        from docvalidator.settings import LLMSettings

        return VisionExtractor(LLMSettings(openrouter_api_key=_llm_api_key()))
    if backend == "ocr":
        return OcrExtractor()
    return OfflineExtractor()


def _extract(document: DocumentInput, backend: str) -> DocumentExtraction:
    """Extract fields with the selected backend without runtime degradation."""
    return _build_extractor(backend).extract(document)


@app.post(
    "/v1/extract",
    response_model=DocumentExtraction,
)
async def extract(request: Request) -> DocumentExtraction:
    """Extract canonical fields from a document."""
    parsed = await _parse_request(request)
    backend = _select_backend(parsed.extraction_backend)
    request.state.backend = backend
    return _extract(parsed.document, backend)


@app.post(
    "/v1/validate",
    response_model=ValidateResponse,
)
async def validate(request: Request) -> ValidateResponse:
    """Validate a document and return the aggregate verdict."""
    parsed = await _parse_request(request)
    backend = _select_backend(parsed.extraction_backend)
    request.state.backend = backend
    verdict = _run_pipeline(
        parsed.document, parsed.config, backend
    )
    request.state.verdict_status = verdict.status
    return ValidateResponse(**verdict.model_dump(), request_id=request.state.request_id)
