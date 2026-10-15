"""Печатная форма ТТН. Макет упрощённый — финальную форму переносим из текущей системы."""

import io
import os
from dataclasses import dataclass

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.modules.shipments.models import Operation, Shipment
from app.modules.shipments.quantities import fmt_liters, fmt_tonnes

FONT = "GSMSans"
_FONT_CANDIDATES = [
    os.environ.get("GSM_PDF_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_font_ready = False


def _ensure_font() -> None:
    global _font_ready
    if _font_ready:
        return
    for path in _FONT_CANDIDATES:
        if path and os.path.exists(path):
            pdfmetrics.registerFont(TTFont(FONT, path))
            _font_ready = True
            return
    raise RuntimeError("Не найден TTF-шрифт с кириллицей (GSM_PDF_FONT)")


@dataclass(frozen=True)
class PrintContext:
    warehouse_name: str
    destination_name: str | None
    verify_url: str | None  # None — предпросмотр без QR, не является выпуском
    show_personal: bool = True


def render(sh: Shipment, ctx: PrintContext) -> bytes:
    _ensure_font()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 20 * mm

    def line(label: str, value, size: int = 10) -> None:
        nonlocal y
        c.setFont(FONT, size)
        c.drawString(20 * mm, y, f"{label}: {value if value not in (None, '') else '—'}")
        y -= 7 * mm

    c.setFont(FONT, 14)
    title = f"ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ № {sh.number or 'б/н'}"
    c.drawString(20 * mm, y, title)
    y -= 6 * mm
    c.setFont(FONT, 9)
    c.drawString(20 * mm, y, f"Редакция {sh.current_revision_no}")
    if ctx.verify_url is None:
        c.setFont(FONT, 28)
        c.setFillGray(0.85)
        c.drawCentredString(w / 2, h / 2, "ПРЕДПРОСМОТР — НЕ ДЕЙСТВИТЕЛЕН")
        c.setFillGray(0)
    y -= 10 * mm

    line("Дата отгрузки", sh.shipment_date.strftime("%d.%m.%Y") if sh.shipment_date else None)
    line("Склад отправления", ctx.warehouse_name)
    if sh.operation == Operation.TRANSFER:
        line("Склад назначения", ctx.destination_name)
        line("Основание", "Перемещение")
    else:
        line("Заказчик", f"{sh.customer_name} (ИНН/ПИНФЛ {sh.customer_tin})")
        line("Грузополучатель", f"{sh.consignee_name} (ИНН/ПИНФЛ {sh.consignee_tin})")
        line("Договор", f"№ {sh.contract_number} от {_d(sh.contract_date)}")
        line("Доверенность", f"№ {sh.poa_number} от {_d(sh.poa_date)}")
    line("Автомобиль", sh.vehicle_plate)
    if ctx.show_personal:
        line("ПИНФЛ водителя", sh.driver_pinfl)
    y -= 4 * mm
    line("Топливо", sh.fuel_name, 11)
    line("Объём, л", fmt_liters(sh.volume_ml) if sh.volume_ml else None, 11)
    line("Плотность, кг/л", sh.density, 11)
    line("Масса, т", fmt_tonnes(sh.mass_g) if sh.mass_g else None, 11)
    if sh.note:
        line("Примечание", sh.note)

    y -= 15 * mm
    c.setFont(FONT, 10)
    c.drawString(20 * mm, y, "Отпустил ____________________")
    c.drawString(110 * mm, y, "Получил ____________________")

    if ctx.verify_url:
        img = qrcode.make(ctx.verify_url, box_size=4, border=1)
        ib = io.BytesIO()
        img.save(ib, format="PNG")
        ib.seek(0)
        c.drawImage(ImageReader(ib), w - 50 * mm, h - 55 * mm, 35 * mm, 35 * mm)
    c.showPage()
    c.save()
    return buf.getvalue()


def _d(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"
