import json
from pathlib import Path

from docx import Document as DocxDocument

from app.schemas.ai import TemplateField, TemplateMeta


BUILTIN_TEMPLATES: list[dict] = [
    {
        "document_type": "consumer_refund_claim",
        "title": "Претензия продавцу о возврате денежных средств",
        "category": "Жалобы",
        "filename": "claims/consumer_refund_claim.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Введите ваши ФИО полностью."},
            {"key": "address", "label": "Адрес", "question": "Введите ваш адрес для документа."},
            {"key": "seller", "label": "Продавец", "question": "Введите название продавца/магазина."},
            {"key": "product", "label": "Товар", "question": "Какой товар или услуга спорные?"},
            {"key": "amount", "label": "Сумма", "question": "Введите сумму требования в рублях."},
            {"key": "purchase_date", "label": "Дата покупки", "question": "Введите дату покупки."},
            {"key": "problem", "label": "Проблема", "question": "Кратко опишите, что произошло."},
        ],
        "body": [
            "ПРЕТЕНЗИЯ",
            "Я, {{ fio }}, проживающий(ая) по адресу: {{ address }}, приобрел(а) у {{ seller }} товар/услугу: {{ product }}.",
            "Дата покупки: {{ purchase_date }}. Сумма требования: {{ amount }} руб.",
            "Суть нарушения: {{ problem }}.",
            "Прошу вернуть денежные средства в установленный законом срок и письменно сообщить о принятом решении.",
        ],
        "instruction": "Передайте претензию продавцу под отметку о получении либо направьте заказным письмом. Сохраните чек, переписку и копию претензии.",
    },
    {
        "document_type": "rospotrebnadzor_complaint",
        "title": "Жалоба в Роспотребнадзор",
        "category": "Жалобы",
        "filename": "complaints/rospotrebnadzor_complaint.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Введите ваши ФИО."},
            {"key": "address", "label": "Адрес", "question": "Введите ваш адрес."},
            {"key": "organization", "label": "Организация", "question": "На кого жалуетесь?"},
            {"key": "problem", "label": "Нарушение", "question": "Опишите нарушение."},
            {"key": "request", "label": "Просьба", "question": "Что просите проверить или сделать?"},
        ],
        "body": [
            "ЖАЛОБА",
            "Я, {{ fio }}, адрес: {{ address }}, сообщаю о нарушении прав потребителя со стороны {{ organization }}.",
            "Обстоятельства: {{ problem }}.",
            "Прошу провести проверку, принять меры реагирования и сообщить мне о результатах.",
            "Дополнительная просьба: {{ request }}.",
        ],
        "instruction": "Подайте жалобу через сайт Роспотребнадзора, Госуслуги или почтой. Приложите чеки, договор, фото и переписку.",
    },
    {
        "document_type": "labor_inspection_complaint",
        "title": "Жалоба работодателю/в трудовую инспекцию",
        "category": "Работа",
        "filename": "work/labor_inspection_complaint.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Введите ваши ФИО."},
            {"key": "employer", "label": "Работодатель", "question": "Введите название работодателя."},
            {"key": "position", "label": "Должность", "question": "Введите вашу должность."},
            {"key": "violation", "label": "Нарушение", "question": "Опишите нарушение работодателя."},
            {"key": "request", "label": "Требование", "question": "Что вы требуете устранить?"},
        ],
        "body": [
            "ЖАЛОБА",
            "Я, {{ fio }}, работаю в {{ employer }} в должности {{ position }}.",
            "Считаю, что работодатель нарушил мои трудовые права: {{ violation }}.",
            "Прошу провести проверку, обязать работодателя устранить нарушение и сообщить мне о результатах.",
            "Мое требование: {{ request }}.",
        ],
        "instruction": "Можно подать работодателю письменно, затем в Государственную инспекцию труда через онлайнинспекция.рф. Приложите трудовой договор, расчетные листки и переписку.",
    },
    {
        "document_type": "resignation_letter",
        "title": "Заявление на увольнение",
        "category": "Работа",
        "filename": "work/resignation_letter.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Введите ваши ФИО."},
            {"key": "employer", "label": "Работодатель", "question": "Введите название работодателя."},
            {"key": "position", "label": "Должность", "question": "Введите вашу должность."},
            {"key": "dismissal_date", "label": "Дата увольнения", "question": "С какой даты просите уволить?"},
        ],
        "body": [
            "ЗАЯВЛЕНИЕ",
            "Прошу уволить меня, {{ fio }}, с должности {{ position }} в {{ employer }} по собственному желанию с {{ dismissal_date }}.",
        ],
        "instruction": "Подайте заявление работодателю под отметку о получении. Обычно предупреждение подается за 14 календарных дней, если нет исключений.",
    },
    {
        "document_type": "explanatory_note",
        "title": "Объяснительная",
        "category": "Работа",
        "filename": "work/explanatory_note.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Введите ваши ФИО."},
            {"key": "position", "label": "Должность", "question": "Введите вашу должность/статус."},
            {"key": "event_date", "label": "Дата события", "question": "Введите дату события."},
            {"key": "explanation", "label": "Объяснение", "question": "Что нужно объяснить?"},
        ],
        "body": [
            "ОБЪЯСНИТЕЛЬНАЯ",
            "Я, {{ fio }}, {{ position }}, сообщаю следующее.",
            "{{ event_date }} произошла ситуация: {{ explanation }}.",
            "Прошу учесть изложенные обстоятельства при рассмотрении вопроса.",
        ],
        "instruction": "Передайте объяснительную адресату под отметку о получении или направьте официальным каналом организации.",
    },
    {
        "document_type": "debt_receipt",
        "title": "Расписка",
        "category": "Юридические документы",
        "filename": "legal/debt_receipt.docx",
        "fields": [
            {"key": "borrower", "label": "Заемщик", "question": "Введите ФИО заемщика."},
            {"key": "lender", "label": "Займодавец", "question": "Введите ФИО займодавца."},
            {"key": "amount", "label": "Сумма", "question": "Введите сумму займа."},
            {"key": "return_date", "label": "Дата возврата", "question": "Введите дату возврата."},
            {"key": "passport", "label": "Паспорт", "question": "Введите паспортные данные заемщика."},
        ],
        "body": [
            "РАСПИСКА",
            "Я, {{ borrower }}, паспорт: {{ passport }}, получил(а) от {{ lender }} денежные средства в размере {{ amount }} руб.",
            "Обязуюсь вернуть указанную сумму не позднее {{ return_date }}.",
        ],
        "instruction": "Расписку лучше подписать собственноручно, указать дату и место составления. Для крупных сумм проверьте паспортные данные.",
    },
    {
        "document_type": "service_contract",
        "title": "Договор оказания услуг",
        "category": "Юридические документы",
        "filename": "legal/service_contract.docx",
        "fields": [
            {"key": "customer", "label": "Заказчик", "question": "Введите ФИО/название заказчика."},
            {"key": "contractor", "label": "Исполнитель", "question": "Введите ФИО/название исполнителя."},
            {"key": "service", "label": "Услуга", "question": "Какая услуга оказывается?"},
            {"key": "price", "label": "Цена", "question": "Введите стоимость услуг."},
            {"key": "deadline", "label": "Срок", "question": "Введите срок оказания услуг."},
        ],
        "body": [
            "ДОГОВОР ОКАЗАНИЯ УСЛУГ",
            "{{ contractor }} обязуется оказать {{ customer }} следующие услуги: {{ service }}.",
            "Стоимость услуг составляет {{ price }} руб. Срок оказания услуг: {{ deadline }}.",
            "Стороны несут ответственность за неисполнение обязательств по законодательству РФ.",
        ],
        "instruction": "Подпишите договор в двух экземплярах. К договору можно приложить техническое задание и акт оказанных услуг.",
    },
    {
        "document_type": "rent_contract",
        "title": "Договор аренды",
        "category": "Юридические документы",
        "filename": "legal/rent_contract.docx",
        "fields": [
            {"key": "landlord", "label": "Арендодатель", "question": "Введите арендодателя."},
            {"key": "tenant", "label": "Арендатор", "question": "Введите арендатора."},
            {"key": "property", "label": "Объект", "question": "Опишите объект аренды."},
            {"key": "rent", "label": "Арендная плата", "question": "Введите размер арендной платы."},
            {"key": "term", "label": "Срок", "question": "Введите срок аренды."},
        ],
        "body": [
            "ДОГОВОР АРЕНДЫ",
            "{{ landlord }} передает, а {{ tenant }} принимает во временное пользование объект: {{ property }}.",
            "Арендная плата составляет {{ rent }} руб. Срок аренды: {{ term }}.",
            "Передача объекта подтверждается актом приема-передачи.",
        ],
        "instruction": "Подпишите договор и акт приема-передачи. Для недвижимости на срок от года может потребоваться регистрация.",
    },
    {
        "document_type": "power_of_attorney",
        "title": "Доверенность",
        "category": "Юридические документы",
        "filename": "legal/power_of_attorney.docx",
        "fields": [
            {"key": "principal", "label": "Доверитель", "question": "Введите ФИО доверителя."},
            {"key": "representative", "label": "Представитель", "question": "Введите ФИО представителя."},
            {"key": "powers", "label": "Полномочия", "question": "Какие полномочия передаются?"},
            {"key": "valid_until", "label": "Срок", "question": "До какой даты действует доверенность?"},
        ],
        "body": [
            "ДОВЕРЕННОСТЬ",
            "Я, {{ principal }}, доверяю {{ representative }} совершать от моего имени следующие действия: {{ powers }}.",
            "Доверенность действует до {{ valid_until }}.",
        ],
        "instruction": "Для ряда действий доверенность нужно нотариально удостоверить. Проверьте требования организации, куда она подается.",
    },
    {
        "document_type": "notice",
        "title": "Уведомление",
        "category": "Бизнес",
        "filename": "business/notice.docx",
        "fields": [
            {"key": "sender", "label": "Отправитель", "question": "Введите отправителя."},
            {"key": "recipient", "label": "Получатель", "question": "Введите получателя."},
            {"key": "subject", "label": "Тема", "question": "О чем уведомление?"},
            {"key": "message", "label": "Текст", "question": "Что нужно сообщить?"},
        ],
        "body": [
            "УВЕДОМЛЕНИЕ",
            "{{ sender }} уведомляет {{ recipient }} по вопросу: {{ subject }}.",
            "{{ message }}.",
            "Просим принять указанную информацию к сведению.",
        ],
        "instruction": "Направьте уведомление способом, который позволяет подтвердить отправку и получение: заказное письмо, курьер, ЭДО или подпись на копии.",
    },
    {
        "document_type": "gift_contract",
        "title": "Договор дарения",
        "category": "Юридические документы",
        "filename": "legal/gift_contract.docx",
        "fields": [
            {"key": "donor", "label": "Даритель", "question": "Введите ФИО дарителя."},
            {"key": "donee", "label": "Одаряемый", "question": "Введите ФИО одаряемого."},
            {"key": "property", "label": "Предмет дарения", "question": "Что передается в дар?"},
            {"key": "value", "label": "Оценочная стоимость", "question": "Оценочная стоимость дара (в рублях)."},
        ],
        "body": [
            "ДОГОВОР ДАРЕНИЯ",
            "Даритель {{ donor }} безвозмездно передает в собственность Одаряемому {{ donee }} следующее имущество: {{ property }}.",
            "Оценочная стоимость передаваемого имущества составляет {{ value }} руб.",
            "Одаряемый принимает дар с благодарностью.",
        ],
        "instruction": "Подпишите договор в двух экземплярах. Для недвижимости требуется государственная регистрация перехода права собственности.",
    },
    {
        "document_type": "loan_agreement",
        "title": "Договор займа",
        "category": "Юридические документы",
        "filename": "legal/loan_agreement.docx",
        "fields": [
            {"key": "lender", "label": "Займодавец", "question": "Введите ФИО займодавца."},
            {"key": "borrower", "label": "Заемщик", "question": "Введите ФИО заемщика."},
            {"key": "amount", "label": "Сумма", "question": "Введите сумму займа."},
            {"key": "interest_rate", "label": "Процентная ставка", "question": "Укажите процентную ставку (или 'без процентов')."},
            {"key": "return_date", "label": "Дата возврата", "question": "Введите дату возврата."},
        ],
        "body": [
            "ДОГОВОР ЗАЙМА",
            "Займодавец {{ lender }} передает в собственность Заемщику {{ borrower }} денежные средства в размере {{ amount }} руб.",
            "Процентная ставка по займу: {{ interest_rate }}.",
            "Заемщик обязуется возвратить сумму займа не позднее {{ return_date }}.",
        ],
        "instruction": "Подпишите договор. Рекомендуется составить расписку о фактической передаче денежных средств.",
    },
    {
        "document_type": "divorce_claim",
        "title": "Исковое заявление о расторжении брака",
        "category": "Семья",
        "filename": "family/divorce_claim.docx",
        "fields": [
            {"key": "plaintiff", "label": "Истец", "question": "ФИО истца (кто подает)."},
            {"key": "defendant", "label": "Ответчик", "question": "ФИО ответчика (второй супруг)."},
            {"key": "marriage_date", "label": "Дата брака", "question": "Когда был заключен брак?"},
            {"key": "children", "label": "Дети", "question": "Есть ли общие несовершеннолетние дети? (укажите ФИО и даты рождения или 'нет')."},
            {"key": "reason", "label": "Причина", "question": "Причина расторжения брака (кратко)."},
        ],
        "body": [
            "ИСКОВОЕ ЗАЯВЛЕНИЕ О РАСТОРЖЕНИИ БРАКА",
            "Истец: {{ plaintiff }}. Ответчик: {{ defendant }}.",
            "Брак между сторонами зарегистрирован {{ marriage_date }}.",
            "Общие несовершеннолетние дети: {{ children }}.",
            "Дальнейшая совместная жизнь стала невозможной по причине: {{ reason }}.",
            "Прошу расторгнуть брак между Истцом и Ответчиком.",
        ],
        "instruction": "Подайте исковое заявление в мировой суд (если нет спора о детях) или в районный суд. Оплатите госпошлину.",
    },
    {
        "document_type": "alimony_claim",
        "title": "Исковое заявление о взыскании алиментов",
        "category": "Семья",
        "filename": "family/alimony_claim.docx",
        "fields": [
            {"key": "plaintiff", "label": "Истец", "question": "ФИО истца."},
            {"key": "defendant", "label": "Ответчик", "question": "ФИО ответчика."},
            {"key": "child_info", "label": "Ребенок", "question": "ФИО и дата рождения ребенка."},
            {"key": "amount_requested", "label": "Размер алиментов", "question": "Какую долю заработка или твердую сумму просите?"},
        ],
        "body": [
            "ИСКОВОЕ ЗАЯВЛЕНИЕ О ВЗЫСКАНИИ АЛИМЕНТОВ",
            "Истец: {{ plaintiff }}. Ответчик: {{ defendant }}.",
            "Ребенок: {{ child_info }} находится на иждивении Истца.",
            "Ответчик материальной помощи на содержание ребенка не оказывает.",
            "Прошу взыскать с Ответчика алименты в размере: {{ amount_requested }}.",
        ],
        "instruction": "Подайте заявление в мировой суд по месту жительства истца или ответчика. Госпошлина по таким искам не уплачивается истцом.",
    },
    {
        "document_type": "apartment_acceptance_act",
        "title": "Акт приема-передачи квартиры",
        "category": "Недвижимость",
        "filename": "real_estate/apartment_acceptance_act.docx",
        "fields": [
            {"key": "transferor", "label": "Передающая сторона", "question": "ФИО передающего."},
            {"key": "receiver", "label": "Принимающая сторона", "question": "ФИО принимающего."},
            {"key": "address", "label": "Адрес квартиры", "question": "Точный адрес квартиры."},
            {"key": "condition", "label": "Состояние", "question": "Опишите состояние квартиры (например, 'хорошее, без дефектов')."},
            {"key": "meters", "label": "Показания счетчиков", "question": "Укажите показания счетчиков (вода, электричество)."},
        ],
        "body": [
            "АКТ ПРИЕМА-ПЕРЕДАЧИ КВАРТИРЫ",
            "{{ transferor }} передал, а {{ receiver }} принял квартиру по адресу: {{ address }}.",
            "Состояние квартиры: {{ condition }}. Претензий стороны не имеют.",
            "Показания приборов учета на момент передачи: {{ meters }}.",
            "Ключи от квартиры переданы в полном объеме.",
        ],
        "instruction": "Подпишите акт в двух экземплярах. Он является неотъемлемой частью договора купли-продажи или аренды.",
    },
    {
        "document_type": "complaint_prosecutor",
        "title": "Жалоба в прокуратуру",
        "category": "Жалобы",
        "filename": "complaints/complaint_prosecutor.docx",
        "fields": [
            {"key": "fio", "label": "ФИО заявителя", "question": "Ваши ФИО."},
            {"key": "address", "label": "Адрес", "question": "Ваш адрес для ответа."},
            {"key": "violator", "label": "Нарушитель", "question": "Кто нарушил ваши права?"},
            {"key": "violation_desc", "label": "Суть нарушения", "question": "Подробно опишите, в чем заключается нарушение закона."},
            {"key": "request", "label": "Просьба", "question": "Что вы просите сделать прокуратуру?"},
        ],
        "body": [
            "ЖАЛОБА В ПРОКУРАТУРУ",
            "От: {{ fio }}, проживающего по адресу: {{ address }}.",
            "Сообщаю о нарушении законодательства со стороны: {{ violator }}.",
            "Суть нарушения: {{ violation_desc }}.",
            "На основании изложенного, прошу провести прокурорскую проверку и {{ request }}.",
        ],
        "instruction": "Подайте жалобу лично, по почте или через портал Госуслуг. Срок рассмотрения обычно составляет 30 дней.",
    },
    {
        "document_type": "claim_insurance",
        "title": "Претензия в страховую компанию",
        "category": "Жалобы",
        "filename": "claims/claim_insurance.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Ваши ФИО."},
            {"key": "insurance_company", "label": "Страховая компания", "question": "Название страховой компании."},
            {"key": "policy_number", "label": "Номер полиса", "question": "Номер вашего страхового полиса."},
            {"key": "event_desc", "label": "Страховой случай", "question": "Кратко опишите страховой случай."},
            {"key": "claim_amount", "label": "Сумма требований", "question": "Сумма, которую вы требуете выплатить."},
        ],
        "body": [
            "ПРЕТЕНЗИЯ В СТРАХОВУЮ КОМПАНИЮ",
            "От: {{ fio }}. Страховая компания: {{ insurance_company }}. Полис № {{ policy_number }}.",
            "Произошел страховой случай: {{ event_desc }}.",
            "Считаю отказ в выплате / размер выплаты необоснованным.",
            "Требую выплатить страховое возмещение в размере {{ claim_amount }} руб. в установленный законом срок.",
        ],
        "instruction": "Направьте претензию заказным письмом с описью вложения или вручите лично под роспись. При отказе обращайтесь к финансовому уполномоченному.",
    },
    {
        "document_type": "vacation_application",
        "title": "Заявление на отпуск",
        "category": "Работа",
        "filename": "work/vacation_application.docx",
        "fields": [
            {"key": "fio", "label": "ФИО", "question": "Ваши ФИО."},
            {"key": "position", "label": "Должность", "question": "Ваша должность."},
            {"key": "start_date", "label": "Дата начала", "question": "С какого числа отпуск?"},
            {"key": "duration", "label": "Продолжительность", "question": "На сколько календарных дней?"},
        ],
        "body": [
            "ЗАЯВЛЕНИЕ НА ОТПУСК",
            "От {{ position }} {{ fio }}.",
            "Прошу предоставить мне ежегодный оплачиваемый отпуск с {{ start_date }} продолжительностью {{ duration }} календарных дней.",
        ],
        "instruction": "Заявление обычно подается за 2 недели до начала отпуска. Отпускные должны быть выплачены не позднее чем за 3 дня до начала отпуска.",
    },
    {
        "document_type": "freelance_contract",
        "title": "Договор с фрилансером (ГПХ)",
        "category": "Бизнес",
        "filename": "business/freelance_contract.docx",
        "fields": [
            {"key": "customer", "label": "Заказчик", "question": "ФИО или название компании Заказчика."},
            {"key": "freelancer", "label": "Фрилансер", "question": "ФИО фрилансера."},
            {"key": "task", "label": "Задача", "question": "Какую работу нужно выполнить?"},
            {"key": "reward", "label": "Вознаграждение", "question": "Размер вознаграждения."},
            {"key": "deadline", "label": "Срок", "question": "Срок выполнения работ."},
        ],
        "body": [
            "ДОГОВОР ГРАЖДАНСКО-ПРАВОВОГО ХАРАКТЕРА",
            "Заказчик {{ customer }} поручает, а Исполнитель {{ freelancer }} принимает на себя обязательство выполнить следующие работы: {{ task }}.",
            "Срок выполнения работ: {{ deadline }}.",
            "Вознаграждение за выполненную работу составляет {{ reward }} руб.",
            "Оплата производится после подписания Акта приема-передачи выполненных работ.",
        ],
        "instruction": "Подпишите договор до начала работ. Если фрилансер самозанятый, он должен выдать вам чек после оплаты.",
    },
]


class TemplateCatalog:
    def __init__(self, root: Path):
        self.root = root
        self._templates: dict[str, TemplateMeta] = {}

    def load(self) -> "TemplateCatalog":
        self.root.mkdir(parents=True, exist_ok=True)
        self._seed_metadata()
        self._templates = {}
        for path in self.root.glob("**/metadata.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            meta = TemplateMeta.model_validate(raw)
            self._templates[meta.document_type] = meta
        self.ensure_docx_templates()
        return self

    def _seed_metadata(self) -> None:
        for template in BUILTIN_TEMPLATES:
            metadata_path = self.root / Path(template["filename"]).with_suffix("") / "metadata.json"
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if not metadata_path.exists():
                metadata_path.write_text(
                    json.dumps(template, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    def ensure_docx_templates(self) -> None:
        for meta in self._templates.values():
            path = self.path_for(meta)
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            doc = DocxDocument()
            for paragraph in meta.body:
                doc.add_paragraph(paragraph)
            doc.save(path)

    def get(self, document_type: str) -> TemplateMeta:
        return self._templates[document_type]

    def find(self, document_type: str | None) -> TemplateMeta | None:
        if not document_type:
            return None
        return self._templates.get(document_type)

    def all(self) -> list[TemplateMeta]:
        return sorted(self._templates.values(), key=lambda item: (item.category, item.title))

    def categories(self) -> list[str]:
        return sorted({template.category for template in self._templates.values()})

    def by_category(self, category: str) -> list[TemplateMeta]:
        return [template for template in self.all() if template.category == category]

    def path_for(self, meta: TemplateMeta) -> Path:
        return self.root / meta.filename

    def match_by_title(self, title: str | None) -> TemplateMeta | None:
        if not title:
            return None
        lowered = title.lower()
        for template in self._templates.values():
            if template.title.lower() in lowered or lowered in template.title.lower():
                return template
        return None
