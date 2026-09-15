import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle
import local_store
import supplier_prices
from supplier_adapters import provider_rows
from test_supplier_prices import configuration

class PDFPrices(unittest.TestCase):
    def pdf(self):
        output=BytesIO();canvas=Canvas(output)
        for price in ('123.50','145.00'):
            table=Table([['Name','SKU','Unit','Price','Currency'],['PVC','0001','sheet',price,'RUB']],colWidths=[90]*5)
            table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),1,'black')]))
            table.wrap(450,200);table.drawOn(canvas,40,650);canvas.showPage()
        canvas.save();return output.getvalue()

    def test_pdf_pages_preview_original_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            service=supplier_prices.SupplierPrices(local_store.LocalStore(directory),Path(directory)/'docs')
            source=configuration(format='pdf');raw=self.pdf()
            result=service.read({'source':source,'content':base64.b64encode(raw).decode(),'file_name':'test.pdf'})
            self.assertEqual(len(result['sheets']),2)
            with patch('supplier_prices.read_pdf',side_effect=AssertionError('Must reuse parsed page')):
                preview=service.preview(result['run_id'],source)
            self.assertEqual(preview['items'][0]['new_price'],'123.5')
            second=service.preview(result['run_id'],{**source,'sheet':1})
            self.assertEqual(second['items'][0]['new_price'],'145')
            self.assertEqual(base64.b64decode(service.document(result['document'])['content']),raw)
            self.assertTrue(result['document']['filename'].endswith('.pdf'))

    @unittest.skipUnless(__import__('shutil').which('tesseract'),'Tesseract is an optional external OCR engine')
    def test_ocr_real_engine_english_scan(self):
        import pypdfium2 as pdfium
        from reportlab.lib.utils import ImageReader
        source=self.pdf();doc=pdfium.PdfDocument(source);bitmap=doc[0].render(scale=2)
        image=bitmap.to_pil();output=BytesIO();canvas=Canvas(output);canvas.drawImage(ImageReader(image),0,0,width=595,height=842);canvas.save()
        from price_pdf import read_pdf
        result=read_pdf(output.getvalue(),{'sheet':0,'pdf_mode':'ocr','ocr_language':'eng'})
        text=' '.join(' '.join(row['values']) for row in result['rows'])
        self.assertIn('123.50',text);self.assertTrue(any('OCR' in w for w in result['warnings']))
        bitmap.close();doc.close()

    def test_zenon_no_zero_and_currency_required(self):
        raw=b'<h1 class="js_c1name">PVC</h1><div id="product" data-articul="0001"></div><div><span class="rub">100</span><span class="kop">50</span> RUB</div>'
        source=configuration(format='zenon',url='https://zenonline.ru/product',unit='sheet')
        with self.assertRaisesRegex(ValueError,'рубли'):provider_rows(raw,source)
        raw=raw.replace(b' RUB', ' руб. '.encode())
        self.assertEqual(provider_rows(raw,source)['rows'][0]['values'][3],'100.5')
        with self.assertRaises(ValueError):provider_rows(raw.replace(b'100',b'on request'),source)

    def test_forda_ambiguous_tiers_and_currency(self):
        import json
        source=configuration(format='forda',url='https://www.forda.ru/get_offers?id=1',unit='sheet')
        item={'name':'PVC','sku':'0001','prices':[{'price':'100','currency':'RUB'}]}
        self.assertEqual(provider_rows(json.dumps([item]).encode(),source)['rows'][0]['values'][3],'100')
        item['prices'].append({'price':'90','currency':'RUB'})
        result=provider_rows(json.dumps([item]).encode(),source)
        self.assertEqual(result['rows'],[]);self.assertEqual(len(result['issues']),1)
