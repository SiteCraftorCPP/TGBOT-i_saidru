from app.schemas.ai import ConsultationResult, FillResult
from app.services.deepseek import DeepSeekClient, _parse_json_object_content
from app.core.config import Settings


def _ds() -> DeepSeekClient:
    return DeepSeekClient(Settings())


def test_parse_json_object_content_accepts_fence_and_surrounding_text() -> None:
    assert _parse_json_object_content('```json\n{"document_title":"x","questions":[],"clarification_needed":false,"extracted_facts_summary":""}\n```')[
        "document_title"
    ] == "x"
    wrapped = 'Ответ:\n{"a": true}\nхвост'
    assert _parse_json_object_content(wrapped)["a"] is True


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


def test_ordered_api_keys_none_keeps_config_order() -> None:
    keys = ["a", "b", "c"]
    assert DeepSeekClient._ordered_api_keys(keys, None) == keys


def test_ordered_api_keys_stable_per_user() -> None:
    keys = ["k0", "k1", "k2", "k3"]
    uid = 77100988773
    a = DeepSeekClient._ordered_api_keys(keys, uid)
    b = DeepSeekClient._ordered_api_keys(keys, uid)
    assert a == b
    assert set(a) == set(keys)


def test_ordered_api_keys_is_permutation_for_various_ids() -> None:
    keys = ["k0", "k1", "k2"]
    for uid in (1, 2, 999_999_999_991):
        o = DeepSeekClient._ordered_api_keys(keys, uid)
        assert sorted(o) == sorted(keys)


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


def test_dynamic_document_normalizes_nested_and_dict_shapes() -> None:
    from app.schemas.ai import DynamicDocumentResult

    raw = DynamicDocumentResult.model_validate(
        {
            "title": {"ru": "Тест"},
            "body": [["абзац в массиве"], {"1": "первый", "2": "второй"}],
            "date_and_signature": [{"строка": "Дата ______"}],
            "header": {"0": "Верх"},
        }
    )
    assert isinstance(raw.title, str)
    assert len(raw.body) >= 2
    assert isinstance(raw.date_and_signature, str)
    assert len(raw.header) >= 1


def test_dynamic_document_result_coerces_list_to_string_fields() -> None:
    from app.schemas.ai import DynamicDocumentResult

    raw = DynamicDocumentResult.model_validate(
        {
            "title": "Тест",
            "date_and_signature": ["Дата: ________", "________________ (подпись)"],
            "body": "один абзац",
        }
    )
    assert "______" in raw.date_and_signature
    assert "\n" in raw.date_and_signature
    assert raw.body == ["один абзац"]


def test_infer_dynamic_doc_kind_lease_is_contract() -> None:
    c = _ds()
    k = c._infer_dynamic_document_kind(
        "нужен договор аренды нежилого помещения",
        "площадь 50 кв м, арендатор иванов, срок 11 месяцев",
    )
    assert k == "contract"


def test_infer_dynamic_doc_kind_claim_is_litigation() -> None:
    c = _ds()
    k = c._infer_dynamic_document_kind(
        "подать иск о взыскании долга по расписке",
        "ответчик петров, сумма 100000",
    )
    assert k == "litigation"


def test_sanitize_contract_strips_requests_and_bad_title() -> None:
    from app.schemas.ai import DynamicDocumentResult

    c = _ds()
    raw = DynamicDocumentResult(
        title="ИСКОВОЕ ЗАЯВЛЕНИЕ",
        subtitle="о взыскании",
        header=["В суд", "Истец:"],
        body=["текст"],
        requests=["1. Взыскать"],
        attachments=[],
        date_and_signature="",
        instruction="",
    )
    fixed = c._sanitize_dynamic_document_output(
        "contract",
        raw,
        "договор аренды офиса",
    )
    assert fixed.requests == []
    assert "АРЕНД" in (fixed.title or "").upper() or fixed.title == "ДОГОВОР АРЕНДЫ"
