from app.schemas.ai import ConsultationResult, FillResult


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
