from aiogram.fsm.state import State, StatesGroup


class ConsultationStates(StatesGroup):
    waiting_problem = State()


class DocumentStates(StatesGroup):
    waiting_document_request = State()
    """Пошаговый сбор ответов на уточняющие вопросы (по одному сообщению)."""
    collecting_document_qa = State()
    waiting_document_details = State()
    confirming_generation = State()
