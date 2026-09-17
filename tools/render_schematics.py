from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.tools/pylibs'))
import pymupdf
out=ROOT/'research/images'
out.mkdir(parents=True,exist_ok=True)
for name in ['[schematic] - en.MB1115-F411VE-D01_Schematic.pdf', 'SEN0628_rp2040-8-8-matrix_schematics_v1.0.pdf']:
    doc=pymupdf.open(ROOT/name)
    for i,page in enumerate(doc):
        if i>=6:
            break
        page.get_pixmap(matrix=pymupdf.Matrix(1.6,1.6)).save(out/f'{"board" if name.startswith("[") else "sensor"}_{i+1}.png')
        print(name, i+1, page.get_text()[:50].replace('\n',' '))
