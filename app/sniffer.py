from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionRule:
    name: str
    marker: str
    length: int | None = None
    terminator: str | None = None

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Rule name must not be empty.")
        if not self.marker:
            raise ValueError("Search text must not be empty.")
        if (self.length is None) == (self.terminator is None):
            raise ValueError("Choose either an extraction length or a termination text.")
        if self.length is not None:
            if not isinstance(self.length, int) or isinstance(self.length, bool) or self.length < 1:
                raise ValueError("Extraction length must be a positive number of characters.")
        elif not isinstance(self.terminator, str) or not self.terminator:
            raise ValueError("Termination text must not be empty.")


def extract_values(data: bytes, rules: list[ExtractionRule]) -> list[dict]:
    text = data.decode("utf-8", errors="surrogateescape")
    results = []
    for rule in rules:
        start = text.find(rule.marker)
        if start < 0:
            results.append({"name": rule.name, "status": "not_found", "value": None})
            continue
        start += len(rule.marker)
        if rule.length is not None:
            end = start + rule.length
            incomplete = end > len(text)
        else:
            end = text.find(rule.terminator, start)
            incomplete = end < 0
        value = text[start:end] if not incomplete else ""
        if incomplete:
            status = "incomplete"
        elif any("\udc80" <= char <= "\udcff" for char in value):
            status = "invalid_utf8"
        else:
            status = "matched"
        results.append({"name": rule.name, "status": status, "value": value if status == "matched" else None})
    return results
