"""PDF order receipt, attached to the 'order confirmed' e-mail."""
import os
from fpdf import FPDF
from fpdf.enums import XPos, YPos

MAROON = (107, 29, 20)
GOLD = (212, 160, 23)
FSSAI_LICENSE = os.getenv('FSSAI_LICENSE', '21521275000514').strip()


def _rupees(paise):
    return f"Rs {(paise or 0) / 100:,.2f}"


def build_receipt_pdf(order):
    """Returns the receipt as PDF bytes for the given SpiceOrder."""
    pdf = FPDF(format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_fill_color(*MAROON)
    pdf.rect(0, 0, 210, 28, style='F')
    pdf.set_text_color(*GOLD)
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_xy(10, 8)
    pdf.cell(0, 12, 'Heritage Spices', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_text_color(30, 30, 30)
    pdf.set_xy(10, 36)
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 8, 'Order Receipt', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font('Helvetica', '', 10)
    created = order.created_at.strftime('%d %b %Y, %I:%M %p') if order.created_at else ''
    pdf.cell(0, 6, f'Order number: {order.order_number}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f'Order date: {created}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f'FSSAI licence: {FSSAI_LICENSE}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 6, 'Delivering to', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 6, order.full_name, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.multi_cell(0, 6, order.address)
    pdf.cell(0, 6, f'{order.city}, {order.state} - {order.pincode}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f'Phone: {order.phone}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_fill_color(*MAROON)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(90, 8, 'Item', border=0, fill=True)
    pdf.cell(25, 8, 'Qty', border=0, fill=True, align='R')
    pdf.cell(37, 8, 'Unit price', border=0, fill=True, align='R')
    pdf.cell(38, 8, 'Line total', border=0, fill=True, align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_text_color(30, 30, 30)
    pdf.set_font('Helvetica', '', 10)
    for item in order.items:
        pdf.cell(90, 8, item.product_name, border='B')
        pdf.cell(25, 8, str(item.quantity), border='B', align='R')
        pdf.cell(37, 8, _rupees(item.unit_price), border='B', align='R')
        pdf.cell(38, 8, _rupees(item.unit_price * item.quantity), border='B', align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(2)
    pdf.set_font('Helvetica', '', 10)
    totals = [('Subtotal', order.subtotal), ('Shipping', order.shipping_cost)]
    if order.discount_amount:
        totals.append(('Discount', -order.discount_amount))
    if order.points_discount_amount:
        totals.append(('Points redeemed', -order.points_discount_amount))
    for label, paise in totals:
        pdf.cell(152, 6, label, align='R')
        pdf.cell(38, 6, _rupees(paise), align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(152, 8, 'Total paid', align='R')
    pdf.cell(38, 8, _rupees(order.total_amount), align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(6)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, 'Heritage Spices Pvt. Ltd., Sindewahi, Maharashtra 441222. '
                          'Questions about this order? Reply to your confirmation e-mail.')

    return bytes(pdf.output())
