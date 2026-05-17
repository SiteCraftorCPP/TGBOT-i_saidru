import json
import re
from collections.abc import Sequence
from itertools import cycle
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.constants import SUPPORTED_LAW_AREAS
from app.schemas.ai import ConsultationResult, FillResult, TemplateMeta, DocumentQuestionsResult, DynamicDocumentResult


class DeepSeekError(RuntimeError):
    pass


# Служебные слова юридических заголовков (не считаются «предметом», упомянутым пользователем).
_DOCUMENT_TITLE_BOILERPLATE: frozenset[str] = frozenset(
    {
        "исковое",
        "заявление",
        "заявления",
        "исковым",
        "заявителю",
        "истец",
        "истца",
        "ответчик",
        "ответчика",
        "ответчиком",
        "ходатайство",
        "претензия",
        "претензии",
        "жалоба",
        "жалобы",
        "заявление",
        "заявления",
        "взыскании",
        "расторжении",
        "признании",
        "понукании",
        "изменении",
        "заключении",
        "расторжении",
        "договоре",
        "договора",
        "дополнительное",
        "соглашение",
        "соглашения",
        "судебном",
        "порядке",
        "административном",
        "исковое",
        "гражданский",
        "гражданского",
        "процессуальный",
        "процессуального",
        "апелляционная",
        "кассационная",
        "надзорная",
        "частная",
        "возражение",
        "проект",
        "юридический",
        "документ",
        "увольнении",
        "восстановлении",
        "премировании",
        "компенсации",
        "морального",
        "вреда",
        "неустойки",
        "штрафе",
        "неустойку",
        "взыскать",
        "взыскание",
        "требование",
        "требования",
        "обязать",
        "признать",
        "расторгнуть",
        "заключить",
        "изменить",
        "вернуть",
        "перечень",
        "приложение",
        "исполнении",
        "обязательств",
        "мотивированное",
        "определение",
        "решение",
        "постановление",
        "назначении",
        "экспертизы",
        "делу",
        "дела",
        "рассмотрении",
        "мер",
        "обеспечительных",
        "мер",
        "обеспечении",
        "иска",
        "иск",
        "просьбе",
        "основании",
        "краже",
        "ущербе",
        "ущерб",
        "взыскании",
        "неустойки",
        "неустойку",
        "пени",
        "пеней",
        "долга",
        "долг",
        "задолженности",
        "задолженность",
        "арбитражный",
        "районный",
        "городской",
        "мировой",
        "судья",
        "суд",
        "суды",
        "подаче",
        "подачи",
        "подачу",
        "обращении",
        "обращение",
        "заявлении",
        "заявитель",
        "заявителя",
        "стороны",
        "сторона",
        "сторон",
        "лица",
        "лицу",
        "лицом",
        "лиц",
        "физического",
        "юридического",
        "индивидуальный",
        "предприниматель",
        "общества",
        "общество",
        "ограниченной",
        "ответственностью",
        "полномочия",
        "представителя",
        "доверенность",
        "доверенности",
    }
)

# Уточняющие вопросы при недостатке контекста (идут первыми).
_CLARIFICATION_QUESTION_SEED: tuple[str, ...] = (
    "Опишите ситуацию своими словами: что произошло, между кем спор и какого результата вы хотите добиться?",
    "Кому будет адресован документ (суд, орган, контрагент)? Что вы уже успели сделать по делу: переписка, претензия, оплата, обращение в службу?",
    "Назовите вторую сторону, если знаете: ФИО или полное название организации / ИНН, адрес или контакт.",
    "Это судебный документ (иск, заявление в суд) или досудебный (претензия, уведомление)? Если суд — общий гражданский спор, трудной, семейный, по защите прав потребителей или спор с организацией?",
)


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._keys = cycle(settings.deepseek_api_keys_list or [""])
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # Увеличиваем лимиты соединений, чтобы запросы не вставали в очередь
            # по умолчанию httpx ограничивает до 100 одновременных соединений
            limits = httpx.Limits(max_keepalive_connections=100, max_connections=1000)
            self._client = httpx.AsyncClient(
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.deepseek_timeout_seconds,
                limits=limits,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def consult(self, problem_text: str, templates: list[TemplateMeta]) -> ConsultationResult:
        template_hint = "\n".join(
            f"- {template.document_type}: {template.title} ({template.category})"
            for template in templates
        )
        payload = await self._chat_json(
            [
                {"role": "system", "content": self._consult_system_prompt(template_hint)},
                {"role": "user", "content": problem_text},
            ]
        )
        try:
            return ConsultationResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректную консультацию: {exc}") from exc

    async def normalize_answers(
        self,
        template: TemplateMeta,
        raw_answers: dict[str, str],
    ) -> FillResult:
        payload = await self._chat_json(
            [
                {"role": "system", "content": self._fill_system_prompt(template)},
                {"role": "user", "content": json.dumps(raw_answers, ensure_ascii=False)},
            ]
        )
        try:
            result = FillResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректные данные документа: {exc}") from exc

        allowed = template.placeholders
        result.values = {key: str(value).strip() for key, value in result.values.items() if key in allowed}
        missing = [field.key for field in template.fields if field.required and not result.values.get(field.key)]
        if missing:
            raise DeepSeekError(f"Не заполнены обязательные поля: {', '.join(missing)}")
        return result

    async def _chat_json(self, messages: list[dict[str, str]], *, temperature: float | None = None) -> dict[str, Any]:
        if not self.settings.deepseek_api_keys_list:
            raise DeepSeekError("DEEPSEEK_API_KEYS не заполнен")

        temp = self.settings.deepseek_temperature if temperature is None else temperature
        errors: list[str] = []
        # Пробуем каждый ключ по кругу (Round-Robin). Если ключ отвалился (например, лимит 429),
        # сразу же, без задержек, пробуем следующий ключ из пула.
        for _ in range(len(self.settings.deepseek_api_keys_list)):
            api_key = next(self._keys)
            try:
                response = await self._http.post(
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": self.settings.deepseek_model,
                        "messages": messages,
                        "temperature": temp,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return json.loads(content)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                errors.append(f"HTTP {status}")
                # Если словили Too Many Requests (429) или ошибку сервера, идем к следующему ключу
            except (httpx.RequestError, KeyError, json.JSONDecodeError) as exc:
                errors.append(type(exc).__name__)
                
        raise DeepSeekError("Все DeepSeek API ключи вернули ошибку: " + " | ".join(errors))

    def _consult_system_prompt(self, template_hint: str) -> str:
        laws = ", ".join(SUPPORTED_LAW_AREAS)
        return (
            "Ты AI-юрист по документам для пользователей России. "
            "Дай понятную справочную консультацию простым языком. "
            f"Учитывай только применимые общие нормы: {laws}. "
            "Не выдумывай статьи, адреса, факты и сроки. Если не уверен, говори общо. "
            "Если пользователю нужен письменный документ, поставь document_required=true и короткое название в recommended_document. "
            "document_type — строго один из кодов шаблонов списка ниже, если ситуация прямо подходит под готовый шаблон; "
            "если нет — поставь document_type=null (в боте документ всё равно можно оформить в свободной форме).\n\n"
            f"Шаблоны (код — document_type):\n{template_hint}\n\n"
            "Верни строго JSON с ключами: category, consultation, risks, next_steps, "
            "document_required, recommended_document, document_type. "
            "document_type — код из списка или null."
        )

    @staticmethod
    def _meaningful_tokens(text: str) -> set[str]:
        return {m.group(0).lower() for m in re.finditer(r"[а-яёa-z]{4,}", text.lower())}

    @staticmethod
    def _question_fingerprint(q: str) -> str:
        s = q.strip().lower()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^\w\s\u0400-\u04FF]", "", s, flags=re.UNICODE)
        return s[:120]

    def _request_has_concrete_signals(self, t: str) -> bool:
        """Признаки, что пользователь дал хотя бы намёк на факты / реквизиты (не голый шаблон слова)."""
        low = t.lower()
        if re.search(r"\d", t):
            return True
        if re.search(r"\b(инн|огрн|огрнип|ооо|ooo|зао|пао|ао|ип|чек|договор)\b", low):
            return True
        if "@" in t:
            return True
        if re.search(r"\+?\d[\d\s\-()]{8,}\d", t):
            return True
        if re.search(
            r"\b(г\.|город|област|край|республ|улиц|ул\.|просп\.|проспект|дом\.|кв\.)\b",
            low,
        ):
            return True
        if low.count("\n") >= 1 and len(t) >= 100:
            return True
        return False

    def _request_is_context_sparse(self, request_text: str) -> bool:
        """Запрос без фактов (одно слово «иск», пара общих слов и т.п.)."""
        t = request_text.strip()
        words = re.findall(r"[а-яёa-z0-9]+", t.lower())
        substantive = [w for w in words if len(w) >= 5]

        if len(substantive) >= 3:
            return False
        if len(t) >= 160:
            return False

        if self._request_has_concrete_signals(t) and (len(substantive) >= 2 or len(t) >= 100):
            return False

        generic = frozenset(
            {
                "иск",
                "исковое",
                "исковый",
                "исковая",
                "заявление",
                "заявления",
                "претензия",
                "претензии",
                "жалоба",
                "жалобы",
                "договор",
                "договора",
                "напиши",
                "нужен",
                "нужна",
                "нужно",
                "сделай",
                "составь",
                "подготовь",
                "помоги",
                "документ",
                "ходатайство",
            }
        )
        if len(words) <= 8 and set(words) <= generic:
            return True
        if len(substantive) < 2 and len(t) < 100:
            return True
        return False

    def _title_introduces_unmentioned_topics(self, request_text: str, title: str) -> bool:
        """В заголовке есть смысловые слова, которых нет в запросе пользователя (галлюцинация темы)."""
        req = self._meaningful_tokens(request_text)
        tit = self._meaningful_tokens(title)
        novel = tit - req - _DOCUMENT_TITLE_BOILERPLATE
        return bool(novel)

    @staticmethod
    def _neutral_document_title(request_text: str) -> str:
        r = request_text.lower()
        if "претенз" in r:
            return "Претензия — уточняем адресата, предмет и основание требований"
        if "жалоб" in r and "иск" not in r and "исков" not in r:
            return "Жалоба — уточняем орган и содержание"
        if any(k in r for k in ("иск", "исков")):
            return "Исковое заявление — уточняем суд, стороны, предмет спора и требования"
        if "договор" in r or "соглашен" in r:
            return "Договор / соглашение — уточняем тип, стороны и условия"
        if "заявлен" in r:
            return "Заявление — уточняем адресата и цель обращения"
        return "Юридический документ — уточняем задачу и обстоятельства"

    def _sanitize_extracted_facts_summary(self, request_text: str, summary: str) -> str:
        s = summary.strip().replace("\n", " ")
        if len(s) < 16:
            return ""
        if self._title_introduces_unmentioned_topics(request_text, s):
            return ""
        return s[:420]

    @staticmethod
    def _trim_document_title(title: str, max_len: int = 130) -> str:
        t = " ".join(title.strip().split())
        if len(t) <= max_len:
            return t
        cut = t[: max_len - 1]
        if " " in cut:
            return cut.rsplit(maxsplit=1)[0].rstrip(",.; ") + "…"
        return cut + "…"

    @staticmethod
    def _merge_question_lists(primary: Sequence[str], secondary: Sequence[str], *, max_questions: int = 7) -> list[str]:
        seen_exact: set[str] = set()
        seen_fp: set[str] = set()
        out: list[str] = []
        for q in (*primary, *secondary):
            q = q.strip()
            if len(q) < 10 or q in seen_exact:
                continue
            fp = DeepSeekClient._question_fingerprint(q)
            if fp in seen_fp:
                continue
            seen_exact.add(q)
            seen_fp.add(fp)
            out.append(q)
            if len(out) >= max_questions:
                break
        return out

    def _sanitize_document_questions(self, request_text: str, result: DocumentQuestionsResult) -> DocumentQuestionsResult:
        sparse = self._request_is_context_sparse(request_text)
        raw_title = self._trim_document_title(result.document_title)
        leaky_title = self._title_introduces_unmentioned_topics(request_text, raw_title)
        force_neutral_title = sparse or leaky_title

        summary = self._sanitize_extracted_facts_summary(request_text, getattr(result, "extracted_facts_summary", ""))

        cleaned = [q.strip() for q in result.questions if isinstance(q, str) and q.strip()]
        clarification_needed = force_neutral_title or result.clarification_needed

        if force_neutral_title:
            title = self._neutral_document_title(request_text)
            merged = self._merge_question_lists(_CLARIFICATION_QUESTION_SEED, cleaned)
            if sparse:
                summary = ""
        elif result.clarification_needed:
            title = raw_title
            merged = self._merge_question_lists(_CLARIFICATION_QUESTION_SEED, cleaned)
        else:
            title = raw_title
            merged = cleaned

        if len(merged) < 4:
            merged = self._merge_question_lists(_CLARIFICATION_QUESTION_SEED, merged)

        merged = merged[:7]

        return DocumentQuestionsResult(
            document_title=title or self._neutral_document_title(request_text),
            questions=merged,
            clarification_needed=clarification_needed,
            extracted_facts_summary=summary,
        )

    def _document_questions_system_prompt(self) -> str:
        return (
            "Ты юрист по составлению документов для пользователей РФ. Твоя цель на этом шаге — корректно понять задачу, "
            "а не составить финальный текст.\n\n"
            "КРИТИЧЕСКИЕ ПРАВИЛА\n"
            "- Единственный источник фактов — сообщение пользователя. Не добавляй стороны, суммы, даты, суды и темы, "
            "которых там нет.\n"
            "- Если сообщение общее (например «иск», «сделай претензию», «напиши заявление» без фактов), "
            "поставь clarification_needed=true и document_title только в формате «<вид документа> — нужны уточнения», "
            "без узкого предмета («алименты», «ДТП», «аренда» и т.д.), если пользователь этого явно не писал.\n"
            "- Узкий document_title («Претензия о возврате предоплаты за…» и т.п.) допустим ТОЛЬКО если предмет уже выражен в тексте.\n"
            "- Различай досудебный документ и судебный. Оформляй сценарий как иск только если есть запрос на суд "
            "или описаны факты, из которых логична подача иска.\n"
            "- Вопросы 4–7 штук. Следуй порядку: предмет спора → стороны → адресат (суд/орган/контрагент) → суммы и сроки → доказательства.\n\n"
            "Поле extracted_facts_summary:\n"
            "- 1 короткое предложение, только парафраз того, что буквально есть в тексте пользователя;\n"
            "- если явных фактов почти нет — пустая строка \"\".\n\n"
            "ПРИМЕРЫ (не копируй вопросы дословно; соблюдай логику и формат ответа)\n\n"
            "Пользователь: «иск»\n"
            '{ "document_title": "Исковое заявление — нужны уточнения", '
            '"clarification_needed": true, '
            '"extracted_facts_summary": "", '
            '"questions": ['
            '"Какой именно конфликт и что хотите получить решением суда?",'
            '"С кем спор — физическое лицо, ИП или организация (если знаете название или ИНН)?",'
            '"Какие ключевые даты или суммы уже есть?",'
            '"Были ли досудебные шаги (претензия, претензионный порядок)?"'
            '] }\n\n'
            "Пользователь: «Претензия в М.Видео, телефон с браком, оплата картой, чек сохранился, отказ возврат»\n"
            '{ "document_title": "Претензия по ЗоЗПП о возврате денег за некачественный товар в магазин электроники", '
            '"clarification_needed": false, '
            '"extracted_facts_summary": "Пользователь описал покупку телефона с дефектом в магазине и отказ в возврате; платил картой, чек есть.", '
            '"questions": ['
            '"Дата покупки и номер заказа или чека, если они есть?",'
            '"Модель товара и в чём именно проявляется брак?",'
            '"Когда вы заявили о недостатках и когда получили отказ?",'
            '"Если известен канал для претензий (e-mail/адрес поддержки) — укажите."'
            '] }\n\n'
            'Верни строго JSON вида:'
            '{"document_title":"...", "questions":["..."],"clarification_needed":false,'
            '"extracted_facts_summary":""}'
        )

    async def generate_document_questions(self, request_text: str) -> DocumentQuestionsResult:
        user_block = (
            "Ниже — единственный источник фактов о задаче пользователя. "
            "Не используй информацию вне этого текста при выборе названия документа и summary.\n\n"
            f"{request_text.strip()}"
        )
        payload = await self._chat_json(
            [
                {"role": "system", "content": self._document_questions_system_prompt()},
                {"role": "user", "content": user_block},
            ],
            temperature=0.08,
        )
        try:
            raw = DocumentQuestionsResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректные вопросы: {exc}") from exc
        return self._sanitize_document_questions(request_text.strip(), raw)

    async def generate_dynamic_document(self, request_text: str, details_text: str) -> DynamicDocumentResult:
        from datetime import datetime
        current_date = datetime.now().strftime("%d.%m.%Y")
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты высококвалифицированный российский юрист. Твоя задача — составить безупречный юридический документ. "
                        "Проанализируй изначальный запрос и дополнительные детали от пользователя. "
                        "Ниже в инструкции приведён пример JSON только для полей и структуры; не переносите факты, имена судов, "
                        "организаций и суммы из примера — используйте только текст пользователя. "
                        "Правила составления: "
                        "1. Структура: Вводная часть (кто/кому), Описательная часть (факты в хронологии), Мотивировочная часть (ссылки на конкретные статьи ГК, ТК, ЗоЗПП, КоАП, ГПК/АПК и т.д.), Резолютивная часть (четкие требования). "
                        "2. Тон: Сухой, официально-деловой, беземоциональный, убедительный. "
                        "3. Данные: Используй только те сведения, которые есть в запросе и ответах пользователя. "
                        "Не добавляй новые факты, имена, суммы и организации «от себя». "
                        "Если данных не хватает (ФИО, адрес суда и т.п.), ставь подчёркивание «________________». "
                        "Не используй английские слова или скобки вроде [ФИО]. "
                        f"Текущая дата: {current_date}. "
                        "Верни строго JSON со следующей структурой:\n"
                        "{\n"
                        "  \"header\": [\"В Кунцевский районный суд г. Москвы\", \"Адрес: ...\", \"\", \"Истец: Иванов И.И.\", \"Адрес: ...\", \"\", \"Ответчик: ООО 'Ромашка'\", \"Адрес: ...\"], // Шапка (правый верхний угол). Пустые строки для отступов.\n"
                        "  \"title\": \"ИСКОВОЕ ЗАЯВЛЕНИЕ\", // Главный заголовок (по центру, ЗАГЛАВНЫМИ)\n"
                        "  \"subtitle\": \"о взыскании долга и компенсации морального вреда\", // Подзаголовок (по центру, строчными)\n"
                        "  \"body\": [\"12 мая 2023 года между мной...\", \"В соответствии со ст. 309 ГК РФ...\"], // Основной текст. Каждый абзац - элемент списка. Логично разделяй факты и правовую базу.\n"
                        "  \"requests\": [\"1. Взыскать с ответчика...\", \"2. ...\"], // Требования (нумерованный список)\n"
                        "  \"attachments\": [\"1. Копия паспорта\", \"2. ...\"], // Приложения (нумерованный список)\n"
                        "  \"date_and_signature\": \"13.05.2026    _______________ /Иванов И.И./\", // Дата и подпись\n"
                        "  \"instruction\": \"Подробная инструкция: куда подавать (адрес/ведомство), размер госпошлины (если есть), количество экземпляров, нужно ли отправлять копию второй стороне, сроки рассмотрения.\"\n"
                        "}"
                    )
                },
                {"role": "user", "content": f"Запрос: {request_text}\nДетали: {details_text}"},
            ],
            temperature=0.15,
        )
        try:
            return DynamicDocumentResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректный документ: {exc}") from exc

    def _fill_system_prompt(self, template: TemplateMeta) -> str:
        from datetime import datetime
        current_date = datetime.now().strftime("%d.%m.%Y")
        fields = ", ".join(field.key for field in template.fields)
        return (
            "Ты нормализуешь ответы пользователя для заполнения юридического шаблона. "
            "Не добавляй новые юридические блоки и не меняй смысл. "
            "Не выдумывай отсутствующие факты. Если пользователь ввел неясно, аккуратно приведи к официальному стилю. "
            f"Текущая дата: {current_date}. Если пользователь пишет 'вчера', 'сегодня', 'завтра', переведи это в точную дату. "
            "Даты должны быть в формате ДД.ММ.ГГГГ. Суммы должны быть числами (или числами с указанием валюты). "
            f"Документ: {template.title}. Допустимые поля: {fields}. "
            "Верни строго JSON: {\"values\": {field: value}, \"instruction\": \"краткая инструкция\"}."
        )
