#!/usr/bin/env python3
"""Local, offline lookup for the estimator's packaged reference database."""
import argparse
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse

KB = Path(__file__).resolve().parents[1] / 'references' / 'knowledge'
SECTIONS = ('materials', 'operations', 'sources', 'conditions', 'prices', 'websites')


def read_data():
    data = {s: json.loads((KB / 'data' / (s + '.json')).read_text(encoding='utf-8'))
            for s in SECTIONS}
    data['metadata'] = json.loads((KB / 'data/metadata.json').read_text(encoding='utf-8'))
    return data


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def validate(data):
    errors = []
    indexes = {}
    for section in SECTIONS:
        rows = data[section]
        if not isinstance(rows, list):
            errors.append(f'{section}: expected an array')
            continue
        indexes[section] = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get('id') or not row.get('name'):
                errors.append(f'{section}: record needs id and name')
                continue
            if row['id'] in indexes[section]:
                errors.append(f'{section}: duplicate {row["id"]}')
            indexes[section][row['id']] = row
    if errors:
        return errors
    for source in data['sources']:
        if urlparse(source['url']).scheme != 'https':
            errors.append(f'{source["id"]}: expected an HTTPS source URL')
        try:
            date.fromisoformat(source['checked_on'])
        except (ValueError, TypeError):
            errors.append(f'{source["id"]}: invalid check date')
    for section in ('materials', 'operations', 'conditions', 'websites'):
        for row in data[section]:
            for ref in row.get('source_ids', []):
                if ref not in indexes['sources']:
                    errors.append(f'{row["id"]}: unknown source {ref}')
    for row in data['prices']:
        tag = row['id']
        amount = row.get('price_minor')
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            errors.append(f'{tag}: price_minor must be a nonnegative integer')
        if row.get('currency') != 'RUB' or row.get('minor_units') != 2:
            errors.append(f'{tag}: unsupported currency / minor unit scale')
        for key, section in [('source_id', 'sources'), ('material_id', 'materials')]:
            if row.get(key) not in indexes[section]:
                errors.append(f'{tag}: invalid {key}')
        source = indexes['sources'].get(row.get('source_id'), {})
        if source.get('access_status') != 'page_read':
            errors.append(f'{tag}: numerical quote requires a read source')
        for ref in row.get('condition_ids', []):
            if ref not in indexes['conditions']:
                errors.append(f'{tag}: unknown condition {ref}')
        if row.get('status') not in ('published_snapshot', 'conflict', 'from_price', 'scope_needs_confirmation', 'historical_snapshot'):
            errors.append(f'{tag}: invalid status')
        if source.get('publication_status') == 'historical' and row.get('status') != 'historical_snapshot':
            errors.append(f'{tag}: historical source cannot provide a current quote')
        if row.get('status') == 'historical_snapshot':
            if row.get('current_price_usable') is not False or not row.get('source_published_period'):
                errors.append(f'{tag}: historical quote needs its period and an explicit current-use restriction')
        if row.get('price_kind') == 'from' and row.get('status') != 'from_price':
            errors.append(f'{tag}: a from-price must keep its conditional status')
        if row.get('status') == 'conflict':
            matched = [indexes['conditions'][ref] for ref in row['condition_ids']
                       if ref in indexes['conditions'] and tag in indexes['conditions'][ref].get('conflicting_quote_ids', [])]
            if not matched:
                errors.append(f'{tag}: conflict lacks linked observations')
    for condition in data['conditions']:
        for ref in condition.get('conflicting_quote_ids', []):
            if ref not in indexes['prices']:
                errors.append(f'{condition["id"]}: missing conflicting quote {ref}')
    for site in data['websites']:
        for key, section in [('condition_ids', 'conditions'), ('material_ids', 'materials'),
                             ('operation_ids', 'operations'), ('price_ids', 'prices')]:
            for ref in site.get(key, []):
                if ref not in indexes[section]:
                    errors.append(f'{site["id"]}: unknown {key} reference {ref}')
                elif key == 'price_ids' and indexes['prices'][ref]['source_id'] not in site['source_ids']:
                    errors.append(f'{site["id"]}: quote {ref} belongs to another source')
        for label in site.get('incomplete_price_labels', []):
            if label.get('source_id') not in site['source_ids'] or label.get('usable_for_calculation') is not False:
                errors.append(f'{site["id"]}: incomplete price label must retain its source and restriction')
    return errors


def expand(row, section, data):
    result = {'section': section, 'record': row}
    source_index = {r['id']: r for r in data['sources']}
    condition_index = {r['id']: r for r in data['conditions']}
    quote_index = {r['id']: r for r in data['prices']}
    source_ids = set(row.get('source_ids', []))
    if row.get('source_id'):
        source_ids.add(row['source_id'])
    conditions = [condition_index[c] for c in row.get('condition_ids', [])]
    if conditions:
        result['conditions'] = conditions
    conflicts = set()
    for condition in conditions:
        source_ids.update(condition.get('source_ids', []))
        conflicts.update(condition.get('conflicting_quote_ids', []))
    if conflicts:
        result['conflicting_observations'] = [quote_index[i] for i in sorted(conflicts)]
    if source_ids:
        result['sources'] = [source_index[i] for i in sorted(source_ids)]
    if section == 'prices':
        result['usage_note'] = 'Снимок публикации. Перепроверьте условия для заказа; неизвестные статьи и НДС не равны нулю.'
    return result


def rebuild(data):
    fd, temporary = tempfile.mkstemp(prefix='estimator-', suffix='.sqlite.tmp', dir=KB)
    os.close(fd)
    try:
        con = sqlite3.connect(temporary)
        try:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            for key, value in data['metadata'].items():
                con.execute('INSERT INTO metadata VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
            for section in (s for s in SECTIONS if s != 'prices'):
                con.execute(f'CREATE TABLE {section} (id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL)')
                con.executemany(f'INSERT INTO {section} VALUES (?,?,?)',
                                [(r['id'], r['name'], json.dumps(r, ensure_ascii=False)) for r in data[section]])
            con.execute('''CREATE TABLE prices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, supplier TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES sources(id),
                material_id TEXT NOT NULL REFERENCES materials(id),
                status TEXT NOT NULL, price_minor INTEGER NOT NULL CHECK(price_minor>=0),
                currency TEXT NOT NULL, unit TEXT NOT NULL, body TEXT NOT NULL)''')
            for r in data['prices']:
                con.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (r['id'], r['name'], r['supplier'], r['source_id'], r['material_id'],
                             r['status'], r['price_minor'], r['currency'], r['unit'], json.dumps(r, ensure_ascii=False)))
            con.execute('CREATE INDEX price_material ON prices(material_id)')
            con.execute('CREATE INDEX price_supplier ON prices(supplier)')
            if con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('SQLite foreign key check failed')
            con.commit()
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite integrity check failed')
        finally:
            con.close()
        os.replace(temporary, KB / 'estimator.sqlite')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'status': 'rebuilt', 'database': str(KB / 'estimator.sqlite'),
            'records': sum(len(data[s]) for s in SECTIONS)}


def demo(data):
    ex = json.loads((KB / 'examples/pvc-signs-20.json').read_text(encoding='utf-8'))
    quotes = {r['id']: r for r in data['prices']}
    conditions = {r['id']: r for r in data['conditions']}
    sheet = quotes[ex['sheet_quote_id']]
    rate_quotes = [quotes[i] for i in ex['print_quote_ids']]
    cutting = quotes[ex['cut_quote_id']]
    if sheet['unit'] != 'лист' or cutting['unit'] != 'пог. м' or any(r['unit'] != 'м²' for r in rate_quotes):
        raise ValueError('Demo quote units do not match the example')
    if any(r['status'] != 'published_snapshot' for r in [sheet, cutting] + rate_quotes):
        raise ValueError('Demo requires resolving a quote status before calculation')
    D = Decimal
    n, w, h = map(D, (ex['quantity'], ex['width_mm'], ex['height_mm']))
    if min(n, w, h) <= 0 or n != n.to_integral_value():
        raise ValueError('Positive dimensions and an integer quantity are required')
    layout = ex['layout_assumptions']
    cols, rows, edge, gap = (D(layout[k]) for k in ('columns','rows','edge_mm','gap_mm'))
    occupied_w = cols*w + (cols-1)*gap + 2*edge
    occupied_h = rows*h + (rows-1)*gap + 2*edge
    if cols*rows < n or occupied_w > sheet['parameters']['width_mm'] or occupied_h > sheet['parameters']['length_mm']:
        raise ValueError('The example layout does not fit the purchased sheet')
    net = n*w*h/D(1000000)
    charged = n*max(w*h/D(1000000), D(conditions['C02']['min_piece_area_m2']))
    perimeter = n*2*(w+h)/1000
    material_cost = D(sheet['price_minor'])/100
    cut_cost = perimeter*D(cutting['price_minor'])/100
    print_costs = sorted({max(charged*D(r['price_minor'])/100, D(conditions['C02']['min_print_order_minor'])/100)
                         for r in rate_quotes})
    def money(value):
        return str(value.quantize(D('0.01'), rounding=ROUND_HALF_UP))
    return dict(status='educational_partial_scenarios',currency='RUB',
                checked_on=data['metadata']['checked_on'],area_m2=str(net),billed_area_m2=str(charged),
                nominal_cut_m=str(perimeter),layout_mm=[str(occupied_w),str(occupied_h)],
                material_cost=money(material_cost),cut_cost=money(cut_cost),
                scenarios=[dict(print_cost=money(p),partial_cost=money(material_cost+cut_cost+p),
                    illustrative_price=money((material_cost+cut_cost+p)*(1+D(ex['markup_fraction']))),
                    illustrative_unit_price=money((material_cost+cut_cost+p)*(1+D(ex['markup_fraction']))/n))
                    for p in print_costs],
                unresolved=ex['unpriced'],
                note='Сценарии не гарантируют цену подрядчика; раскрой, применение ступени, подготовка и налоговые условия требуют проверки.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('stats','validate','rebuild','demo'):
        sub.add_parser(cmd)
    search = sub.add_parser('search')
    search.add_argument('query')
    search.add_argument('--section', choices=SECTIONS+('all',), default='all')
    search.add_argument('--limit', type=int, default=10)
    show = sub.add_parser('show')
    show.add_argument('id')
    args = parser.parse_args()
    data = read_data()
    errors = validate(data)
    if errors:
        emit({'status':'invalid','errors':errors})
        return 1
    if args.command == 'validate':
        emit({'status':'valid','records':sum(len(data[s]) for s in SECTIONS)})
    elif args.command == 'stats':
        emit({'metadata':data['metadata'],'counts':{s:len(data[s]) for s in SECTIONS},
              'price_status_counts':{status:sum(r['status']==status for r in data['prices'])
                                    for status in sorted({r['status'] for r in data['prices']})}})
    elif args.command == 'rebuild':
        emit(rebuild(data))
    elif args.command == 'demo':
        emit(demo(data))
    elif args.command == 'show':
        found = [(section,r) for section in SECTIONS for r in data[section] if r['id'].casefold()==args.id.casefold()]
        if not found:
            emit({'status':'not_found','id':args.id})
            return 2
        emit(expand(found[0][1],found[0][0],data))
    else:
        if not 1 <= args.limit <= 100 or not args.query.strip():
            parser.error('Use a nonempty query and a limit from 1 to 100')
        words = args.query.casefold().split()
        sections = SECTIONS if args.section == 'all' else (args.section,)
        found=[]
        for section in sections:
            for row in data[section]:
                text=json.dumps(row, ensure_ascii=False).casefold()
                if all(word in text for word in words):
                    found.append((section,row))
        emit({'query':args.query,'total_matches':len(found),
              'results':[expand(r,s,data) for s,r in found[:args.limit]]})
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        emit({'status':'error','message':str(exc)})
        raise SystemExit(1)
