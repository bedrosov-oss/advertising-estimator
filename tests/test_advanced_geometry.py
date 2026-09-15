import base64
from io import StringIO
import math
import unittest
import mixed_layout
import vector_geometry

class AdvancedGeometryTests(unittest.TestCase):
    def test_different_sizes_gap_and_rotation(self):
        result=mixed_layout.layout({'sheet_width_mm':'1000','sheet_height_mm':'500','edge_mm':'10','gap_mm':'5','allow_rotate':True,'parts':[
            {'name':'A','width_mm':'300','height_mm':'200','quantity':3}, {'name':'B','width_mm':'150','height_mm':'100','quantity':4}]})
        self.assertEqual(result['sheets'],1);self.assertEqual(len(result['placements']),7)
        self.assertEqual(result['row']['quantity'],'1');self.assertEqual(result['row']['price'],'')
        for p in result['placements']:
            self.assertGreaterEqual(p['x_mm'],10);self.assertGreaterEqual(p['y_mm'],10)
            self.assertLessEqual(p['x_mm']+p['width_mm'],990);self.assertLessEqual(p['y_mm']+p['height_mm'],490)
        for i,a in enumerate(result['placements']):
            for b in result['placements'][i+1:]:
                if a['sheet']==b['sheet']:
                    separated=(a['x_mm']+a['width_mm']+5<=b['x_mm'] or b['x_mm']+b['width_mm']+5<=a['x_mm'] or a['y_mm']+a['height_mm']+5<=b['y_mm'] or b['y_mm']+b['height_mm']+5<=a['y_mm'])
                    self.assertTrue(separated)

    def test_impossible_part_and_precision(self):
        body={'sheet_width_mm':'100','sheet_height_mm':'50','parts':[{'name':'A','width_mm':'40','height_mm':'80','quantity':1}]}
        with self.assertRaises(ValueError):mixed_layout.layout(body)
        self.assertEqual(mixed_layout.layout({**body,'allow_rotate':True})['sheets'],1)
        with self.assertRaises(ValueError):mixed_layout.layout({**body,'gap_mm':'0.0001'})

    def svg(self,content,**extra):return vector_geometry.measure({'format':'svg','content':base64.b64encode(content.encode()).decode(),**extra})

    def test_svg_scale_group_transform_and_open_length(self):
        result=self.svg('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100"><g transform="translate(10,20) scale(2)"><path d="M0,0 L10,0 L10,5 L0,5 Z"/></g><path d="M0,0 L10,0"/></svg>')
        self.assertAlmostEqual(float(result['length_m']),.07,places=6)
        self.assertEqual(result['open_contours'],1);self.assertEqual(result['contours'],2)
        self.assertEqual(result['closed_area_sum_m2'],'0.000200')
        self.assertEqual(result['row']['unit'],'пог. м')

    def test_svg_unknown_units_unsupported_and_duplicates(self):
        prefix='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        with self.assertRaisesRegex(ValueError,'масштаб'):self.svg(prefix+'<path d="M0,0 L10,0"/></svg>')
        result=self.svg(prefix+'<path d="M0,0 L10,0"/></svg>',unit_mm='1')
        self.assertEqual(result['length_m'],'0.010000')
        for content in ('<use href="#a"/>','<text>test</text>','<path d="M0,0 L10,0"/><path d="M0,0 L10,0"/>'):
            with self.assertRaises(ValueError):self.svg(prefix+content+'</svg>',unit_mm='1')

    def test_dxf_units_circle_layer_and_unsupported_block(self):
        import ezdxf
        doc=ezdxf.new();doc.units=4;model=doc.modelspace();model.add_circle((0,0),100,dxfattribs={'layer':'CUT'});model.add_line((0,0),(20,0),dxfattribs={'layer':'MARK'})
        output=StringIO();doc.write(output)
        body={'format':'dxf','content':base64.b64encode(output.getvalue().encode()).decode(),'layer':'CUT'}
        result=vector_geometry.measure(body)
        self.assertAlmostEqual(float(result['length_m']),2*math.pi*.1,delta=.0002)
        self.assertEqual(result['open_contours'],0)
        with self.assertRaisesRegex(ValueError,'масштаб'):vector_geometry.measure({**body,'unit_mm':'25.4'})
