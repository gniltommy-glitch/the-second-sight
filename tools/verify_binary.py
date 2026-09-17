"""Check actual linked vector entries and memory placement, without hardware."""
from pathlib import Path
import struct
import subprocess
ROOT=Path(__file__).resolve().parents[1]
gcc=next(Path('C:/ST').glob('**/arm-none-eabi-gcc.exe'))
nm=gcc.with_name('arm-none-eabi-nm.exe')
elf=ROOT/'build/stm32_tof_audio.elf'
lines=subprocess.check_output([str(nm),'-n',str(elf)],text=True).splitlines()
symbols={fields[2]:(int(fields[0],16),fields[1]) for line in lines
         if len(fields:=line.split())==3}
data=(ROOT/'build/stm32_tof_audio.bin').read_bytes()
checks={'Reset_Handler':1,'SysTick_Handler':15,'EXTI0_IRQHandler':16+6,
        'DMA1_Stream5_IRQHandler':16+16,'I2C2_EV_IRQHandler':16+33,
        'I2C2_ER_IRQHandler':16+34,'USART2_IRQHandler':16+38}
assert struct.unpack_from('<I',data)[0]==0x20020000
for name,index in checks.items():
    actual,=struct.unpack_from('<I',data,index*4)
    expected,kind=symbols[name]
    assert actual==expected|1,(name,hex(actual),hex(expected))
    if name!='Reset_Handler':
        assert kind=='T',(name,'weak handler linked')
    print(f'{name}: vector[{index}] = 0x{actual:08X} OK')
for name in ['pcm','dma_samples','rx_ring','tx_ring','tof_mm']:
    addr,_=symbols[name]
    assert 0x20000000<=addr<0x20020000
assert symbols['_ebss'][0]+4096<=0x20020000
print('RAM placement, initial stack and 4 KiB stack reserve: OK')
