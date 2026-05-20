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

    @staticmethod
    def _normalize_text_scalar(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return "\n".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip()

    @field_validator("title", "subtitle", "date_and_signature", "instruction", mode="before")
    @classmethod
    def coerce_text_fields(cls, value: object) -> str:
        """Модель иногда кладёт одну строку в массив — иначе падает string_type."""
        return cls._normalize_text_scalar(value)

    @field_validator("header", "body", "requests", "attachments", mode="before")
    @classmethod
    def coerce_line_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            t = value.strip()
            return [t] if t else []
        return []


class DocumentReadinessResult(BaseModel):
    """Проверка: достаточно ли ответов для генерации черновика документа."""

    model_config = ConfigDict(extra="ignore")

    ready: bool = Field(description="True — можно переходить к генерации")
    reason_short: str = Field(
        default="",
        max_length=600,
        description="Кратко по-русски: чего не хватает или почему готово",
    )
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="1–4 узких уточняющих вопроса, если ready=false",
    )

    @field_validator("follow_up_questions", mode="before")
    @classmethod
    def coerce_questions(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if len(s) >= 5:
                out.append(s)
        return out[:4]

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
