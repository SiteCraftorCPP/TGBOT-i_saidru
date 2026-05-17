from aiogram.fsm.state import State, StatesGroup


class ConsultationStates(StatesGroup):
    waiting_problem = State()


class DocumentStates(StatesGroup):
    waiting_document_request = State()
    waiting_document_details = State()
    confirming_generation = State()
