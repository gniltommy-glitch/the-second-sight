"""Build the exact standalone source with the installed ARM GCC toolchain."""
import argparse
from pathlib import Path
import shutil
import subprocess
ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--gcc', help='Path to arm-none-eabi-gcc executable')
args = p.parse_args()
gcc = args.gcc or shutil.which('arm-none-eabi-gcc')
if not gcc:
    gcc = next(iter(Path('C:/ST').glob('**/arm-none-eabi-gcc.exe')), None)
if not gcc:
    raise SystemExit('Supply --gcc PATH_TO_ARM_NONE_EABI_GCC')
gcc = Path(gcc)
out = ROOT / 'build'
out.mkdir(exist_ok=True)
flags = ['-mcpu=cortex-m4', '-mthumb', '-mfpu=fpv4-sp-d16', '-mfloat-abi=hard',
         '-DSTM32F411xE', '-DUSE_HAL_DRIVER', '-DHSE_VALUE=8000000U', '-Os', '-g3',
         '-ffunction-sections', '-fdata-sections', '-Wall', '-Wextra', '-std=c11']
for inc in ['firmware', 'vendor/hal/Inc', 'vendor/device/Include', 'vendor/core']:
    flags += ['-I', str(ROOT / inc)]
sources = [ROOT / '#define STM32F411xE.c', ROOT / 'vendor/device/system_stm32f4xx.c',
           ROOT / 'vendor/device/gcc/startup_stm32f411xe.s']
sources.append(ROOT / 'firmware/runtime.c')
sources += sorted((ROOT / 'vendor/hal/Src').glob('*.c'))
objects = []
for i, src in enumerate(sources):
    obj = out / f'{i}_{src.stem.replace("#", "").replace(" ", "_")}.o'
    subprocess.run([str(gcc), *flags, *(['-Werror'] if i == 0 else ['-Wno-unused-parameter']), '-c', str(src), '-o', str(obj)], check=True)
    objects.append(str(obj))
elf = out / 'stm32_tof_audio.elf'
subprocess.run([str(gcc), *flags, *objects, '-T', str(ROOT / 'firmware/STM32F411VE.ld'),
                '--specs=nano.specs', '--specs=nosys.specs', '-nostartfiles',
                '-Wl,--gc-sections', '-Wl,--no-warn-rwx-segments',
                '-Wl,-Map=' + str(out / 'stm32_tof_audio.map'), '-o', str(elf)], check=True)
suffix = gcc.suffix
for fmt, ext in [('binary', 'bin'), ('ihex', 'hex')]:
    subprocess.run([str(gcc.with_name('arm-none-eabi-objcopy' + suffix)), '-O', fmt,
                    str(elf), str(elf.with_suffix('.' + ext))], check=True)
subprocess.run([str(gcc.with_name('arm-none-eabi-size' + suffix)), str(elf)], check=True)
print('Built:', elf)
