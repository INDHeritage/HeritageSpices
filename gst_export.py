"""The GST workbook for a quarter: sheets laid out like the GST portal's tables, with the editable cells
(input credit, late fee, interest) highlighted yellow and the totals as live formulas, so it can be adjusted
in Excel or Google Sheets before the figures are entered on the portal."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


HEAD = PatternFill('solid', fgColor='1B4332')
SUB = PatternFill('solid', fgColor='EFE6D2')
INPUT = PatternFill('solid', fgColor='FFF2B3')
MONEY = '#,##0.00'


def _r(paise):
    """Paise -> rupees for the sheet."""
    return round((paise or 0) / 100, 2)


def _header(ws, row, labels, widths=None):
    for i, text in enumerate(labels, 1):
        c = ws.cell(row=row, column=i, value=text)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical='center')
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w


def _title(ws, text, sub=None):
    ws['A1'] = text
    ws['A1'].font = Font(bold=True, size=14, color='1B4332')
    if sub:
        ws['A2'] = sub
        ws['A2'].font = Font(italic=True, color='666666')


def _money_cols(ws, first_row, last_row, cols):
    for r in range(first_row, last_row + 1):
        for c in cols:
            ws.cell(row=r, column=c).number_format = MONEY


def _total_row(ws, row, label, first, last, sum_cols):
    ws.cell(row=row, column=1, value=label).font = Font(bold=True)
    for c in sum_cols:
        L = get_column_letter(c)
        cell = ws.cell(row=row, column=c, value=f'=SUM({L}{first}:{L}{last})' if last >= first else 0)
        cell.font = Font(bold=True)
        cell.number_format = MONEY
    for c in range(1, max(sum_cols) + 1):
        ws.cell(row=row, column=c).fill = SUB


def build_workbook(rep):
    """rep: the dict assembled by the admin GST page (see app.build_gst_report). Returns .xlsx bytes."""
    wb = Workbook()
    seller, w, out = rep['seller'], rep['worksheet'], rep['outward']
    period = f"{rep['label']}  |  {rep['start'].strftime('%d %b %Y')} to {rep['end'].strftime('%d %b %Y')}"
    ident = f"{seller['legal_name']} (trading as {seller['trade_name']})  |  GSTIN {seller['gstin']}"

    # ---------- GSTR-3B ----------
    ws = wb.active
    ws.title = 'GSTR-3B'
    _title(ws, 'GSTR-3B worksheet', f'{ident}  |  {period}')
    for col, wdt in zip('ABCDE', (48, 18, 16, 16, 16)):
        ws.column_dimensions[col].width = wdt
    _header(ws, 4, ['3.1(a) Outward taxable supplies', 'Taxable value', 'IGST', 'CGST', 'SGST/UTGST'])
    src = [('Online (website orders)', out['online']), ('Shop (billing app, after sales returns)', out['shop'])]
    for i, (label, d) in enumerate(src, 5):
        ws.cell(row=i, column=1, value=label)
        for c, k in zip((2, 3, 4, 5), ('taxable', 'igst', 'cgst', 'sgst')):
            ws.cell(row=i, column=c, value=_r(d[k]))
    _total_row(ws, 7, 'Total outward taxable supplies', 5, 6, (2, 3, 4, 5))
    _money_cols(ws, 5, 7, (2, 3, 4, 5))
    ws['A8'] = '3.1(c) Nil rated / exempt outward supplies (shop bills of supply)'
    ws['B8'] = _r(out['nil_exempt'])
    ws['B8'].number_format = MONEY

    _header(ws, 10, ['4A(5) Input tax credit to claim  (edit the yellow cells)', '', 'IGST', 'CGST', 'SGST/UTGST'])
    ws['A11'] = 'ITC claimed (starts from the purchases recorded in the billing app: check them against GSTR-2B)'
    for c, h in zip((3, 4, 5), ('igst', 'cgst', 'sgst')):
        cell = ws.cell(row=11, column=c, value=_r(w['itc_claim'][h]))
        cell.fill, cell.number_format = INPUT, MONEY

    _header(ws, 13, ['Tax payable, head by head (before one head\'s credit is used for another)', '', 'IGST', 'CGST', 'SGST/UTGST'])
    ws['A14'] = 'Output tax on the sales above'
    ws['A15'] = 'Less: input credit claimed'
    ws['A16'] = 'Tax left to pay in cash (head by head)'
    for c in (3, 4, 5):
        L = get_column_letter(c)
        ws.cell(row=14, column=c, value=f'={L}7')
        ws.cell(row=15, column=c, value=f'={L}11')
        ws.cell(row=16, column=c, value=f'=MAX(0,{L}14-{L}15)').font = Font(bold=True)
    _money_cols(ws, 14, 16, (3, 4, 5))

    _header(ws, 18, ['As the portal sets credit off (IGST credit may pay CGST/SGST; CGST and SGST credit never pay each other)', '', 'IGST', 'CGST', 'SGST/UTGST'])
    ws['A19'] = 'Estimated cash tax payable after set-off (for planning; the portal decides)'
    for c, h in zip((3, 4, 5), ('igst', 'cgst', 'sgst')):
        ws.cell(row=19, column=c, value=_r(w['cash'][h])).number_format = MONEY

    ws['A21'] = 'Late fee, interest (cash only)  (edit the yellow cells)'
    ws['A21'].font = Font(bold=True)
    for r, (label, vals) in enumerate((('Late fee', {'cgst': w['late_fee']['cgst'], 'sgst': w['late_fee']['sgst']}),
                                       ('Interest', {h: w['interest'][h] for h in ('igst', 'cgst', 'sgst')})), 22):
        ws.cell(row=r, column=1, value=label)
        for c, h in zip((3, 4, 5), ('igst', 'cgst', 'sgst')):
            cell = ws.cell(row=r, column=c, value=_r(vals.get(h, 0)))
            cell.fill, cell.number_format = INPUT, MONEY
    ws['A25'] = 'TOTAL CASH TO PAY (estimate)'
    ws['A25'].font = Font(bold=True)
    ws['C25'] = '=SUM(C19:E19)+SUM(C22:E23)'
    ws['C25'].font = Font(bold=True)
    ws['C25'].number_format = MONEY
    ws['A27'] = 'Due dates (usual, QRMP): GSTR-1 ' + rep['due']['gstr1'].strftime('%d %b %Y') + ', GSTR-3B ' + rep['due']['gstr3b'].strftime('%d %b %Y') + '. Check the portal for extensions.'
    ws['A28'] = 'Prepared from your own records for you and your CA. Not a return: confirm every figure before filing.'
    ws['A28'].font = Font(italic=True, color='A00000')

    # ---------- GSTR-1 B2CS ----------
    ws = wb.create_sheet('GSTR-1 B2CS')
    _title(ws, 'Table 7: Supplies to consumers (B2CS), by place of supply and rate', period)
    _header(ws, 4, ['Place of supply', 'Rate %', 'Taxable value', 'IGST', 'CGST', 'SGST', 'of which Online taxable', 'of which Shop taxable'],
            (24, 9, 16, 14, 14, 14, 20, 20))
    rows = rep['gstr1']['b2cs']
    for i, r in enumerate(rows, 5):
        vals = [r['state'], r['rate'], _r(r['taxable']), _r(r['igst']), _r(r['cgst']), _r(r['sgst']),
                _r(r['online_taxable']), _r(r['shop_taxable'])]
        for c, v in enumerate(vals, 1):
            ws.cell(row=i, column=c, value=v)
    _money_cols(ws, 5, 4 + len(rows), range(3, 9))
    _total_row(ws, 5 + len(rows), 'Total', 5, 4 + len(rows), range(3, 9))

    # ---------- GSTR-1 B2B ----------
    ws = wb.create_sheet('GSTR-1 B2B')
    _title(ws, 'Table 4: Supplies to registered businesses (B2B), invoice by invoice', 'From the shop billing app')
    _header(ws, 4, ['Invoice no.', 'Date', 'Customer GSTIN', 'Customer', 'Place of supply', 'Rate %', 'Taxable value', 'IGST', 'CGST', 'SGST', 'Invoice value'],
            (20, 12, 20, 28, 12, 8, 15, 12, 12, 12, 15))
    r = 5
    for inv in rep['gstr1']['b2b']:
        for rt in inv['rates']:
            vals = [inv['invoice_number'], inv['date'], inv['gstin'], inv['customer'], inv['place_of_supply'], rt['rate'],
                    _r(rt['taxable']), _r(rt['igst']), _r(rt['cgst']), _r(rt['sgst']), _r(inv['value'])]
            for c, v in enumerate(vals, 1):
                ws.cell(row=r, column=c, value=v)
            r += 1
    _money_cols(ws, 5, r, range(7, 12))
    _total_row(ws, r, 'Total (invoice value counts once per rate line)', 5, r - 1, range(7, 12))

    # ---------- HSN ----------
    ws = wb.create_sheet('GSTR-1 HSN')
    _title(ws, 'Table 12: HSN-wise summary of outward supplies', period)
    _header(ws, 4, ['Section', 'HSN', 'Rate %', 'Unit', 'Quantity', 'Taxable value', 'IGST', 'CGST', 'SGST', 'Total value'],
            (10, 12, 8, 8, 10, 15, 12, 12, 12, 15))
    rows = rep['gstr1']['hsn']
    for i, h in enumerate(rows, 5):
        vals = [h['section'], h['hsn'], h['rate'], h.get('unit', 'PCS'), h['qty'], _r(h['taxable']), _r(h['igst']), _r(h['cgst']),
                _r(h['sgst']), _r(h['value'])]
        for c, v in enumerate(vals, 1):
            ws.cell(row=i, column=c, value=v)
    _money_cols(ws, 5, 4 + len(rows), range(6, 11))
    _total_row(ws, 5 + len(rows), 'Total', 5, 4 + len(rows), range(6, 11))

    # ---------- credit notes ----------
    ws = wb.create_sheet('Credit notes')
    _title(ws, 'Table 9B: Credit notes (sales returns)', 'From the shop billing app')
    _header(ws, 4, ['Note no.', 'Date', 'Customer GSTIN', 'Customer', 'Taxable value', 'IGST', 'CGST', 'SGST', 'Note value'],
            (20, 12, 20, 28, 15, 12, 12, 12, 15))
    rows = rep['gstr1']['credit_notes']
    for i, n in enumerate(rows, 5):
        vals = [n['number'], n['date'], n['gstin'] or '', n['customer'], _r(n['taxable']), _r(n['igst']), _r(n['cgst']), _r(n['sgst']), _r(n['value'])]
        for c, v in enumerate(vals, 1):
            ws.cell(row=i, column=c, value=v)
    _money_cols(ws, 5, 4 + len(rows), range(5, 10))
    _total_row(ws, 5 + len(rows), 'Total', 5, 4 + len(rows), range(5, 10))

    # ---------- documents ----------
    ws = wb.create_sheet('Documents issued')
    _title(ws, 'Table 13: Documents issued in the period', 'Includes cancelled numbers')
    _header(ws, 4, ['Channel', 'Kind', 'Series', 'From', 'To', 'Total issued', 'Cancelled'], (10, 16, 18, 22, 22, 12, 12))
    for i, d in enumerate(rep['gstr1']['docs'], 5):
        for c, v in enumerate([d['channel'], d['kind'], d['series'], d['from'], d['to'], d['total'], d['cancelled']], 1):
            ws.cell(row=i, column=c, value=v)

    # ---------- online orders ----------
    ws = wb.create_sheet('Online orders')
    _title(ws, 'Every website order counted in this quarter', 'For checking and for your CA')
    _header(ws, 4, ['Order', 'Invoice no.', 'Date', 'Delivered to', 'Basis', 'Taxable value', 'IGST', 'CGST', 'SGST', 'Invoice value'],
            (16, 18, 12, 18, 10, 15, 12, 12, 12, 15))
    sup = rep['online']['supplies']
    for i, s in enumerate(sup, 5):
        t = s['totals']
        vals = [s['order_number'], s['invoice_number'], s['date'].strftime('%Y-%m-%d'), s['state'], s['source'],
                _r(t['taxable']), _r(t['igst']), _r(t['cgst']), _r(t['sgst']), _r(t['invoice_value'])]
        for c, v in enumerate(vals, 1):
            ws.cell(row=i, column=c, value=v)
    _money_cols(ws, 5, 4 + len(sup), range(6, 11))
    end = 5 + len(sup)
    _total_row(ws, end, 'Total', 5, 4 + len(sup), range(6, 11))
    r = end + 2
    for title, rows in (('Cancelled (not counted as sales)', rep['online']['cancelled']),
                        ('Returned to us, RTO (not counted; ask your CA about a credit note)', rep['online']['returned'])):
        ws.cell(row=r, column=1, value=title).font = Font(bold=True)
        r += 1
        for x in rows:
            ws.cell(row=r, column=1, value=x['order_number'])
            ws.cell(row=r, column=2, value=x['invoice_number'])
            ws.cell(row=r, column=3, value=x['date'].strftime('%Y-%m-%d'))
            ws.cell(row=r, column=10, value=_r(x['value'])).number_format = MONEY
            r += 1
        r += 1

    # ---------- purchases ----------
    ws = wb.create_sheet('Purchases (ITC)')
    _title(ws, 'Purchases recorded in the billing app', 'Input credit: check every line against GSTR-2B and your supplier invoices before claiming')
    _header(ws, 4, ['Date', 'Supplier', 'Supplier GSTIN', 'Supplier invoice', 'Taxable value', 'IGST', 'CGST', 'SGST', 'Total GST', 'Invoice total', 'Eligible? (your note)'],
            (12, 26, 20, 18, 15, 12, 12, 12, 12, 15, 22))
    shop = rep['shop']
    pur = shop['purchases'] if shop else []
    for i, p in enumerate(pur, 5):
        vals = [p['date'], p['supplier'], p['gstin'], p['supplier_invoice'], _r(p['taxable']), _r(p['igst']), _r(p['cgst']),
                _r(p['sgst']), _r(p['gst']), _r(p['total'])]
        for c, v in enumerate(vals, 1):
            ws.cell(row=i, column=c, value=v)
        ws.cell(row=i, column=11).fill = INPUT
    _money_cols(ws, 5, 4 + len(pur), range(5, 11))
    _total_row(ws, 5 + len(pur), 'Total', 5, 4 + len(pur), range(5, 11))

    # ---------- notes ----------
    ws = wb.create_sheet('Read me')
    ws.column_dimensions['A'].width = 120
    lines = ['How to use this workbook', '',
             'GSTR-3B sheet: yellow cells are yours to edit (input credit to claim, late fee, interest). Totals recalculate.',
             'The other sheets follow the GST portal tables, so figures can be entered row by row.',
             f"Return scheme: quarterly (QRMP). Due dates are usually GSTR-1 {rep['due']['gstr1'].strftime('%d %b %Y')} and GSTR-3B {rep['due']['gstr3b'].strftime('%d %b %Y')}.",
             'Online sales: prices include GST, so the tax is worked back out of each price. Shop figures come from the billing app.',
             'Input credit is only what was recorded in the billing app. Check each purchase against GSTR-2B and the supplier invoice.',
             'This is a preparation aid. Your CA should confirm HSN codes, rates and every figure before you file.', '']
    lines += [f'WARNING: {m}' for m in rep['online']['warnings']]
    if rep.get('shop_note'):
        lines.append(f"WARNING: shop figures are missing: {rep['shop_note']}")
    for i, t in enumerate(lines, 1):
        ws.cell(row=i, column=1, value=t)
    ws['A1'].font = Font(bold=True, size=13, color='1B4332')

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
