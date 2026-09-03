"""Extraction interfaces."""

from abc import ABC, abstractmethod

from docvalidator.domain.models import DocumentExtraction
from docvalidator.extraction.input import DocumentInput


class Extractor(ABC):
    """Abstract extractor interface."""

    @abstractmethod
    def extract(self, document: DocumentInput) -> DocumentExtraction:
        """Extract canonical fields from a document."""
        raise NotImplementedError
