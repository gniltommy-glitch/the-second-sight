"""Download ST HAL/CMSIS source files into this workspace; no installers."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'vendor'
HAL = 'https://raw.githubusercontent.com/STMicroelectronics/stm32f4xx-hal-driver/v1.8.3/'
DEV = 'https://raw.githubusercontent.com/STMicroelectronics/cmsis-device-f4/v2.6.10/'
CORE = 'https://raw.githubusercontent.com/ARM-software/CMSIS_5/5.9.0/CMSIS/Core/Include/'
jobs = []
modules = ['hal', 'hal_cortex', 'hal_rcc', 'hal_rcc_ex', 'hal_gpio', 'hal_dma',
           'hal_dma_ex', 'hal_i2c', 'hal_i2c_ex', 'hal_i2s', 'hal_i2s_ex', 'hal_flash',
           'hal_flash_ex', 'hal_pwr', 'hal_pwr_ex']
for m in modules:
    for folder, ext in [('Inc', '.h'), ('Src', '.c')]:
        name = 'stm32f4xx_' + m + ext
        jobs.append((HAL + folder + '/' + name, 'hal/' + folder + '/' + name))
for name in ['stm32f4xx_hal_def.h', 'stm32f4xx_hal_gpio_ex.h', 'stm32f4xx_hal_flash_ramfunc.h', 'Legacy/stm32_hal_legacy.h']:
    jobs.append((HAL + 'Inc/' + name, 'hal/Inc/' + name))
for name in ['stm32f4xx.h', 'stm32f411xe.h', 'system_stm32f4xx.h']:
    jobs.append((DEV + 'Include/' + name, 'device/Include/' + name))
for name in ['system_stm32f4xx.c', 'gcc/startup_stm32f411xe.s']:
    jobs.append((DEV + 'Source/Templates/' + name, 'device/' + name))
for name in ['core_cm4.h', 'cmsis_version.h', 'cmsis_compiler.h', 'cmsis_gcc.h', 'mpu_armv7.h']:
    jobs.append((CORE + name, 'core/' + name))
for base, name in [(HAL, 'hal'), (DEV, 'device')]:
    jobs.append((base + 'LICENSE.md', name + '/LICENSE.md'))
jobs.append(('https://raw.githubusercontent.com/ARM-software/CMSIS_5/5.9.0/LICENSE.txt', 'core/LICENSE.txt'))
jobs += [(f'https://raw.githubusercontent.com/DFRobot/DFRobot_MatrixLidar/master/{n}', 'references/dfrobot/' + n)
         for n in ['DFRobot_MatrixLidar.cpp', 'DFRobot_MatrixLidar.h', 'LICENCE']]

def fetch(job):
    url, relative = job
    path = DEST / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = path.read_bytes() if path.exists() else urllib.request.urlopen(url, timeout=90).read()
    path.write_bytes(data)
    return {'file': relative, 'url': url, 'sha256': hashlib.sha256(data).hexdigest()}

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=6) as pool:
        manifest = list(pool.map(fetch, jobs))
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Downloaded {len(manifest)} files to {DEST}')
