import uvicorn
import sys
from pathlib import Path

# Ensure root directory is on python path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

def main():
    print("=" * 60)
    print(" Starting Teachable Voice Assistant Server")
    print(" UI with Siri Edge Glow available at: http://localhost:8000")
    print(" Download Android APK at: http://localhost:8000/download/voice-assistant.apk")
    print("=" * 60)
    uvicorn.run("voice_assistant_app.server:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
