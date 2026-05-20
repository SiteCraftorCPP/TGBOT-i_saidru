from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def flatten_dynamic_document_lines(
    value: object,
    *,
    max_lines: int = 600,
    depth: int = 0,
) -> list[str]:
    """Разгладить то, что LLM вернула как массив/объект/строку, в список строк абзацев."""
    if depth > 16:
        return []
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, dict):
        keys = list(value.keys())
        numbered: list[tuple[int, object]] | None = []
        for k in keys:
            ks = str(k).strip()
            if ks.isdigit():
                numbered.append((int(ks), value[k]))
            else:
                numbered = None
                break
        lines: list[str] = []
        if numbered is not None:
            numbered.sort(key=lambda x: x[0])
            for _, v in numbered:
                lines.extend(flatten_dynamic_document_lines(v, max_lines=max_lines, depth=depth + 1))
        else:
            for v in value.values():
                lines.extend(flatten_dynamic_document_lines(v, max_lines=max_lines, depth=depth + 1))
        return lines[:max_lines]
    if isinstance(value, list):
        lines = []
        for item in value:
            lines.extend(flatten_dynamic_document_lines(item, max_lines=max_lines, depth=depth + 1))
            if len(lines) >= max_lines:
                break
        return lines[:max_lines]
    if isinstance(value, bool):
        return ["да"] if value else []
    return [str(value).strip()] if str(value).strip() else []


def coerce_dynamic_document_scalar(value: object) -> str:
    """Строковые поля документа: модель может отдать list/dict/scalar."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(flatten_dynamic_document_lines(value, max_lines=120))
    if isinstance(value, dict):
        return "\n".join(flatten_dynamic_document_lines(value, max_lines=120))
    return str(value).strip()


def normalize_dynamic_document_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Привести ответ completions к полям DynamicDocumentResult без падений типизации."""
    out = dict(data)
    for key in ("title", "subtitle", "date_and_signature", "instruction"):
        if key in out:
            out[key] = coerce_dynamic_document_scalar(out[key])
    for key in ("header", "body", "requests", "attachments"):
        if key in out:
            out[key] = flatten_dynamic_document_lines(out[key])
    return out


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

    @field_validator("document_title", mode="before")
    @classmethod
    def coerce_document_title(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            joined = "\n".join(str(v).strip() for v in value if str(v).strip())
            return joined.strip()[:420]
        return str(value).strip()[:420]

    @field_validator("questions", mode="before")
    @classmethod
    def coerce_questions_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            s = value.strip()
            return [s] if s else []
        if isinstance(value, list):
            out: list[str] = []
            for item in value:
                if isinstance(item, list):
                    sub = flatten_dynamic_document_lines(item, max_lines=40)
                    out.extend(sub)
                else:
                    t = str(item).strip()
                    if t:
                        out.append(t)
            return out
        return []

    @field_validator("extracted_facts_summary", mode="before")
    @classmethod
    def coerce_extracted_summary(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            joined = "\n".join(str(v).strip() for v in value if str(v).strip())
            return joined.strip()[:420]
        return str(value).strip()[:420]


class DynamicDocumentResult(BaseModel):
    """Структурированный черновик из LLM; типы полей часто «плывут» — нормализация в model_validator."""

    model_config = ConfigDict(extra="ignore")

    header: list[str] = Field(default_factory=list)
    title: str = ""
    subtitle: str = ""
    body: list[str] = Field(default_factory=list)
    requests: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    date_and_signature: str = ""
    instruction: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_shapes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return normalize_dynamic_document_payload(data)
        return data


class DocumentReadinessResult(BaseModel):
    """Проверка: достаточно ли ответов для генерации черновика документа."""

    model_config = ConfigDict(extra="ignore")

    ready: bool = Field(description="True — можно переходить к генерации")

    @field_validator("ready", mode="before")
    @classmethod
    def coerce_ready(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "да", "готово"}
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    reason_short: str = Field(
        default="",
        max_length=600,
        description="Кратко по-русски: чего не хватает или почему готово",
    )

    @field_validator("reason_short", mode="before")
    @classmethod
    def coerce_reason_short(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return "\n".join(str(v).strip() for v in value if str(v).strip())[:600]
        return str(value).strip()[:600]

    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="1–4 узких уточняющих вопроса, если ready=false",
    )

    @field_validator("follow_up_questions", mode="before")
    @classmethod
    def coerce_questions(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            s = value.strip()
            return [s] if len(s) >= 5 else []
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, list):
                for line in flatten_dynamic_document_lines(item, max_lines=20):
                    if len(line) >= 5:
                        out.append(line)
            else:
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
