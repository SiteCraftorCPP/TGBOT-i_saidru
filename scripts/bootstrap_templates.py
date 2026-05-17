from app.core.config import get_settings
from app.services.catalog import TemplateCatalog


def main() -> None:
    settings = get_settings()
    catalog = TemplateCatalog(settings.templates_dir).load()
    print(f"Created/checked {len(catalog.all())} templates in {settings.templates_dir}")


if __name__ == "__main__":
    main()
