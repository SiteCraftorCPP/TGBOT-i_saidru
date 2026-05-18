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

# Только для совсем «пустых» запросов («сделай документ», «иск» без фактов). Без подстановки «спора/суда»,
# если пользователь сам об этом не писал — остальное задаёт модель по виду документа.
_SPARSE_REQUEST_QUESTION_SEED: tuple[str, ...] = (
    "Какой именно документ вам нужен одной фразой (например: договор найма жилого помещения, претензия о возврате денег, исковое заявление)?",
    "Кратко по делу: кто стороны, что нужно закрепить в документе (цель, объект, сроки или суммы — что уже знаете; неизвестное напишите «не знаю»).",
)

# Добавляются только если после фильтрации «не в тему» осталось мало пунктов (проект договора без суда).
_CONTRACT_FALLBACK_QUESTIONS: tuple[str, ...] = (
    "Стороны: полные ФИО или наименование, статус (физлицо / ИП / юрлицо), контакты; при наличии — ИНН/ОГРН и расчётный счёт.",
    "Предмет: что именно передаётся, оказывается или продаётся; существенные характеристики, объём, качество?",
    "Цена или размер периодических платежей, валюта, НДС при необходимости, аванс/рассрочка и сроки расчётов?",
    "Срок действия договора, этапы исполнения, порядок сдачи-приёмки и документооборот (акты, накладные)?",
    "Ответственность сторон: неустойка/штрафы, порядок претензий и сроки устранения нарушений?",
    "Расторжение: основания, срок уведомления, расчёты при выходе из договора, возврат обеспечения?",
    "Типовые договорные условия: форс-мажор, конфиденциальность, уступка прав, применимое право и разрешение споров по договору (суд/арбитраж — если хотите прописать)?",
)

_LEASE_CONTRACT_FALLBACK_QUESTIONS: tuple[str, ...] = (
    "Стороны: данные наймодателя и нанимателя (ФИО/паспорт или наименование и регистрация организации) — что уже есть?",
    "Объект: полный адрес, назначение (жилое/нежилое), площадь и основание владения у наймодателя (собственность, доверенность и т.д.), известный кадастровый номер?",
    "Срок найма/аренды, дата начала, досрочное расторжение и продление?",
    "Размер и порядок внесения платы, индексация, ответственность за просрочку, отдельно ли залог/обеспечительный платёж?",
    "Коммунальные и эксплуатационные услуги: что входит в арендную плату, что оплачивает наниматель напрямую?",
    "Состояние при передаче, перечень имущества/мебели, текущий и капитальный ремонт, порядок доступа наймодателя?",
    "Передача-возврат по актам, условия расторжения и возврата залога, ограничения по субаренде/передача третьим лицам?",
)

# Признаки «процессуального» вопроса — отфильтровываются, если пользователь просит только договор/сделку без суда.
_LITIGATION_QUESTION_NOISE = re.compile(
    r"(?<![а-яё])(?:"
    r"ответчик|истец|истца|истцом|исковое\s+заявление|подать\s+иск|иск\s+к\s+"
    r"|мировой\s+суд|районн\w*\s+суд|арбитражн\w*\s+суд|апк\s*рф|гпк\s*рф"
    r"|государственн\w*\s+пошлин|госпошлин"
    r"|в\s+какой\s+суд|какой\s+конфликт|предмет\s+спора|между\s+кем.*спор"
    r")(?![а-яё])",
    re.IGNORECASE | re.DOTALL,
)

_PERSONAL_DATA_FORM_QUESTION_NOISE = re.compile(
    r"(?<![а-яёa-z])(?:"
    r"оператор\s+персональн|субъект\s+персональн|152[\s-]*фз"
    r"|цел[ия]\s+обработки\s+персональн|согласи\w*\s+на\s+обработку\s+персональн"
    r"|какие\s+персональн|обработк\w*\s+персональн|учёт\s+персональн"
    r")(?![а-яёa-z0-9])",
    re.IGNORECASE | re.DOTALL,
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

    async def _chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        if not self.settings.deepseek_api_keys_list:
            raise DeepSeekError("DEEPSEEK_API_KEYS не заполнен")

        temp = self.settings.deepseek_temperature if temperature is None else temperature
        errors: list[str] = []
        body: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "temperature": temp,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        # Пробуем каждый ключ по кругу (Round-Robin). Если ключ отвалился (например, лимит 429),
        # сразу же, без задержек, пробуем следующий ключ из пула.
        for _ in range(len(self.settings.deepseek_api_keys_list)):
            api_key = next(self._keys)
            try:
                response = await self._http.post(
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
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

    @staticmethod
    def _user_wants_contract_draft(request_text: str) -> bool:
        low = request_text.lower()
        return bool(
            re.search(
                r"(?<![а-яёa-z])(?:"
                r"договор|соглашен|дду|аренд|найм|купл[\s-]*продаж|займ|кредит|лизинг|подряд|"
                r"оказани\w*\s+услуг"
                r")(?![а-яёa-z0-9])",
                low,
            )
        )

    @staticmethod
    def _user_wants_litigation_route(request_text: str) -> bool:
        low = request_text.lower()
        if re.search(
            r"(?<![а-яёa-z])(?:"
            r"исковое|иск\s+к|ответчик|истец|истца|истцом|"
            r"подать\s+в\s+суд|подам\s+в\s+суд|пойду\s+в\s+суд|обратить\w*\s+в\s+суд|"
            r"судебн\w*\s+(?:порядок|разбирательств|практик)|"
            r"мировой\s+суд|районн\w*\s+суд|арбитражн\w*\s+суд|"
            r"госпошлин|государственн\w*\s+пошлин|частн\w*\s+жалоб|кассаци|апелляц"
            r")(?![а-яёa-z0-9])",
            low,
        ):
            return True
        if re.search(r"(?<![а-яёa-z])иск(?![а-яёa-z0-9])", low):
            return True
        if re.search(r"\bгпк\b|\bапк\b", low):
            return True
        return False

    @staticmethod
    def _user_wants_personal_data_document(request_text: str) -> bool:
        low = request_text.lower()
        return bool(
            re.search(
                r"(персональн\w*\s+данн|152[\s-]*фз|согласи\w*\s+на\s+обработку|"
                r"политик\w*\s+конфиденци|обработк\w*\s+персональн|оператор\s+пд)",
                low,
            )
        )

    @staticmethod
    def _likely_lease_contract(request_text: str) -> bool:
        low = request_text.lower()
        return bool(
            re.search(
                r"(найм(?:\s+жил|а\s+жил)?|аренд\w*|квартир\w*|жил(?:ое|ого|ым)?\s+помещен|"
                r"нежил\w*\s+помещен)",
                low,
            )
        )

    def _classifier_rubric_line(self, request_text: str) -> str:
        """Короткая подсказка модели (без новых фактов), чтобы вопросы совпали с типом задачи."""
        low = request_text.strip().lower()
        bits: list[str] = []
        if self._user_wants_personal_data_document(low):
            bits.append("в тексте затронуты ПДн / согласие / политика → допустимы уточнения по 152‑ФЗ")
        if self._user_wants_litigation_route(low):
            bits.append("есть процессуальный контекст (суд, иск, стороны процесса) → уместны процессуальные вопросы")
        elif self._user_wants_contract_draft(low):
            bits.append("похоже на проект договора/сделки без суда → не уводи в «спор/истца/ответчика», спрашивай существенные условия")
        if re.search(r"претенз|требова|возврат|некачествен|задолжен|неустойк", low) and not self._user_wants_litigation_route(
            low
        ):
            bits.append("есть досудебные/претензионные мотивы → адресат, факты, срок исполнения, приложения")
        if not bits:
            bits.append("тип задачи из текста неочевиден → сперва выясни вид документа и ключевые факты нейтральными вопросами")
        return "Автоклассификатор (ориентир, не подставляй факты): " + " | ".join(bits) + "."

    def _filter_questions_by_request_intent(self, request_text: str, questions: list[str]) -> list[str]:
        low = request_text.lower()
        contract_only = self._user_wants_contract_draft(low) and not self._user_wants_litigation_route(low)
        allow_pd_form = self._user_wants_personal_data_document(low)
        out: list[str] = []
        for q in questions:
            if contract_only and _LITIGATION_QUESTION_NOISE.search(q):
                continue
            if not allow_pd_form and _PERSONAL_DATA_FORM_QUESTION_NOISE.search(q):
                continue
            out.append(q)
        return out

    def _backfill_contract_questions(
        self,
        request_text: str,
        questions: list[str],
        *,
        sparse: bool,
        max_questions: int,
    ) -> list[str]:
        """Если фильтр отрезал лишнее и список стал коротким — добиваем типовым чек-листом по договору."""
        if sparse or len(questions) >= 6:
            return questions[:max_questions]
        low = request_text.lower()
        if not (self._user_wants_contract_draft(low) and not self._user_wants_litigation_route(low)):
            return questions[:max_questions]
        extra = _LEASE_CONTRACT_FALLBACK_QUESTIONS if self._likely_lease_contract(low) else _CONTRACT_FALLBACK_QUESTIONS
        merged = self._merge_question_lists(questions, extra, max_questions=max_questions)
        return merged

    def _sanitize_document_questions(self, request_text: str, result: DocumentQuestionsResult) -> DocumentQuestionsResult:
        sparse = self._request_is_context_sparse(request_text)
        raw_title = self._trim_document_title(result.document_title)
        leaky_title = self._title_introduces_unmentioned_topics(request_text, raw_title)
        force_neutral_title = sparse or leaky_title

        summary = self._sanitize_extracted_facts_summary(request_text, getattr(result, "extracted_facts_summary", ""))

        cleaned = [q.strip() for q in result.questions if isinstance(q, str) and q.strip()]
        clarification_needed = force_neutral_title or result.clarification_needed

        max_questions = 12

        if force_neutral_title:
            title = self._neutral_document_title(request_text)
            if sparse:
                merged = self._merge_question_lists(
                    _SPARSE_REQUEST_QUESTION_SEED, cleaned, max_questions=max_questions
                )
                summary = ""
            else:
                merged = self._merge_question_lists((), cleaned, max_questions=max_questions)
        elif result.clarification_needed:
            title = raw_title
            merged = self._merge_question_lists((), cleaned, max_questions=max_questions)
        else:
            title = raw_title
            merged = self._merge_question_lists((), cleaned, max_questions=max_questions)

        if sparse and len(merged) < 2:
            merged = self._merge_question_lists(
                _SPARSE_REQUEST_QUESTION_SEED, merged, max_questions=max_questions
            )

        merged = self._filter_questions_by_request_intent(request_text, merged)
        merged = self._backfill_contract_questions(
            request_text, merged, sparse=sparse, max_questions=max_questions
        )
        merged = merged[:max_questions]

        return DocumentQuestionsResult(
            document_title=title or self._neutral_document_title(request_text),
            questions=merged,
            clarification_needed=clarification_needed,
            extracted_facts_summary=summary,
        )

    def _document_questions_system_prompt(self) -> str:
        return (
            "Ты ведущий практикующий юрист РФ по подготовке документов. Сейчас ты только собираешь факты для будущего черновика; "
            "готовый текст, шапку суда и реквизиты «из головы» не придумывай.\n\n"
            "В сообщении пользователя в конце может быть строка «Автоклассификатор…» — это служебный ориентир по типу задачи. "
            "Согласуй с ней выбор тем вопросов; факты и названия сторон бери только из основного текста пользователя (блок до этой строки).\n\n"
            "ШАГ 1 — определи сценарий строго из текста пользователя (не дорисовывай):\n"
            "• проект договора/сделки (дарение, купля-продажа, аренда/найм, подряд, услуги, займ, лизинг и т.д.);\n"
            "• досудебное обращение (претензия, требование, уведомление контрагенту/продавцу);\n"
            "• судебный документ (иск, заявление, жалоба, ходатайство в суд);\n"
            "• обращение в орган (заявление, жалоба в госорган);\n"
            "• документ по персональным данным (согласие, политика) — только если в тексте есть ПДн/152‑ФЗ/«согласие на обработку».\n\n"
            "ШАГ 2 — сформируй 6–12 ЦЕЛЕВЫХ вопросов (без «общих рассуждений»):\n"
            "• Для ДОГОВОРА: существенные и обычно существенные условия по ГК для ЭТОГО типа, стороны и реквизиты, предмет, цена/вознаграждение, срок, "
            "передача/сдача, гарантии, залог/обеспечение, ответственность, расторжение, урегулирование разногласий по договору. "
            "Если пользователь НЕ писал про суд/иск — НЕ спрашивай про ответчика/истца/между кем спор/госпошлину. Допускается уточнить подсудность/арбитраж "
            "только как пункт договорной оговорки.\n"
            "• Для ПРЕТЕНЗИИ: адресат, хронология, правовое основание требований, суммы и срок исполнения, документы во вложении.\n"
            "• Для ИСКА/СУДА: суд при известности, истец/ответчик, предмет спора, требования, доказательства, досудебный порядок если уместен.\n"
            "• Для ПДн: оператор, цели, состав, категории субъектов, сроки/трансграничная передача — только если пользователь зашёл в эту тему.\n\n"
            "ЧЕКЛИСТЫ (выбирай фрагменты под тип; не выплёскивай всё сразу — только пробелы в знаниях пользователя):\n"
            "— Аренда/найм жилья или нежилого: стороны; адрес и режим объекта; срок; аренда/индексация; залог; коммуналка; ремонт; передача/возврат; субаренда; расторжение.\n"
            "— Купля-продажа: стороны; объект (вещь/недвижимость), идентификация; цена; расчёты; риски до регистрации/передачи; гарантии; состояние; момент перехода права.\n"
            "— Подряд/услуги: ТЗ/объём; результат и приёмка; сроки; цена и авансы; материалы и оборудование; ответственность за просрочку/дефекты; интеллектуальные права при необходимости.\n"
            "— Займ: стороны; сумма; валюта; %/индексация; срок возврата; порядок погашения; обеспечение; последствия просрочки.\n"
            "— Трудовой договор: стороны; должность/работа; место; режим; оклад/надбавки; отпуск; испытание; конфиденциальность/конкуренция при необходимости.\n\n"
            "ФОРМАТ ЗАГОЛОВКА\ndocument_title: если фактов мало — «<вид документа> — нужны уточнения»; узкая формулировка только если предмет уже явно в тексте.\n"
            "clarification_needed=true, если из сообщения нельзя понять даже семейство документа или нет фактов для черновика.\n\n"
            "extracted_facts_summary: ровно одно короткое предложение-парафраз фактов из текста пользователя, либо \"\" если фактов почти нет.\n\n"
            "ПРИМЕРЫ ЛОГИКИ (не копируй дословно)\n\n"
            "Пользователь: «иск»\n"
            '{"document_title":"Исковое заявление — нужны уточнения",'
            '"clarification_needed":true,"extracted_facts_summary":"",'
            '"questions":['
            '"В какой суд (или подсудность по спору) и есть ли типовой исковый срок?",'
            '"Сформулируйте требования: что взыскать/признать/обязать и фактическое основание?",'
            '"Ответчик: наименование/ФИО, адрес, ИНН если знаете?",'
            '"Какие документы уже есть для приложения (договор, акты, переписка, чеки)?"'
            "]}\n\n"
            "Пользователь: «договор аренды квартиры»\n"
            '{"document_title":"Договор найма жилого помещения — нужны уточнения",'
            '"clarification_needed":true,"extracted_facts_summary":"",'
            '"questions":['
            '"Стороны: ФИО и реквизиты наймодателя и нанимателя (что есть — паспорт, адрес регистрации)?",'
            '"Объект: адрес, этаж/площадь, основание у наймодателя (собственность/иное)?",'
            '"Срок и дата начала; условия продления или расторжения?",'
            '"Арендная плата, сроки оплаты, индексация; отдельно залог?",'
            '"Коммунальные платежи: что входит в аренду?",'
            '"Состояние при въезде/выезде, мебель и техника, ремонт, запрет/разрешение субаренды?"'
            "]}\n\n"
            "Пользователь: «Претензия в М.Видео, телефон брак, оплата картой, чек есть, отказ в возврате»\n"
            '{"document_title":"Претензия по ЗоЗПП о возврате денег за некачественный товар",'
            '"clarification_needed":false,'
            '"extracted_facts_summary":"Покупка телефона с дефектом; оплата картой, чек есть; в возврате отказали.",'
            '"questions":['
            '"Дата покупки и номер заказа/чека?",'
            '"Модель и описание недостатка?",'
            '"Когда требовали возврат и какой ответ?",'
            '"Известен ли адрес/email для претензии?"'
            "]}\n\n"
            "Верни строго JSON вида "
            '{"document_title":"...", "questions":["..."],"clarification_needed":false,"extracted_facts_summary":""}'
        )

    async def generate_document_questions(self, request_text: str) -> DocumentQuestionsResult:
        rt = request_text.strip()
        user_block = (
            "Ниже — единственный источник фактов о задаче пользователя. "
            "Не используй информацию вне этого текста при выборе названия документа и summary.\n\n"
            f"{rt}\n\n"
            f"{self._classifier_rubric_line(rt)}"
        )
        payload = await self._chat_json(
            [
                {"role": "system", "content": self._document_questions_system_prompt()},
                {"role": "user", "content": user_block},
            ],
            temperature=0.17,
            max_tokens=4096,
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
