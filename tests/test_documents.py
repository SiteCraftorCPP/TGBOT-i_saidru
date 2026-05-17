from app.services.catalog import TemplateCatalog
from app.services.documents import DocumentGenerator


def test_render_text_replaces_placeholders(tmp_path) -> None:
    catalog = TemplateCatalog(tmp_path).load()
    template = catalog.get("consumer_refund_claim")

    text = DocumentGenerator.render_text(
        template,
        {
            "fio": "Иванов Иван",
            "address": "Москва",
            "seller": "ООО Магазин",
            "product": "телефон",
            "amount": "10000",
            "purchase_date": "01.01.2026",
            "problem": "товар неисправен",
        },
    )

    assert "Иванов Иван" in text
    assert "{{ fio }}" not in text
