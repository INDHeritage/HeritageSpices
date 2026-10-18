"""PDF for a paid order, attached to the 'order confirmed' e-mail: a GST tax invoice when one has been issued
for the order, otherwise the plain order receipt."""
import os
from fpdf import FPDF
from fpdf.enums import XPos, YPos

import gst_tax

MAROON = (107, 29, 20)
GOLD = (212, 160, 23)
FSSAI_LICENSE = os.getenv('FSSAI_LICENSE', '21521275000514').strip()


def _rupees(paise):
    return f"Rs {(paise or 0) / 100:,.2f}"


def _amt(paise):
    return f"{(paise or 0) / 100:,.2f}"


def _safe(text):
    """The built-in PDF fonts only know Latin-1; anything else (e.g. a name in Devanagari) becomes '?' instead of crashing."""
    return str(text or '').encode('latin-1', 'replace').decode('latin-1')


def _line(pdf, text, h=6, bold=False, size=10):
    pdf.set_font('Helvetica', 'B' if bold else '', size)
    pdf.cell(0, h, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_receipt_pdf(order, invoice=None):
    """Returns the PDF bytes for the given SpiceOrder (and its GstInvoice, if one was issued)."""
    pdf = FPDF(format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if invoice is not None:
        _tax_invoice(pdf, order, invoice)
    else:
        _plain_receipt(pdf, order)
    return bytes(pdf.output())


def _header(pdf, right_text):
    pdf.set_fill_color(*MAROON)
    pdf.rect(0, 0, 210, 28, style='F')
    pdf.set_text_color(*GOLD)
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_xy(10, 8)
    pdf.cell(110, 12, 'Heritage Spices')
    if right_text:
        pdf.set_font('Helvetica', 'B', 14)
        pdf.set_xy(120, 8)
        pdf.cell(80, 12, right_text, align='R')
    pdf.set_text_color(30, 30, 30)


def _plain_receipt(pdf, order):
    _header(pdf, '')
    pdf.set_xy(10, 36)
    _line(pdf, 'Order Receipt', h=8, bold=True, size=14)

    created = order.created_at.strftime('%d %b %Y, %I:%M %p') if order.created_at else ''
    _line(pdf, f'Order number: {order.order_number}')
    _line(pdf, f'Order date: {created}')
    _line(pdf, f'FSSAI licence: {FSSAI_LICENSE}')
    pdf.ln(4)

    _line(pdf, 'Delivering to', bold=True, size=11)
    _line(pdf, order.full_name)
    pdf.set_font('Helvetica', '', 10)
    pdf.multi_cell(0, 6, _safe(order.address), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    _line(pdf, f'{order.city}, {order.state} - {order.pincode}')
    _line(pdf, f'Phone: {order.phone}')
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
        pdf.cell(90, 8, _safe(item.product_name), border='B')
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
    pdf.multi_cell(0, 5, 'Heritage Spices, Sindewahi, Maharashtra 441222. '
                         'Questions about this order? Reply to your confirmation e-mail.')


def _tax_invoice(pdf, order, invoice):
    doc = invoice.doc
    seller, buyer, br = doc.get('seller', {}), doc.get('buyer', {}), doc.get('breakup', {})
    intra = br.get('intra', True)
    _header(pdf, 'TAX INVOICE')

    # seller (left) and invoice details (right)
    pdf.set_xy(10, 33)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(100, 6, _safe(seller.get('legal_name')), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 9)
    for txt in (f"Trade name: {seller.get('trade_name', '')}", _safe(seller.get('address')),
                f"GSTIN: {seller.get('gstin', '')}", f"FSSAI Lic. No.: {FSSAI_LICENSE}"):
        pdf.cell(100, 5, _safe(txt), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    left_end = pdf.get_y()

    pos_code = br.get('pos_code')
    pos = gst_tax.state_label(pos_code) if pos_code else _safe(br.get('place_of_supply'))
    rows = [('Invoice no.', invoice.invoice_number), ('Invoice date', invoice.invoice_date.strftime('%d %b %Y')),
            ('Order no.', doc.get('order_number', order.order_number)), ('Place of supply', pos),
            ('Reverse charge', 'No')]
    pdf.set_xy(115, 33)
    for k, v in rows:
        pdf.set_x(115)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(30, 5, k)
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(55, 5, _safe(v), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(max(left_end, pdf.get_y()) + 3)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'Billed and shipped to', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 9)
    pdf.cell(0, 5, _safe(buyer.get('name')), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.multi_cell(120, 5, _safe(buyer.get('address')), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, _safe(f"{buyer.get('city', '')}, {buyer.get('state', '')} - {buyer.get('pincode', '')}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, _safe(f"Phone: {buyer.get('phone', '')}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    if intra:
        cols = [('Description', 58, 'L'), ('HSN', 16, 'L'), ('Qty', 10, 'R'), ('Taxable (Rs)', 24, 'R'),
                ('GST %', 12, 'R'), ('CGST (Rs)', 22, 'R'), ('SGST (Rs)', 22, 'R'), ('Total (Rs)', 26, 'R')]
    else:
        cols = [('Description', 70, 'L'), ('HSN', 18, 'L'), ('Qty', 12, 'R'), ('Taxable (Rs)', 28, 'R'),
                ('GST %', 14, 'R'), ('IGST (Rs)', 24, 'R'), ('Total (Rs)', 24, 'R')]
    pdf.set_fill_color(*MAROON)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 8)
    for i, (name, w, al) in enumerate(cols):
        pdf.cell(w, 7, name, fill=True, align=al, new_x=XPos.LMARGIN if i == len(cols) - 1 else XPos.RIGHT,
                 new_y=YPos.NEXT if i == len(cols) - 1 else YPos.TOP)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font('Helvetica', '', 8)
    discount_total = 0
    for ln in br.get('lines', []):
        discount_total += ln.get('discount', 0)
        vals = [_safe(ln['description'])[:38 if intra else 46], ln.get('hsn', ''), str(ln['qty']), _amt(ln['taxable']),
                f"{ln['rate']:g}%"]
        vals += [_amt(ln['cgst']), _amt(ln['sgst'])] if intra else [_amt(ln['igst'])]
        vals.append(_amt(ln['paid']))
        for i, ((name, w, al), v) in enumerate(zip(cols, vals)):
            pdf.cell(w, 7, v, border='B', align=al, new_x=XPos.LMARGIN if i == len(cols) - 1 else XPos.RIGHT,
                     new_y=YPos.NEXT if i == len(cols) - 1 else YPos.TOP)

    t = br.get('totals', {})
    pdf.ln(2)
    pdf.set_font('Helvetica', '', 9)
    summary = [('Taxable value', t.get('taxable', 0))]
    summary += [('CGST', t.get('cgst', 0)), ('SGST', t.get('sgst', 0))] if intra else [('IGST', t.get('igst', 0))]
    for label, paise in summary:
        pdf.cell(152, 6, label, align='R')
        pdf.cell(38, 6, _rupees(paise), align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(152, 8, 'Invoice total (incl. GST)', align='R')
    pdf.cell(38, 8, _rupees(t.get('invoice_value', 0)), align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 9)
    pdf.cell(190, 6, f"Paid online (prepaid) - Razorpay payment ID: {_safe(order.razorpay_payment_id or '-')}",
             align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(6)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(90, 90, 90)
    notes = ['Prices include GST; the tax shown above is the GST contained in the price you paid.']
    if discount_total:
        notes.append(f"Coupon / points discount of {_rupees(discount_total)} is already deducted in the amounts above.")
    notes.append('This is a computer-generated invoice and needs no signature.')
    for n in notes:
        pdf.multi_cell(0, 4.5, _safe(n), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.cell(0, 5, _safe(f"For {seller.get('legal_name', '')} (trading as {seller.get('trade_name', '')})"),
             align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
