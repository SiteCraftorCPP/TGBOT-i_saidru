from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConsultationResult(BaseModel):
    category: str
    consultation: str
    risks: str
    next_steps: str
    document_required: bool
    recommended_document: str | None = None
    document_type: str | None = None

    @field_validator("category", "consultation", "risks", "next_steps", mode="before")
    @classmethod
    def coerce_strings(cls, value: object) -> str:
        if isinstance(value, list):
            return "\n".join(str(v) for v in value)
        if value is None:
            return ""
        return str(value).strip()


class FieldAnswer(BaseModel):
    key: str
    label: str
    value: str


class TemplateField(BaseModel):
    key: str
    label: str
    question: str
    required: bool = True


class TemplateMeta(BaseModel):
    document_type: str
    title: str
    category: str
    filename: str
    fields: list[TemplateField]
    body: list[str]
    instruction: str

    @property
    def placeholders(self) -> set[str]:
        return {field.key for field in self.fields}


class DocumentQuestionsResult(BaseModel):
    """Ответ модели на свободный запрос документа: название и уточняющие вопросы."""

    model_config = ConfigDict(extra="ignore")

    document_title: str
    questions: list[str]
    clarification_needed: bool = Field(
        default=False,
        description="True — недостаточно контекста, нужна только серия уточняющих вопросов без узкой темы",
    )

    @field_validator("clarification_needed", mode="before")
    @classmethod
    def coerce_clarification_flag(cls, value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "да"}
        return bool(value)

    extracted_facts_summary: str = Field(
        default="",
        max_length=420,
        description="1–2 предложения: только факты, явно написанные пользователем; иначе пустая строка",
    )

    @field_validator("extracted_facts_summary", mode="before")
    @classmethod
    def coerce_extracted_summary(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()[:420]


class DynamicDocumentResult(BaseModel):
    header: list[str] = Field(default_factory=list)
    title: str = ""
    subtitle: str = ""
    body: list[str] = Field(default_factory=list)
    requests: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    date_and_signature: str = ""
    instruction: str = ""

class FillResult(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    instruction: str = ""

    @field_validator("values", mode="before")
    @classmethod
    def coerce_values(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("values must be an object")
        return value  # type: ignore[return-value]

    @field_validator("instruction", mode="before")
    @classmethod
    def coerce_instruction(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()
