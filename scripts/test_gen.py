import asyncio
from app.core.config import get_settings
from app.services.catalog import TemplateCatalog
from app.services.deepseek import DeepSeekClient
from app.services.documents import DocumentGenerator
from app.db.session import make_session_factory
from app.db.repositories import DocumentRepository, UserRepository
from app.db.models import DocumentStatus

async def main():
    settings = get_settings()
    catalog = TemplateCatalog(settings.templates_dir).load()
    deepseek = DeepSeekClient(settings)
    generator = DocumentGenerator(settings, catalog, deepseek)
    session_factory = make_session_factory(settings)
    
    async with session_factory() as session:
        user = await UserRepository(session).get_or_create(telegram_id=123, username="test")
        doc = await DocumentRepository(session).create(user_id=user.id, document_type="notice")
        await session.commit()
        doc_id = doc.id
        
    template = catalog.get("notice")
    answers = {
        "sender": "Test Sender",
        "recipient": "Test Recipient",
        "subject": "Test Subject",
        "message": "Test Message"
    }
    
    try:
        values, text, instr, docx, pdf = await generator.generate(
            document_id=doc_id,
            template=template,
            raw_answers=answers
        )
        print("Success")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
