# InScroll

Local browser-based hand gesture control studio.

## Features
- Live webcam preview inside a technical dashboard
- MediaPipe Hands tracking for left and right hands
- Skeleton-only mode for a lighter visual view
- Gesture detection: thumb up/down, fist, open palm, swipe up/down/left/right, pinch
- Bind gestures to keyboard actions
- Local Python backend triggers keyboard events

## Run
```bash
pip install -r requirements.txt
python main.py
```

Open: http://127.0.0.1:7860

## Notes
- Camera access works best on localhost.
- Keyboard execution uses `pyautogui`.
- On Windows, keep the browser and the Python app running on the same machine.
