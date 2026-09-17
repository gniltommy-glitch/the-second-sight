from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/pylibs'))
from pypdf import PdfReader
out = ROOT / 'research/pdf_text'
out.mkdir(parents=True, exist_ok=True)
for path in ROOT.glob('*.pdf'):
    reader = PdfReader(path)
    pages = [f'\n=== PDF PAGE {i+1} ===\n{page.extract_text() or ""}'
             for i, page in enumerate(reader.pages)]
    (out / (path.stem + '.txt')).write_text('\n'.join(pages), encoding='utf-8')
    print(path.name, len(pages), 'pages', sum(map(len, pages)), 'characters')
