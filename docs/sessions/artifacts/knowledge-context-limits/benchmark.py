"""Same inline benchmark body used for the two retained one-shot measurements.

This formatted copy was saved after execution; it is not a new run or harness.
The first run used the reviewed full-scan draft, the second the indexed draft.
"""
from time import perf_counter
from app.services.scripts.knowledge.sections import build_section_catalog, render_catalog_page

for count in (500, 2000, 5000):
    nodes = [dict(id='root', source_id='source', source_version=1, version='1',
                  heading='Root', content='Conditions.', path='1', depth=1)]
    nodes += [dict(id=str(i), source_id='source', source_version=1, version='1',
                   heading=f'Topic {i}', content='Facts.', path=f'1.{i}', depth=2,
                   parent_id='root') for i in range(1, count)]
    catalog = build_section_catalog(nodes, tenant_id='tenant', campaign_id='campaign', source_policy='call_snapshot')
    start = perf_counter()
    page = render_catalog_page(catalog, parent='')
    print(count, round(perf_counter() - start, 6), page['status'], len(page['entries']))
