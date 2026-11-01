"""
# finx - turn financial PDFs into structured data, without any AI language model

Files, in the order the data flows through them:
- `read_pdf.py`      read every word on every page, with its position, size and boldness
- `tables.py`        words -> lines -> rows (label + numbers) -> tables (columns, periods, units)
- `page_index.py`    page text, headings, sections ("Item 7"), contents pages, document type
- `statements.py`    find the five financial statements
- `check_numbers.py` prove the numbers add up (totals, balance sheet, cash flow) and mark them verified
- `pipeline.py`      runs everything for one PDF: `run(path)` or `extract(path)`
"""
