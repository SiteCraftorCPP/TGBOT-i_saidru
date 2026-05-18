from app.schemas.ai import ConsultationResult, FillResult
from app.services.deepseek import DeepSeekClient
from app.core.config import Settings


def _ds() -> DeepSeekClient:
    return DeepSeekClient(Settings())


def test_consultation_schema_accepts_expected_json() -> None:
    result = ConsultationResult.model_validate(
        {
            "category": "защита прав потребителей",
            "consultation": "Продавец обязан рассмотреть претензию.",
            "risks": "Может потребоваться экспертиза.",
            "next_steps": "Направьте претензию.",
            "document_required": True,
            "recommended_document": "Претензия продавцу",
            "document_type": "consumer_refund_claim",
        }
    )

    assert result.document_required is True
    assert result.document_type == "consumer_refund_claim"


def test_fill_schema_accepts_missing_instruction() -> None:
    result = FillResult.model_validate({"values": {"fio": "Иванов Иван"}})
    assert result.instruction == ""


def test_fill_schema_accepts_values_and_instruction() -> None:
    result = FillResult.model_validate(
        {"values": {"fio": "Иванов Иван"}, "instruction": "Подайте документ адресату."}
    )

    assert result.values["fio"] == "Иванов Иван"


def test_filter_drops_litigation_smells_for_pure_contract() -> None:
    c = _ds()
    qs = [
        "Кто ответчик по делу?",
        "Укажите адрес и площадь арендуемого нежилого помещения.",
        "В какой суд подаём заявление?",
    ]
    out = c._filter_questions_by_request_intent("договор аренды нежилого помещения для офиса", qs)
    assert len(out) == 1
    assert "площадь" in out[0].lower()


def test_filter_keeps_litigation_when_user_requests_isk() -> None:
    c = _ds()
    qs = ["Кто ответчик и по какому адресу?", "Какие доказательства приложить?"]
    out = c._filter_questions_by_request_intent("нужно исковое заявление о взыскании долга", qs)
    assert len(out) == 2


def test_backfill_contract_adds_lease_checklist_when_thin() -> None:
    c = _ds()
    merged = c._backfill_contract_questions(
        "договор аренды квартиры на год",
        ["Стороны: как указать?"],
        sparse=False,
        max_questions=12,
    )
    assert len(merged) >= 6
    text = " ".join(merged).lower()
    assert "залог" in text or "аренд" in text
