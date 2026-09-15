"""Generated input checks for amount conservation and complete nesting."""
import unittest
from decimal import Decimal
try:
    from hypothesis import given,settings,strategies as st
except ImportError:
    raise unittest.SkipTest('Install requirements-dev.txt for generated property checks')
import engine
import mixed_layout

class PropertyTests(unittest.TestCase):
    @settings(max_examples=70,deadline=None)
    @given(st.lists(st.tuples(st.integers(1,100),st.integers(0,100000)),min_size=1,max_size=15),st.integers(0,80),st.integers(0,100))
    def test_row_order_and_money_conservation(self,values,profit,discount):
        rows=[{'id':str(i),'name':'T','unit':'шт.','quantity':str(qty),'price':str(Decimal(price)/100),'minimum_charge':'0'} for i,(qty,price) in enumerate(values)]
        project={'project':{'quantity':'1'},'rows':rows,'settings':{'profit_percent':str(profit),'discount_percent':str(discount),'tax_mode':'none'}}
        result=engine.calculate(project);reversed_result=engine.calculate({**project,'rows':list(reversed(rows))})
        self.assertEqual(result['totals'],reversed_result['totals']);totals=result['totals']
        self.assertEqual(Decimal(totals['direct']),sum(Decimal(q)*Decimal(p)/100 for q,p in values))
        self.assertEqual(Decimal(totals['sale_net']),Decimal(totals['cost'])+Decimal(totals['profit']))
        self.assertEqual(Decimal(totals['total']),Decimal(totals['sale_net'])+Decimal(totals['tax']))
        rows[0]['price']='';self.assertIsNone(engine.calculate(project)['totals']['total'])

    @settings(max_examples=35,deadline=None)
    @given(st.lists(st.tuples(st.integers(10,180),st.integers(10,180),st.integers(1,3)),min_size=1,max_size=8),st.booleans())
    def test_layout_preserves_every_part(self,parts,rotate):
        body={'sheet_width_mm':'400','sheet_height_mm':'400','gap_mm':'3','edge_mm':'5','allow_rotate':rotate,'parts':[{'name':str(i),'width_mm':str(w),'height_mm':str(h),'quantity':q} for i,(w,h,q) in enumerate(parts)]}
        result=mixed_layout.layout(body)
        self.assertEqual(len(result['placements']),sum(p[2] for p in parts))
        self.assertEqual(len({p['id'] for p in result['placements']}),sum(p[2] for p in parts))
        for p in result['placements']:
            self.assertGreaterEqual(p['x_mm'],5);self.assertGreaterEqual(p['y_mm'],5)
            self.assertLessEqual(p['x_mm']+p['width_mm'],395);self.assertLessEqual(p['y_mm']+p['height_mm'],395)
