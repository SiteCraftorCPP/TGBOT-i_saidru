from app.services.catalog import TemplateCatalog


def test_catalog_seeds_builtin_templates(tmp_path) -> None:
    catalog = TemplateCatalog(tmp_path).load()

    assert len(catalog.all()) >= 10
    assert catalog.find("consumer_refund_claim") is not None
    assert (tmp_path / "claims" / "consumer_refund_claim.docx").exists()
