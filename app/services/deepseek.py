import json
import logging
import re
from collections.abc import Sequence
from itertools import cycle
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.constants import SUPPORTED_LAW_AREAS
from app.schemas.ai import (
    ConsultationResult,
    DocumentReadinessResult,
    DocumentQuestionsResult,
    DynamicDocumentResult,
    FillResult,
    TemplateMeta,
)


class DeepSeekError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


def _parse_json_object_content(content: str) -> dict[str, Any]:
    """Достаёт JSON из ответа модели (режим json_object иногда оборачивают в ``` или текст)."""
    raw = (content or "").strip()
    if raw.startswith("\ufeff"):
        raw = raw[1:].lstrip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()
    try:
        parsed: dict[str, Any] = json.loads(raw)
        return parsed
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise


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
            read_s = float(max(15, self.settings.deepseek_timeout_seconds))
            self._client = httpx.AsyncClient(
                base_url=self.settings.deepseek_base_url,
                timeout=httpx.Timeout(connect=15.0, read=read_s, write=60.0, pool=10.0),
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
        read_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not self.settings.deepseek_api_keys_list:
            raise DeepSeekError("DEEPSEEK_API_KEYS не заполнен")

        temp = self.settings.deepseek_temperature if temperature is None else temperature
        errors: list[str] = []

        read_sec = float(
            read_seconds if read_seconds is not None else max(30, self.settings.deepseek_timeout_seconds)
        )
        request_timeout = httpx.Timeout(
            connect=20.0,
            read=read_sec,
            write=min(max(read_sec, 60.0), 300.0),
            pool=30.0,
        )

        base: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "temperature": temp,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            base["max_tokens"] = max_tokens

        n_keys = len(self.settings.deepseek_api_keys_list)
        # Ответ уже пришёл (HTTP 200), но модель порвала/испачкала JSON — повторяем тем же ключом.
        same_key_decode_attempts = 3
        same_key_read_retries = 3

        for key_attempt in range(1, n_keys + 1):
            api_key = next(self._keys)
            outbound = dict(base)
            bail_http = False
            decode_problem_tags: list[str] = []

            for decode_try in range(1, same_key_decode_attempts + 1):
                finish_reason: str | None = None
                jr: dict[str, Any] | None = None

                read_timeout_fatal = False
                for net_try in range(1, same_key_read_retries + 1):
                    try:
                        response = await self._http.post(
                            "/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"},
                            json=outbound,
                            timeout=request_timeout,
                        )
                        response.raise_for_status()
                        jr = response.json()
                        break
                    except httpx.HTTPStatusError as exc:
                        code = exc.response.status_code
                        errors.append(f"HTTP_{code}")
                        logger.warning(
                            "DeepSeek ключ %s/%s: HTTP %s — переключаюсь на следующий ключ",
                            key_attempt,
                            n_keys,
                            code,
                        )
                        bail_http = True
                        break
                    except httpx.ReadTimeout as exc:
                        logger.warning(
                            "DeepSeek ключ %s/%s: ReadTimeout чтения (%ss, попытка сети %s/%s, тот же ключ)",
                            key_attempt,
                            n_keys,
                            int(read_sec),
                            net_try,
                            same_key_read_retries,
                        )
                        if net_try >= same_key_read_retries:
                            errors.append(f"ReadTimeout×{same_key_read_retries}")
                            read_timeout_fatal = True
                        # иначе повтор той же попытки
                    except httpx.RequestError as exc:
                        errors.append(f"{type(exc).__name__}:{exc!r}")
                        logger.warning(
                            "DeepSeek ключ %s/%s: транспорт %s — следующий ключ",
                            key_attempt,
                            n_keys,
                            type(exc).__name__,
                        )
                        bail_http = True
                        break

                if bail_http:
                    break
                if read_timeout_fatal:
                    bail_http = True
                    break
                if jr is None:
                    bail_http = True
                    break

                try:
                    choice0 = jr["choices"][0]
                    finish_reason = choice0.get("finish_reason")
                    msg = choice0.get("message") or {}
                    content_raw = msg.get("content")
                    if content_raw is None or (isinstance(content_raw, str) and not content_raw.strip()):
                        raise KeyError("empty message content")
                    return _parse_json_object_content(str(content_raw))
                except (KeyError, json.JSONDecodeError, TypeError) as exc:
                    decode_problem_tags.append(type(exc).__name__)
                    logger.warning(
                        "DeepSeek ключ %s/%s: модель вернула невалидный JSON после HTTP 200 "
                        "(попытка %s/%s, тот же API-ключ повторится): %s",
                        key_attempt,
                        n_keys,
                        decode_try,
                        same_key_decode_attempts,
                        type(exc).__name__,
                    )
                    fr = finish_reason or ""
                    if fr == "length" and isinstance(outbound.get("max_tokens"), int):
                        mt = int(outbound["max_tokens"])
                        outbound["max_tokens"] = min(max(int(mt * 1.85), mt + 300), 8192)
                        logger.info(
                            "Ответ завершился по «length»: увеличиваю max_tokens до %s и повторяю тот же ключ",
                            outbound["max_tokens"],
                        )

            if bail_http:
                continue

            errors.append(
                "ключ %s из %s: после %s разборок JSON по-прежнему ошибка (%s)"
                % (
                    key_attempt,
                    n_keys,
                    same_key_decode_attempts,
                    ",".join(decode_problem_tags) or "unknown",
                )
            )

        raise DeepSeekError("Не удалось получить корректный JSON от DeepSeek: " + " || ".join(errors))

    def _consult_system_prompt(self, template_hint: str) -> str:
        laws = ", ".join(SUPPORTED_LAW_AREAS)
        return (
            "Ты старший консультант по праву для пользователей РФ (как юрист в топ-фирме на первичном приёме): ясно, структурно, без панибратства. "
            "Разложи ситуацию по слоям: факты, применимая отрасль права из перечисленных, типовые риски, что можно сделать дальше. "
            f"Опирайся на обобщённые нормы областей: {laws}. Не выдумывай номера статей, дел, адреса судов и исходы, которых нет в сообщении. "
            "Если без деталей нельзя сказать точно — так и обозначь альтернативы. "
            "Если пользователю нужен письменный документ, document_required=true и осмысленное название в recommended_document. "
            "document_type — строго один из кодов шаблонов ниже, если ситуация явно подходит; иначе null.\n\n"
            f"Шаблоны (код — document_type):\n{template_hint}\n\n"
            "Верни строго JSON: category, consultation, risks, next_steps, document_required, recommended_document, document_type. "
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
            bits.append(
                "ПДн / согласие / политика: задавай вопросы уровня DPO/юриста по compliance (цели, состав, сроки, категории, трансграничность)"
            )
        if self._user_wants_litigation_route(low):
            bits.append(
                "Процессуальный контекст: выясняй подсудность, предмет спора, состав участников, цену иска/обжалуемые акты, доказательства, досудебный порядок"
            )
        elif self._user_wants_contract_draft(low):
            bits.append(
                "Проект сделки/договора: выясни существенные и обычно существенные условия по типу договора, риски, типовые оговорки; не перетягивай в процессуальные формулировки"
            )
        if re.search(
            r"претенз|требова|возврат|некачествен|задолжен|неустойк", low
        ) and not self._user_wants_litigation_route(low):
            bits.append(
                "Досудебная защита: установи адресата, правовую квалификацию требований, сроки и способ направления, пакет доказательных приложений"
            )
        if not bits:
            bits.append(
                "Ниша неочевидна: сначала классифицируй запрос (вид документа), затем задавай структурированные вопросы как на приёме у партнёра юрфирмы"
            )
        return (
            "Автоклассификатор (только ориентир сценария, не факты): "
            + " | ".join(bits)
            + "."
        )

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
        if sparse or len(questions) >= 5:
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

        max_questions = 8

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
            "Юрист РФ: собираешь факты для черновика документа, без юрзаключений и без выдумок. "
            "Не повторяй уже сказанное пользователем.\n"
            "Строка «Автоклассификатор…» в конце текста пользователя — лишь сценарная подсказка; факты только из строк выше неё.\n\n"
            "Сделай ровно 5–8 конкретных вопросов (в каждом один юридически содержательный фокус). "
            "Договор: стороны/форма (физ/ИП/ООО), предмет, деньги/аренда, сроки, приёмка, ответственность, расторжение, споры из договора; "
            "без суда/истца/госпошлины если пользователь не про спор. "
            "Претензия/иск: своя логика (адресат, требование, сроки, доказательства; для иска подсудность при необходимости). "
            "Персональные данные — только если пользователь затронул тему.\n"
            "Направления (что релевантно — включай точечно): аренда/найм жил или нежил; купля-продажа; подряд/услуги; заём; труд.\n\n"
            "questions — упорядоченный массив: в приложении задаётся строго по одному вопросу за раз.\n"
            "clarification_needed=true, если не ясен тип документа или совсем нет фактов. "
            "extracted_facts_summary — одно короткое предложение по букве запроса или пустая строка.\n"
            'Верни JSON: {"document_title":"…","questions":["…"],"clarification_needed":false,"extracted_facts_summary":""}'
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
            temperature=0.12,
            max_tokens=1800,
        )
        try:
            raw = DocumentQuestionsResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректные вопросы: {exc}") from exc
        return self._sanitize_document_questions(request_text.strip(), raw)

    @staticmethod
    def _document_readiness_system_prompt() -> str:
        return (
            "Ты старший юрист РФ. Перед тобой исходный запрос и расшифровка уточняющих вопросов/ответов. "
            "Оцени, достаточно ли материала для подготовки профессионально пригодного черновика именно того документа, "
            "который подразумевается запросом (не занижай и не завышай планку без оснований).\n\n"
            "Критерий ready=true: по совокупности текста можно составить связный скелет документа без «фантазирования» смысловых блоков "
            "(стороны/адресаты идентифицируемы, предмет и правовая задача понятны, есть минимум для расчёта/сроков/процедуры там, где они обычно "
            "существенны). Допустимы подстановки-линии для реквизитов, если запрос это предполагает.\n\n"
            "Критерий ready=false: отсутствует существенное для выбранной конструкции (например: для договора не ясен предмет или вознаграждение; "
            "для иска — нет ответчика/подсудности при известном типе спора; для претензии — нет адресата или неясна сумма требования и т.п.). "
            "Тогда дай 1–4 узких вопроса в follow_up_questions — как на допросе у партнёра, без воды и без дублирования уже данных.\n\n"
            "Не выдумывай факты. reason_short — одно чёткое предложение: готовность или конкретный пробел.\n"
            "Верни строго JSON: {\"ready\": true, \"reason_short\": \"\", \"follow_up_questions\": []}"
        )

    async def assess_document_readiness(self, request_text: str, qa_transcript: str) -> DocumentReadinessResult:
        block = (
            "Исходный запрос пользователя:\n"
            f"{request_text.strip()}\n\n"
            "Диалог уточнений (вопрос — ответ):\n"
            f"{qa_transcript.strip()}\n"
        )
        payload = await self._chat_json(
            [
                {"role": "system", "content": self._document_readiness_system_prompt()},
                {"role": "user", "content": block},
            ],
            temperature=0.06,
            max_tokens=2400,
        )
        try:
            raw = DocumentReadinessResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректную оценку готовности: {exc}") from exc
        follow = raw.follow_up_questions
        if raw.ready:
            follow = []
        elif not follow:
            follow = [
                "Кратко перечислите недостающие сведения одним сообщением (стороны, предмет, суммы, сроки, регион, реквизиты — что применимо).",
            ]
        return DocumentReadinessResult.model_validate(
            {"ready": raw.ready, "reason_short": raw.reason_short.strip()[:600], "follow_up_questions": follow}
        )

    def _infer_dynamic_document_kind(self, request_text: str, details_text: str) -> str:
        """Черновой вид документа для генерации (не для юридической квалификации)."""
        req = (request_text or "").strip().lower()
        det = (details_text or "").strip().lower()
        full = f"{request_text or ''}\n{details_text or ''}".lower()

        contract = self._user_wants_contract_draft(full)
        litigation = self._user_wants_litigation_route(full)

        if contract and litigation:
            if self._user_wants_contract_draft(req) and not self._user_wants_litigation_route(req):
                return "contract"
            if self._user_wants_litigation_route(req) and not self._user_wants_contract_draft(req):
                return "litigation"
            if self._user_wants_litigation_route(det) and not self._user_wants_litigation_route(req):
                return "litigation"
            if any(k in req for k in ("договор", "аренд", "найм", "соглашен", "купл", "займ")):
                return "contract"
            return "litigation"

        if contract:
            return "contract"
        if litigation:
            return "litigation"
        if re.search(r"претенз", full):
            return "pretrial"
        if re.search(
            r"(заявлен\w+\s+в|жалоб\w+\s+в\s+(?!суд)|обращен\w+\s+в\s+(?!суд))",
            full,
        ) and "исков" not in full:
            return "application"
        return "generic"

    def _dynamic_document_system_prompt(self, kind: str, current_date: str) -> str:
        common = (
            "Ты ведущий практикующий юрист РФ по договорной и процессуальной работе. Готовь документ уровня «для подписи с доработкой юрслужбой», "
            "а не учебный конспект. Стиль: сухой, плотный, без лирики и без лозунгов; логика как у старшего консультанта.\n"
            f"Текущая дата: {current_date}.\n"
            "Источник фактов — только запрос пользователя и блок уточнений. Не придумывай стороны, суммы, даты, суды, номера дел, исходы споров, "
            "почтовые адреса и телефоны «от себя». При пробелах — строки «________________». Не используй англоязычные скобки [ ].\n"
            "Ссылайся на нормы права осторожно: указывай статьи типовых актов (ГК, ГПК, АПК, ЗоЗПП, ТК и т.д.) только там, где это стандартно и по смыслу "
            "вытекает из фактов; не приписывай пользователю нормы, которых он не касался. Если норма неочевидна — «в соответствии с применимыми нормами "
            "...» без выдуманных номеров.\n"
            "Структура JSON строго: header (массив строк), title, subtitle, body (абзацы по смыслу), requests, attachments, date_and_signature, instruction. "
            "Пустые разделы — [] или \"\".\n"
        )

        by_kind = {
            "contract": (
                "ВИД: договор / соглашение (не процессуальный акт).\n"
                "ЗАПРЕТ: не использовать «ИСКОВОЕ ЗАЯВЛЕНИЕ» и иные заголовки судебных актов; не вставлять ПРОШУ к суду. requests всегда [].\n"
                "title — заголовок договора ЗАГЛАВНЫМИ по классификации запроса. subtitle — краткое отражение предмета.\n"
                "header — «г. …» при наличии; стороны с ролями и тем, что есть из текста (ФИО, наименование, ОГРН/ИНН если даны, адреса); пустые строки для визуальных интервалов.\n"
                "body — последовательность: преамбула и термины при необходимости; предмет; срок действия; цена/вознаграждение и расчёты; передача/исполнение; "
                "права и обязанности сторон; ответственность и штрафы; расторжение и уведомления; урегулирование споров по договору (подсудность/медиация/АС — если уместно из текста); "
                "заключительные положения; приложения к договору — перечисли текстом в body при необходимости. Абзацы короткие и нумеруемые формулировки допустимы внутри body как часть текста.\n"
                "attachments — список вероятных приложений, если пользователь на них намекнул; иначе [].\n"
                "instruction для пользователя: порядок подписания, количество экземпляров, кому передать, нужна ли госрегистрация/нотариат — только если следует из фактов или из типовой практики без выдумки конкретики.\n"
            ),
            "litigation": (
                "ВИД: судебный или иной процессуальный документ.\n"
                "header: суд/судья при наличии в материалах; истец/заявитель, ответчик/иные участники; индексы, адреса — только из текста.\n"
                "title/subtitle: по типу и предмету из запроса.\n"
                "body: изложение фактов в хронологии; мотивировка со ссылками на процессуальные и материальные нормы там, где это стандартно для данного типа требований; "
                "разграничивай установленные факты и правовую оценку.\n"
                "requests — пронумерованные требования к суду исходя из текста пользователя.\n"
                "attachments — доказательства, на которые опирается пользователь, плюс типовые приложения если логично из текста.\n"
                "instruction: перспектива подачи (суд, копии, пошлина если применимо, сопровождение) — без выдуманных реквизитов суда.\n"
            ),
            "pretrial": (
                "ВИД: досудебное требование / претензия.\n"
                "Не оформляй как иск; title — ПРЕТЕНЗИЯ или иная адекватная форма.\n"
                "header: отправитель и получатель с теми реквизитами, что есть в тексте.\n"
                "body: фабула, правовое обоснование в общих чертах по смыслу дела, требования со сроком добровольного исполнения, ссылки на первичные доказательства.\n"
                "requests — конкретные требования к контрагенту пунктами, если удобно отделить от body; иначе пусто.\n"
                "instruction: как и куда направить для фиксации доставки (заказное, email если указан).\n"
            ),
            "application": (
                "ВИД: заявление или жалоба в госорган/муниципалитет (не суд).\n"
                "header: орган и заявитель по имеющимся данным.\n"
                "body: юридически выверенная просьба с изложением оснований из текста пользователя.\n"
                "requests — нумерованный перечень просьб.\n"
                "instruction: порядок подачи при известности из текста.\n"
            ),
            "generic": (
                "Сначала определи по совокупности запроса и уточнений фактический вид документа и оформи его по соответствующим правилам. "
                "Не подставляй иск или суд туда, где пользователь просит договор или претензию.\n"
            ),
        }
        return common + by_kind.get(kind, by_kind["generic"])

    def _dynamic_document_user_message(self, kind: str, request_text: str, details_text: str) -> str:
        labels = {
            "contract": "договор или соглашение (НЕ иск, НЕ заявление в суд)",
            "litigation": "судебный (процессуальный) документ",
            "pretrial": "претензия или иное досудебное требование",
            "application": "заявление/жалоба в орган (не в суд)",
            "generic": "документ в соответствии с формулировкой запроса (сначала определи вид)",
        }
        return (
            f"Задача генерации черновика: {kind} — ожидаемый тип результата: {labels.get(kind, labels['generic'])}.\n"
            "Не смешивай конструкции (договор ≠ иск). Используй только факты из блоков ниже.\n\n"
            f"Исходный запрос пользователя:\n{request_text.strip()}\n\n"
            f"Собранные уточнения и ответы:\n{details_text.strip()}"
        )

    async def generate_dynamic_document(self, request_text: str, details_text: str) -> DynamicDocumentResult:
        from datetime import datetime
        current_date = datetime.now().strftime("%d.%m.%Y")
        kind = self._infer_dynamic_document_kind(request_text, details_text)
        system_prompt = self._dynamic_document_system_prompt(kind, current_date)
        user_message = self._dynamic_document_user_message(kind, request_text, details_text)
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": user_message},
            ],
            temperature=0.14,
            max_tokens=8192,
            read_seconds=float(max(120, self.settings.deepseek_generation_timeout_seconds)),
        )
        try:
            raw = DynamicDocumentResult.model_validate(payload)
        except ValidationError as exc:
            raise DeepSeekError(f"DeepSeek вернул некорректный документ: {exc}") from exc
        return self._sanitize_dynamic_document_output(kind, raw, request_text)

    def _dynamic_contract_title_guess(self, request_text: str) -> str:
        r = request_text.strip().lower()
        if "найм" in r and ("жил" in r or "квартир" in r):
            return "ДОГОВОР НАЙМА ЖИЛОГО ПОМЕЩЕНИЯ"
        if "аренд" in r and "нежил" in r:
            return "ДОГОВОР АРЕНДЫ НЕЖИЛОГО ПОМЕЩЕНИЯ"
        if "аренд" in r or "найм" in r:
            return "ДОГОВОР АРЕНДЫ"
        if "купл" in r or "продаж" in r:
            return "ДОГОВОР КУПЛИ-ПРОДАЖИ"
        if "займ" in r:
            return "ДОГОВОР ЗАЙМА"
        if "подряд" in r:
            return "ДОГОВОР ПОДРЯДА"
        if "услуг" in r:
            return "ДОГОВОР ОКАЗАНИЯ УСЛУГ"
        if "договор" in r:
            return "ДОГОВОР"
        return "ДОГОВОР"

    def _sanitize_dynamic_document_output(
        self, kind: str, result: DynamicDocumentResult, request_text: str
    ) -> DynamicDocumentResult:
        """Для договора: не допускаем блока требований к суду; исправляем типичные «исковые» заголовки."""
        if kind != "contract":
            return result
        tl = (result.title or "").upper()
        looks_like_claim = any(
            s in tl
            for s in (
                "ИСКОВОЕ",
                "ИСКОВЫЙ",
                "ИСК ",
                "ЗАЯВЛЕНИЕ О ПРИЗНАНИИ",
                "ЗАЯВЛЕНИЕ О ВОЗБУЖДЕНИИ",
                "О ВЗЫСКАНИИ",
                "ВЗЫСКАТЬ",
            )
        )
        updates: dict[str, Any] = {"requests": []}
        if looks_like_claim:
            updates["title"] = self._dynamic_contract_title_guess(request_text)
        return result.model_copy(update=updates)

    def _fill_system_prompt(self, template: TemplateMeta) -> str:
        from datetime import datetime
        current_date = datetime.now().strftime("%d.%m.%Y")
        fields = ", ".join(field.key for field in template.fields)
        return (
            "Ты нормализуешь ответы пользователя для заполнения юридического шаблона в формулировках делового документа. "
            "Не добавляй новые юридические блоки и не меняй смысл. "
            "Не выдумывай отсутствующие факты. Если пользователь ввел неясно, аккуратно приведи к официальному стилю. "
            f"Текущая дата: {current_date}. Если пользователь пишет 'вчера', 'сегодня', 'завтра', переведи это в точную дату. "
            "Даты должны быть в формате ДД.ММ.ГГГГ. Суммы должны быть числами (или числами с указанием валюты). "
            f"Документ: {template.title}. Допустимые поля: {fields}. "
            "Верни строго JSON: {\"values\": {field: value}, \"instruction\": \"краткая инструкция\"}."
        )
