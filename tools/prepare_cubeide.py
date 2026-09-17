"""Prepare source/header folders to copy into a CubeIDE Empty F411VE project."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
target = root / 'cubeide_import'
inc, src = target / 'Inc', target / 'Src'
inc.mkdir(parents=True, exist_ok=True)
src.mkdir(parents=True, exist_ok=True)
for origin in ['vendor/hal/Inc', 'vendor/device/Include', 'vendor/core']:
    for path in (root / origin).rglob('*.h'):
        dest = inc / path.relative_to(root / origin)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
shutil.copy2(root / 'firmware/stm32f4xx_hal_conf.h', inc / 'stm32f4xx_hal_conf.h')
shutil.copy2(root / 'firmware/app.c', src / 'main.c')
shutil.copy2(root / 'vendor/device/system_stm32f4xx.c', src / 'system_stm32f4xx.c')
for path in (root / 'vendor/hal/Src').glob('*.c'):
    shutil.copy2(path, src / path.name)
for name in ['hal', 'device', 'core']:
    for path in (root / 'vendor' / name).glob('LICENSE*'):
        dest = target / 'licenses' / name / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
assert (src / 'main.c').read_bytes() == (root / 'firmware/app.c').read_bytes()
print('Ready:', target)
print('Copy Inc, Src, licenses into an EMPTY STM32F411VET6 CubeIDE project.')
print('Keep CubeIDE startup, linker script, syscalls.c and sysmem.c. See HUONG_DAN_CUBEIDE.md.')
