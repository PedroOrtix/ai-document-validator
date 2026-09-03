"""Input models and errors for the extraction layer."""

from io import BytesIO

from pydantic import BaseModel, ConfigDict, model_validator
from pypdf import PdfReader


class ExtractionError(ValueError):
    """Raised when input cannot be read or converted to text."""


class DocumentInput(BaseModel):
    """A document supplied as plain text or PDF bytes."""

    model_config = ConfigDict(frozen=True)

    text: str | None = None
    pdf_bytes: bytes | None = None
    filename: str | None = None

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "DocumentInput":
        provided = [value is not None for value in (self.text, self.pdf_bytes)]
        if not any(provided):
            raise ValueError("provide text or pdf_bytes")
        if all(provided):
            raise ValueError("provide only one of text or pdf_bytes")
        return self

    def to_text(self) -> str:
        """Return the supplied text or extract text from all PDF pages.

        A PDF without a text layer is a hard extraction failure. This avoids
        silently returning a document whose fields all appear to be missing.
        """
        if self.text is not None:
            return self.text
        if self.pdf_bytes is None:  # pragma: no cover - guarded by validation
            raise ExtractionError("document has no text or PDF bytes")

        try:
            reader = PdfReader(BytesIO(self.pdf_bytes))
            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as exc:
            raise ExtractionError("unable to read PDF") from exc
        if not text:
            raise ExtractionError("PDF has no extractable text layer")
        return text
