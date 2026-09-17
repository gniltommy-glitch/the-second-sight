"""Call save_requested_frames(bridge, annotated_frame) inside your YOLO loop.

annotated_frame must be the exact BGR pixel array displayed by your application.
UART receiving and acknowledgements happen in Bridge's background thread.
"""
from pathlib import Path
import queue

def save_requested_frames(bridge, annotated_frame, directory='captures'):
    import cv2
    directory = Path(directory)
    saved = []
    while True:
        try:
            event = bridge.buttons.get_nowait()
        except queue.Empty:
            break
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"button_{event['count']}_{event['received_ns']}.png"
        if not cv2.imwrite(str(path), annotated_frame):
            raise OSError(f'Could not save requested image: {path}')
        saved.append(path)
    return saved
