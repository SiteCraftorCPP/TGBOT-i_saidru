from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Consultation,
    Document,
    DocumentStatus,
    Payment,
    PaymentKind,
    PaymentStatus,
    User,
)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, telegram_id: int, username: str | None) -> User:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user:
            if user.username != username:
                user.username = username
            return user
        user = User(telegram_id=telegram_id, username=username)
        self.session.add(user)
        await self.session.flush()
        return user

    async def delete_user_data(self, telegram_id: int) -> None:
        await self.session.execute(delete(User).where(User.telegram_id == telegram_id))
        await self.session.flush()

    async def count_all(self, *, exclude_telegram_ids: Sequence[int] | None = None) -> int:
        """Сколько строк в users; при exclude_telegram_ids — без этих Telegram id (например админы)."""
        stmt = select(func.count()).select_from(User)
        if exclude_telegram_ids:
            stmt = stmt.where(User.telegram_id.notin_(tuple(exclude_telegram_ids)))
        r = await self.session.execute(stmt)
        return int(r.scalar_one() or 0)

    async def extend_subscription_month(self, user: User) -> None:
        """+30 календарных дней от max(сейчас, subscription_until) — продление суммируется до истечения."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        base = user.subscription_until
        if base is not None and base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        start = base if base and base > now else now
        user.subscription_until = start + timedelta(days=30)
        await self.session.flush()


class ConsultationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        problem_text: str,
        category: str,
        consultation_text: str,
        risks: str | None,
        next_steps: str | None,
        recommended_document: str | None,
        document_type: str | None,
        raw_ai_json: dict[str, Any],
    ) -> Consultation:
        consultation = Consultation(
            user_id=user_id,
            problem_text=problem_text,
            category=category,
            consultation_text=consultation_text,
            risks=risks,
            next_steps=next_steps,
            recommended_document=recommended_document,
            document_type=document_type,
            raw_ai_json=raw_ai_json,
        )
        self.session.add(consultation)
        await self.session.flush()
        return consultation

    async def get(self, consultation_id: int) -> Consultation | None:
        return await self.session.get(Consultation, consultation_id)


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        document_type: str,
        consultation_id: int | None = None,
        status: DocumentStatus = DocumentStatus.UNPAID,
    ) -> Document:
        document = Document(
            user_id=user_id,
            consultation_id=consultation_id,
            document_type=document_type,
            status=status.value,
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def get(self, document_id: int) -> Document | None:
        return await self.session.get(Document, document_id)

    async def list_for_user(self, user_id: int, limit: int = 10) -> Sequence[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def update_status(self, document: Document, status: DocumentStatus) -> Document:
        document.status = status.value
        await self.session.flush()
        return document

    async def save_generated(
        self,
        document: Document,
        *,
        answers_json: dict[str, Any],
        document_text: str,
        instruction_text: str,
        docx_path: str,
        pdf_path: str | None,
    ) -> Document:
        document.answers_json = answers_json
        document.document_text = document_text
        document.instruction_text = instruction_text
        document.docx_path = docx_path
        document.pdf_path = pdf_path
        document.status = DocumentStatus.GENERATED.value
        await self.session.flush()
        return document


@dataclass(frozen=True)
class PaidBuyerRow:
    telegram_id: int
    username: str | None
    payment_kind: str
    document_type: str | None
    amount: int
    currency: str
    payment_status: str
    last_payment_at: datetime


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        document_id: int | None,
        amount: int,
        currency: str = "RUB",
        payment_kind: PaymentKind = PaymentKind.DOCUMENT,
        status: PaymentStatus = PaymentStatus.PENDING,
        provider_payment_charge_id: str | None = None,
        payment_meta: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Payment:
        payment = Payment(
            user_id=user_id,
            document_id=document_id,
            amount=amount,
            currency=currency,
            payment_kind=payment_kind.value,
            status=status.value,
            provider_payment_charge_id=provider_payment_charge_id,
            payment_meta=payment_meta,
            idempotency_key=idempotency_key,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def get_payment(self, payment_id: int) -> Payment | None:
        return await self.session.get(Payment, payment_id)

    async def by_provider_charge_id(self, provider_id: str) -> Payment | None:
        r = await self.session.execute(select(Payment).where(Payment.provider_payment_charge_id == provider_id))
        return r.scalar_one_or_none()

    async def by_telegram_charge_id(self, charge_id: str) -> Payment | None:
        c = (charge_id or "").strip()
        if not c:
            return None
        r = await self.session.execute(select(Payment).where(Payment.telegram_payment_charge_id == c))
        return r.scalar_one_or_none()

    def mark_paid(self, payment: Payment) -> None:
        payment.status = PaymentStatus.PAID.value

    async def flush(self) -> None:
        await self.session.flush()

    async def count_completed_by_kind(
        self, *, exclude_telegram_ids: Sequence[int] | None = None
    ) -> tuple[int, int]:
        """
        Количество строк payments со статусом paid по виду платежа.
        Исключает платежи пользователей из exclude_telegram_ids (админы не учитываются).
        """
        st = PaymentStatus.PAID.value

        def base_doc():
            q = (
                select(func.count())
                .select_from(Payment)
                .join(User, Payment.user_id == User.id)
                .where(
                    Payment.status == st,
                    Payment.payment_kind == PaymentKind.DOCUMENT.value,
                )
            )
            if exclude_telegram_ids:
                q = q.where(User.telegram_id.notin_(tuple(exclude_telegram_ids)))
            return q

        def base_sub():
            q = (
                select(func.count())
                .select_from(Payment)
                .join(User, Payment.user_id == User.id)
                .where(
                    Payment.status == st,
                    Payment.payment_kind == PaymentKind.SUBSCRIPTION.value,
                )
            )
            if exclude_telegram_ids:
                q = q.where(User.telegram_id.notin_(tuple(exclude_telegram_ids)))
            return q

        q_doc = await self.session.execute(base_doc())
        q_sub = await self.session.execute(base_sub())
        return int(q_doc.scalar_one() or 0), int(q_sub.scalar_one() or 0)

    async def list_buyers_who_paid(
        self,
        *,
        offset: int = 0,
        limit: int = 10,
        exclude_telegram_ids: Sequence[int] | None = None,
    ) -> tuple[list[PaidBuyerRow], int]:
        """Один пользователь — одна строка: его последний (по дате) платёж со статусом paid (без админов)."""
        paid_statuses = (PaymentStatus.PAID.value,)

        rn = func.row_number().over(
            partition_by=Payment.user_id,
            order_by=Payment.created_at.desc(),
        ).label("rn")

        ranked_sel = (
            select(
                Payment.user_id,
                Payment.document_id,
                Payment.amount,
                Payment.currency,
                Payment.payment_kind,
                Payment.status,
                Payment.created_at,
                rn,
            )
            .join(User, Payment.user_id == User.id)
            .where(Payment.status.in_(paid_statuses))
        )
        if exclude_telegram_ids:
            ranked_sel = ranked_sel.where(User.telegram_id.notin_(tuple(exclude_telegram_ids)))

        ranked = ranked_sel.subquery("ranked")

        latest = (
            select(
                ranked.c.user_id,
                ranked.c.document_id,
                ranked.c.amount,
                ranked.c.currency,
                ranked.c.payment_kind,
                ranked.c.status,
                ranked.c.created_at,
            )
            .where(ranked.c.rn == 1)
            .subquery("latest")
        )

        count_q = await self.session.execute(select(func.count()).select_from(latest))
        total = int(count_q.scalar_one() or 0)

        stmt = (
            select(
                User.telegram_id,
                User.username,
                latest.c.payment_kind,
                latest.c.amount,
                latest.c.currency,
                latest.c.status,
                latest.c.created_at,
                Document.document_type,
            )
            .join(latest, User.id == latest.c.user_id)
            .outerjoin(Document, Document.id == latest.c.document_id)
            .order_by(latest.c.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows_out: list[PaidBuyerRow] = []
        for (
            telegram_id,
            username,
            kind,
            amount,
            currency,
            pst,
            ts,
            doc_type,
        ) in res.all():
            rows_out.append(
                PaidBuyerRow(
                    telegram_id=int(telegram_id),
                    username=str(username) if username is not None else None,
                    payment_kind=str(kind),
                    document_type=str(doc_type) if doc_type is not None else None,
                    amount=int(amount),
                    currency=str(currency),
                    payment_status=str(pst),
                    last_payment_at=ts,
                )
            )
        return rows_out, total
